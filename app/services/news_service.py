"""
News intelligence service for gathering and processing company intelligence.

Handles news collection from multiple sources, LLM summarization, and Notion integration.
"""

import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import feedparser
import httpx
import structlog
import tldextract
from httpx import TimeoutException

from app.core.config import IntelligenceConfig
from app.core.exceptions import ExternalServiceError, WorkflowError
from app.core.models import (
    Company,
    CompanyIntelligence,
    NewsItem,
    NewsItemSource,
    ProcessingResult,
    ProcessingStatus,
    WorkflowType,
)
from app.data.csv_parser import CSVProcessor, filter_dataframe_by_relationship_manager
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient
from app.intelligence.dossier_onboarding import DossierOnboardingService
from app.intelligence.news_quality import NewsQualityScorer
from app.intelligence.news_relevance import CompanyDossierBuilder, NewsRelevanceScorer
from app.intelligence.seen_store import NotionNewsSeenStore
from app.utils.reliability import ParallelProcessor, with_circuit_breaker, with_retry

logger = structlog.get_logger(__name__)

_CORPORATE_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "l.l.c.",
    "corp",
    "corp.",
    "co",
    "co.",
    "company",
    "ltd",
    "ltd.",
    "incorporated",
    "corporation",
}


class NewsCollector:
    """Collect news from multiple sources with reliability patterns."""

    def __init__(self, config: IntelligenceConfig):
        """Initialise collector with configuration and quality scorer."""
        self.config = config
        self.http_client = httpx.Client(timeout=25.0, follow_redirects=True)
        self.quality_scorer = NewsQualityScorer(config)

    def __del__(self):
        """Cleanup HTTP client."""
        if hasattr(self, "http_client"):
            self.http_client.close()

    def build_search_queries(
        self,
        company: Company,
        domain_root: Optional[str] = None,
        aka_names: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> List[str]:
        """
        Build search queries for a company.

        Args:
            company: Company object
            domain_root: Domain root override
            aka_names: Alternative names override
            tags: Industry tags override

        Returns:
            List of search query strings
        """

        def strip_suffix(name: str) -> str:
            tokens = [t for t in re.split(r"\s+", name.strip()) if t]
            while tokens:
                suffix = tokens[-1].rstrip(",.").lower()
                if suffix in _CORPORATE_SUFFIXES:
                    tokens.pop()
                    continue
                break
            return " ".join(tokens)

        raw_names: List[str] = []
        if company.callsign:
            raw_names.append(company.callsign)
        if company.dba:
            raw_names.append(company.dba)
        if company.callsign:
            raw_names.append(company.callsign.upper())

        alt_names = aka_names or company.aka_names
        if alt_names:
            raw_names.extend([n.strip() for n in alt_names.split(",") if n.strip()])

        names: List[str] = []
        seen_names = set()
        for name in raw_names:
            if not name:
                continue
            original = name.strip()
            if not original:
                continue
            orig_key = original.lower()
            if orig_key not in seen_names:
                seen_names.add(orig_key)
                names.append(original)

            cleaned = strip_suffix(original).strip()
            if cleaned and cleaned.lower() not in seen_names:
                seen_names.add(cleaned.lower())
                names.append(cleaned)

        if not names and company.callsign:
            names.append(company.callsign)

        domains: List[str] = []
        domain = domain_root or company.domain_root
        if domain:
            domains.append(domain.lower())

        if company.website:
            w = re.sub(r"^https?://", "", company.website.strip().lower())
            w = re.sub(r"^www\.", "", w).split("/")[0]
            ext = tldextract.extract(w)
            if ext.registered_domain:
                domains.append(ext.registered_domain.lower())

        domains = list({d for d in domains if d})
        site_scopes = self.quality_scorer.company_site_scopes(company)

        queries = self.quality_scorer.build_query_variants(company, domains, names, site_scopes)
        return queries

    @with_circuit_breaker(name="rss_feeds", failure_threshold=3, recovery_timeout=60.0)
    def collect_rss_feeds(self, site_url: Optional[str]) -> List[NewsItem]:
        """
        Collect news items from RSS feeds.

        Args:
            site_url: Website URL to check for RSS feeds

        Returns:
            List of NewsItem objects
        """
        if not site_url:
            return []

        site_url = str(site_url).strip()

        # Build candidate RSS URLs
        candidates = []
        if site_url.startswith("http"):
            base = site_url.rstrip("/")
            candidates = [f"{base}/feed", f"{base}/rss", base]
        else:
            candidates = [
                f"https://{site_url}/feed",
                f"https://{site_url}/rss",
                f"https://{site_url}",
            ]

        items = []

        for feed_url in candidates:
            try:
                logger.debug("Checking RSS feed", url=feed_url)

                # Use feedparser which handles many feed formats
                feed = feedparser.parse(feed_url)

                if not feed.entries:
                    continue

                for entry in feed.entries[:10]:  # Limit to recent entries
                    title = getattr(entry, "title", "") or ""
                    link = getattr(entry, "link", "") or ""

                    # Get publication date
                    date = ""
                    for date_field in ("published", "updated"):
                        if hasattr(entry, date_field):
                            date = getattr(entry, date_field) or ""
                            break

                    if title and link:
                        items.append(
                            NewsItem(
                                title=title[:500],  # Truncate long titles
                                url=link,
                                source=tldextract.extract(link).registered_domain or "RSS",
                                published_at=date,
                                source_type=NewsItemSource.RSS,
                            )
                        )

                logger.debug("RSS feed processed", url=feed_url, items_found=len(feed.entries))

                # If we found items, don't try other URLs
                if items:
                    break

            except Exception as e:
                logger.debug("RSS feed failed", url=feed_url, error=str(e))
                continue

        logger.info("RSS collection completed", site_url=site_url, items_found=len(items))
        return items

    @with_circuit_breaker(name="google_search", failure_threshold=5, recovery_timeout=60.0)
    @with_retry(max_attempts=2, retry_exceptions=(TimeoutException, ExternalServiceError))
    def collect_google_search(
        self,
        query: str,
        date_restrict: Optional[str] = None,
        num_results: int = 5,
        exclude_domains: Optional[List[str]] = None,
    ) -> List[NewsItem]:
        """
        Collect news items from Google Custom Search.

        Args:
            query: Search query
            date_restrict: Date restriction (e.g., 'd10' for last 10 days)
            num_results: Number of results to return

        Returns:
            List of NewsItem objects
        """
        if not self.config.google_api_key or not self.config.google_cse_id:
            logger.debug("Google Custom Search not configured")
            return []

        if self.config.cse_disable:
            logger.debug("Google Custom Search disabled by configuration")
            return []

        try:
            query_string = query
            if exclude_domains:
                exclusions = " ".join(f"-site:{d}" for d in exclude_domains if d)
                if exclusions:
                    query_string = f"{query} {exclusions}"

            params = {
                "key": self.config.google_api_key,
                "cx": self.config.google_cse_id,
                "q": query_string,
                "num": min(10, max(1, num_results)),
            }

            if date_restrict:
                params["dateRestrict"] = date_restrict

            logger.debug("Making Google Custom Search request", query=query)

            response = self.http_client.get(
                "https://www.googleapis.com/customsearch/v1", params=params
            )

            if not response.is_success:
                logger.warning(
                    "Google Custom Search API error",
                    status_code=response.status_code,
                    response_text=response.text[:500],
                )
                return []

            data = response.json()
            items = []

            for result in data.get("items", [])[:num_results]:
                link = result.get("link", "")
                title = result.get("title", "")
                snippet = result.get("snippet", "")

                # Try to extract publication date from metadata
                date = ""
                pagemap = result.get("pagemap", {})
                if "metatags" in pagemap and pagemap["metatags"]:
                    tags = pagemap["metatags"][0]
                    for date_key in (
                        "article:published_time",
                        "og:updated_time",
                        "date",
                    ):
                        if date_key in tags:
                            date = tags[date_key]
                            break

                if title and link:
                    items.append(
                        NewsItem(
                            title=title[:500],
                            url=link,
                            source=tldextract.extract(link).registered_domain or "Google",
                            published_at=date or snippet[:100],  # Fallback to snippet
                            source_type=NewsItemSource.GOOGLE_SEARCH,
                        )
                    )

            logger.info("Google search completed", query=query, items_found=len(items))
            return items

        except TimeoutException as e:
            logger.error("Google search timeout", query=query, error=str(e))
            raise ExternalServiceError("Google Custom Search", f"Request timeout: {e}")

        except Exception as e:
            logger.error("Google search failed", query=query, error=str(e))
            # Don't raise exception - just return empty results
            return []

    def filter_by_date_range(self, items: List[NewsItem], days: int) -> List[NewsItem]:
        """
        Filter news items by date range.

        Args:
            items: List of NewsItem objects
            days: Number of days to look back

        Returns:
            Filtered list of NewsItem objects
        """
        if not items:
            return []

        cutoff_date = datetime.now() - timedelta(days=days)
        filtered_items = []

        for item in items:
            if not item.published_at:
                # Include items without dates
                filtered_items.append(item)
                continue

            try:
                # Parse various date formats
                pub_date = item.published_at
                if isinstance(pub_date, str):
                    # Try to parse date string
                    pub_date = pub_date.replace("/", "-").replace(".", "-")
                    parts = [int(x) for x in pub_date.split("-") if x.isdigit()]
                    if len(parts) >= 3:
                        y, m, d = parts[:3]
                        pub_date = datetime(y, m, d)
                    else:
                        # Can't parse - include the item
                        filtered_items.append(item)
                        continue
                elif isinstance(pub_date, datetime):
                    pass  # Already a datetime
                else:
                    # Unknown format - include the item
                    filtered_items.append(item)
                    continue

                if pub_date >= cutoff_date:
                    filtered_items.append(item)

            except Exception as e:
                logger.debug("Date parsing failed", published_at=item.published_at, error=str(e))
                # Include items with unparseable dates
                filtered_items.append(item)

        logger.debug(
            "Date filtering completed",
            original_count=len(items),
            filtered_count=len(filtered_items),
            days=days,
        )

        return filtered_items

    def deduplicate_items(self, items: List[NewsItem]) -> List[NewsItem]:
        """
        Remove duplicate news items based on URL.

        Args:
            items: List of NewsItem objects

        Returns:
            Deduplicated list
        """
        seen_urls = set()
        unique_items = []

        for item in items:
            if not item.url or item.url in seen_urls:
                continue
            seen_urls.add(item.url)
            unique_items.append(item)

        logger.debug(
            "Deduplication completed",
            original_count=len(items),
            unique_count=len(unique_items),
        )

        return unique_items

    def collect_company_news(
        self, company: Company, enhanced_data: Optional[Dict[str, Any]] = None
    ) -> List[NewsItem]:
        """
        Collect news for a single company from all sources.

        Args:
            company: Company object
            enhanced_data: Enhanced company data (e.g., from Notion)

        Returns:
            List of NewsItem objects
        """
        all_items = []

        # Use enhanced data if available
        website = (enhanced_data or {}).get("website") or company.website
        domain_root = (enhanced_data or {}).get("domain") or company.domain_root
        blog_url = (enhanced_data or {}).get("blog_url") or company.blog_url

        if website and website != company.website:
            company.website = website
        if domain_root and domain_root != company.domain_root:
            company.domain_root = domain_root
        if blog_url and blog_url != company.blog_url:
            company.blog_url = blog_url

        # Collect from RSS feeds
        site_for_rss = blog_url or website
        if site_for_rss:
            try:
                rss_items = self.collect_rss_feeds(site_for_rss)
                all_items.extend(rss_items)
            except Exception as e:
                logger.warning("RSS collection failed", callsign=company.callsign, error=str(e))

        # Collect from Google Custom Search
        exclude_domains = self.quality_scorer.blocked_domains

        if not self.config.cse_disable and self.config.google_api_key and self.config.google_cse_id:
            try:
                queries = self.build_search_queries(company, domain_root)
                max_queries = self.config.cse_max_queries_per_org

                for query in queries[:max_queries]:
                    try:
                        search_items = self.collect_google_search(
                            query,
                            date_restrict=f"d{self.config.lookback_days}",
                            num_results=5,
                            exclude_domains=exclude_domains,
                        )
                        all_items.extend(search_items)
                    except Exception as e:
                        logger.warning("Google search query failed", query=query, error=str(e))
                        continue

            except Exception as e:
                logger.warning(
                    "Google search collection failed",
                    callsign=company.callsign,
                    error=str(e),
                )

        # Deduplicate and filter
        all_items = self.deduplicate_items(all_items)
        all_items = self.filter_by_date_range(all_items, self.config.lookback_days)

        ranked_items = self.quality_scorer.rank_items(company, all_items, self.config.max_per_org)

        for item in ranked_items:
            item.callsign = company.callsign

        logger.info(
            "Company news collection completed",
            callsign=company.callsign,
            items_collected=len(ranked_items),
            sources_checked=(["rss", "google_search"] if not self.config.cse_disable else ["rss"]),
        )

        return ranked_items


class NewsService:
    """Coordinate the end-to-end news intelligence workflow."""

    def __init__(
        self,
        gmail_client: EnhancedGmailClient,
        notion_client: Optional[EnhancedNotionClient],
        config: IntelligenceConfig,
    ):
        """Create the service with required dependencies."""
        self.gmail_client = gmail_client
        self.notion_client = notion_client
        self.config = config
        notion_companies_db = (
            getattr(notion_client.config, "companies_db_id", None) if notion_client else None
        )
        notion_intel_db = (
            getattr(notion_client.config, "intel_db_id", None) if notion_client else None
        )
        self.companies_db_id = getattr(config, "companies_db_id", None) or notion_companies_db
        self.intel_db_id = getattr(config, "intel_db_id", None) or notion_intel_db
        self.collector = NewsCollector(config)
        self.dossier_service = DossierOnboardingService(
            notion_client,
            self.companies_db_id,
        )
        self.dossier_builder = CompanyDossierBuilder(notion_client)
        self.relevance_scorer = NewsRelevanceScorer(config)
        self.news_store: Optional[NotionNewsSeenStore] = None
        if notion_client and self.intel_db_id:
            try:
                self.news_store = NotionNewsSeenStore(
                    notion_client,
                    self.intel_db_id,
                    self.companies_db_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to initialize Notion news store",
                    error=str(exc),
                    intel_db_id=self.intel_db_id,
                )

    def fetch_companies_data(self, subject_filter: Optional[str] = None) -> List[Company]:
        """
        Fetch companies data from Gmail CSV.

        Args:
            subject_filter: Subject filter for Gmail search

        Returns:
            List of Company objects
        """
        try:
            subject = subject_filter or self.config.news_profile_subject

            relationship_manager = os.getenv("RELATIONSHIP_MANAGER_NAME", "Will Mitchell")

            # Search for CSV with company profiles
            messages = self.gmail_client.search_messages(
                query=f'subject:"{subject}" has:attachment filename:csv', max_results=5
            )

            if not messages:
                raise WorkflowError(f"No profile CSV found with subject: {subject}")

            # Get the first message with CSV
            for msg_info in messages:
                try:
                    message = self.gmail_client.get_message(msg_info["id"])
                    attachments = self.gmail_client.extract_csv_attachments(message)

                    if attachments:
                        filename, data = attachments[0]
                        df = self.gmail_client.parse_csv_attachment(data)
                        df = filter_dataframe_by_relationship_manager(df, relationship_manager)
                        if df.empty:
                            logger.warning(
                                "CSV attachment empty after relationship manager filter",
                                message_id=msg_info["id"],
                                relationship_manager=relationship_manager,
                            )
                            continue

                        # Parse into Company objects
                        processor = CSVProcessor(strict_validation=False)
                        companies = processor.parse_companies_csv(df)

                        logger.info(
                            "Companies data fetched",
                            companies_count=len(companies),
                            filename=filename,
                        )

                        return companies

                except Exception as e:
                    logger.warning(
                        "Failed to process message",
                        message_id=msg_info["id"],
                        error=str(e),
                    )
                    continue

            raise WorkflowError("No valid CSV attachments found in messages")

        except Exception as e:
            if isinstance(e, WorkflowError):
                raise

            error_msg = f"Failed to fetch companies data: {e}"
            logger.error("Companies data fetch failed", error=str(e))
            raise WorkflowError(error_msg)

    def enhance_companies_with_notion_data(
        self, companies: List[Company]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Enhance company data with Notion domain information.

        Args:
            companies: List of Company objects

        Returns:
            Dict mapping callsign to enhanced data
        """
        if not self.notion_client or not self.companies_db_id:
            logger.debug("Notion enhancement skipped - no client or DB ID")
            return {}

        try:
            callsigns = [c.callsign for c in companies]
            notion_data = self.notion_client.get_all_companies_domain_data(
                self.companies_db_id, callsigns
            )

            logger.info(
                "Companies enhanced with Notion data",
                requested=len(callsigns),
                found=len([d for d in notion_data.values() if d.get("domain") or d.get("website")]),
            )

            return notion_data

        except Exception as e:
            logger.error("Failed to enhance companies with Notion data", error=str(e))
            return {}

    def _ensure_dossiers_for_new_companies(
        self,
        companies: List[Company],
        enhanced_data: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Generate dossiers for companies missing Notion coverage."""
        if not enhanced_data:
            enhanced_data = {}
        if not self.dossier_service or not self.dossier_service.is_available():
            return enhanced_data

        updates = 0
        for company in companies:
            key = (company.callsign or "").lower()
            if not key:
                continue
            entry = enhanced_data.get(key)
            is_new = not (entry and entry.get("page_id"))
            needs_dossier = bool(entry and entry.get("needs_dossier"))

            if not (is_new or needs_dossier):
                continue

            updated_entry = self.dossier_service.generate_dossier(company, entry)
            if updated_entry:
                enhanced_data[key] = {**(entry or {}), **updated_entry}
                updates += 1

        if updates:
            logger.info(
                "dossiers_generated_for_new_companies",
                total=updates,
            )
        return enhanced_data

    def summarize_intelligence(self, items: List[NewsItem]) -> Optional[str]:
        """
        Generate AI summary of news items.

        Args:
            items: List of NewsItem objects

        Returns:
            Generated summary or None if unavailable
        """
        if not self.config.openai_api_key or not items:
            return None

        try:
            import openai

            # Prepare text for summarization
            text_parts = []
            for item in items:
                date_str = item.published_at or "Recent"
                text_parts.append(f"{date_str} — {item.title} — {item.source} {item.url}")

            text = "\n".join(text_parts)

            prompt = (
                "Summarize the following items into a crisp 2–3 sentence weekly intel highlight. "
                "Keep dates and sources implicit; focus on what happened and why it matters:\n\n"
                + text
            )

            client = openai.OpenAI(api_key=self.config.openai_api_key)

            def extract_output_text(resp):
                text = (getattr(resp, "output_text", None) or "").strip()
                if text:
                    return text

                segments = []
                for item in getattr(resp, "output", []) or []:
                    content = getattr(item, "content", None)
                    if content is None and isinstance(item, dict):
                        content = item.get("content")
                    if not content:
                        continue
                    for chunk in content:
                        value = None
                        if hasattr(chunk, "text"):
                            value = getattr(chunk.text, "value", None) or getattr(
                                chunk.text, "text", None
                            )
                        elif isinstance(chunk, dict):
                            value = chunk.get("text")
                            if isinstance(value, dict):
                                value = value.get("value") or value.get("text")
                        if value:
                            segments.append(str(value).strip())
                return " ".join(segment for segment in segments if segment).strip()

            # Try with temperature first, then without if it fails
            def try_completion(with_temperature: bool = True):
                kwargs = {
                    "model": self.config.openai_model,
                    "input": prompt,
                    "max_output_tokens": 512,
                    "reasoning": {"effort": "medium"},
                    "text": {"format": {"type": "text"}},
                }

                if with_temperature and self.config.openai_temperature is not None:
                    kwargs["temperature"] = self.config.openai_temperature

                response = client.responses.create(**kwargs)
                return extract_output_text(response)

            try:
                summary = try_completion(with_temperature=True)
            except Exception:
                summary = try_completion(with_temperature=False)

            summary = (summary or "").strip()

            logger.info(
                "Intelligence summary generated",
                items_count=len(items),
                summary_length=len(summary),
            )

            return summary

        except Exception as e:
            logger.error("Failed to generate summary", error=str(e))
            return None

    def process_single_company(
        self, company: Company, enhanced_data: Optional[Dict[str, Any]] = None
    ) -> CompanyIntelligence:
        """
        Process intelligence for a single company.

        Args:
            company: Company object
            enhanced_data: Enhanced company data from Notion

        Returns:
            CompanyIntelligence object
        """
        try:
            logger.debug("Processing intelligence", callsign=company.callsign)

            # Collect news items
            news_items = self.collector.collect_company_news(company, enhanced_data)

            # Apply relevance filtering scaffold
            dossier_snapshot = self.dossier_builder.build(company, enhanced_data)
            news_items, rejected_items = self.relevance_scorer.filter_items(
                news_items,
                dossier_snapshot,
            )

            if rejected_items:
                logger.debug(
                    "news_items_rejected",
                    callsign=company.callsign,
                    rejected=len(rejected_items),
                    snapshot_id=(dossier_snapshot.snapshot_id if dossier_snapshot else None),
                )

            # Generate summary
            summary = self.summarize_intelligence(news_items) or f"{len(news_items)} new items."

            # Create intelligence object
            intelligence = CompanyIntelligence(
                callsign=company.callsign,
                news_items=news_items,
                summary=summary,
                processing_status=ProcessingStatus.COMPLETED,
            )

            logger.info(
                "Company intelligence processed",
                callsign=company.callsign,
                news_items=len(news_items),
                summary_length=len(summary),
            )

            return intelligence

        except Exception as e:
            error_msg = f"Failed to process intelligence for {company.callsign}: {e}"
            logger.error(
                "Company intelligence processing failed",
                callsign=company.callsign,
                error=str(e),
            )

            return CompanyIntelligence(
                callsign=company.callsign,
                processing_status=ProcessingStatus.FAILED,
                error_message=error_msg,
            )

    def run_intelligence_workflow(
        self, filter_callsigns: Optional[List[str]] = None
    ) -> ProcessingResult:
        """
        Run the complete intelligence workflow.

        Args:
            filter_callsigns: Optional list to filter companies

        Returns:
            ProcessingResult with workflow outcome
        """
        result = ProcessingResult(workflow_type=WorkflowType.NEWS, started_at=datetime.now())

        try:
            logger.info("Starting intelligence workflow")

            # Step 1: Fetch companies data
            companies = self.fetch_companies_data()

            # Apply filters
            filter_list = filter_callsigns or self.config.filter_callsigns
            if filter_list:
                companies = [c for c in companies if c.callsign.lower() in filter_list]
                logger.info("Applied callsign filter", filtered_count=len(companies))

            result.items_processed = len(companies)

            # Step 2: Enhance with Notion data
            enhanced_data = self.enhance_companies_with_notion_data(companies)
            enhanced_data = self._ensure_dossiers_for_new_companies(companies, enhanced_data)

            # Step 3: Process companies in parallel
            def process_company_wrapper(company: Company) -> CompanyIntelligence:
                return self.process_single_company(
                    company, enhanced_data.get(company.callsign.lower())
                )

            processor = ParallelProcessor(max_workers=6)  # Conservative for API limits
            batch_results = processor.process_batch(companies, process_company_wrapper, timeout=300)

            # Collect results
            intelligence_by_company = {}
            successful = 0
            failed = 0

            for company, intel in batch_results:
                if intel and intel.processing_status == ProcessingStatus.COMPLETED:
                    intelligence_by_company[company.callsign] = intel
                    successful += 1
                else:
                    failed += 1

            # Step 4: Update Notion (if configured)
            if self.notion_client and self.companies_db_id and self.intel_db_id:
                self._update_notion_intelligence(intelligence_by_company)

            # Step 5: Send digest (if configured)
            digest_to = getattr(self.config, "digest_to", None)
            if not self.config.preview_only and digest_to:
                self._send_intelligence_digest(intelligence_by_company)

            # Complete result
            result.status = ProcessingStatus.COMPLETED
            result.completed_at = datetime.now()
            result.items_successful = successful
            result.items_failed = failed
            result.data = {
                "companies_processed": len(companies),
                "intelligence_items": sum(
                    len(intel.news_items) for intel in intelligence_by_company.values()
                ),
                "enhanced_companies": len(
                    [d for d in enhanced_data.values() if d.get("domain") or d.get("website")]
                ),
            }

            if result.started_at:
                duration = (result.completed_at - result.started_at).total_seconds()
                result.duration_seconds = duration

            logger.info(
                "Intelligence workflow completed",
                companies_processed=len(companies),
                successful=successful,
                failed=failed,
                duration_seconds=result.duration_seconds,
            )

            return result

        except Exception as e:
            # Update result with error
            result.status = ProcessingStatus.FAILED
            result.completed_at = datetime.now()
            result.error_message = str(e)
            result.error_details = {"error_type": type(e).__name__}

            if result.started_at:
                duration = (result.completed_at - result.started_at).total_seconds()
                result.duration_seconds = duration

            logger.error(
                "Intelligence workflow failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_seconds=result.duration_seconds,
            )

            return result

    def _update_notion_intelligence(
        self, intelligence_by_company: Dict[str, CompanyIntelligence]
    ) -> None:
        """Update Notion with intelligence data."""
        if self.config.preview_only:
            logger.info("Notion update skipped in preview mode")
            return

        if not self.notion_client or not self.news_store:
            logger.info("Notion update skipped - client or news store unavailable")
            return

        for callsign, intel in intelligence_by_company.items():
            items = intel.news_items or []
            try:
                new_items, existing_items = self.news_store.ingest(callsign, None, items)
                logger.info(
                    "Notion news updated",
                    callsign=callsign,
                    new_items=len(new_items),
                    existing_items=len(existing_items),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to write news items to Notion",
                    callsign=callsign,
                    error=str(exc),
                )

            summary_text = (intel.summary or "").strip()
            if not summary_text:
                continue

            if not self.companies_db_id:
                logger.debug(
                    "Skipping summary update - companies DB ID missing",
                    callsign=callsign,
                )
                continue

            try:
                page_id = self.notion_client.find_company_page(self.companies_db_id, callsign)
                if not page_id:
                    logger.debug(
                        "Skipping summary update - company page not found",
                        callsign=callsign,
                    )
                    continue

                date_iso = datetime.utcnow().date().isoformat()
                self.notion_client.set_latest_intel(
                    page_id,
                    summary_text,
                    date_iso=date_iso,
                    database_id=self.companies_db_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to update latest intel summary",
                    callsign=callsign,
                    error=str(exc),
                )

    def _send_intelligence_digest(
        self, intelligence_by_company: Dict[str, CompanyIntelligence]
    ) -> None:
        """Send intelligence digest email."""
        # Implementation would go here
        logger.info("Intelligence digest email would be sent here")


def create_news_service(
    gmail_client: EnhancedGmailClient,
    notion_client: Optional[EnhancedNotionClient],
    config: IntelligenceConfig,
) -> NewsService:
    """Create a `NewsService` configured with required dependencies."""
    return NewsService(gmail_client, notion_client, config)
