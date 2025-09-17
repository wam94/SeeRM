"""
Data aggregation layer for SeeRM intelligence system.

Provides unified access to CSV data, Notion intelligence, and other sources
for report generation and analysis.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import structlog

from app.core.config import Settings
from app.core.exceptions import WorkflowError
from app.data.csv_parser import CSVProcessor, filter_dataframe_by_relationship_manager
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient
from app.utils.reliability import track_performance, with_retry

from .cache import cache_company_profile, cache_movements
from .models import CompanyIntelligence, CompanyProfile, Movement, MovementType, NewsItem, NewsType
from .news_classifier import NewsClassifier
from .parallel_processor import get_parallel_processor
from .seen_store import NotionNewsSeenStore

logger = structlog.get_logger(__name__)


class IntelligenceAggregator:
    """
    Unified data aggregation layer for intelligence reports.

    Combines data from CSV files, Notion databases, Gmail, and other sources
    to provide comprehensive company intelligence.
    """

    def __init__(
        self,
        gmail_client: Optional[EnhancedGmailClient] = None,
        notion_client: Optional[EnhancedNotionClient] = None,
        settings: Optional[Settings] = None,
    ):
        """Create the aggregator and prepare shared services."""
        self.gmail_client = gmail_client
        self.notion_client = notion_client
        self.settings = settings or Settings()
        self.csv_processor = CSVProcessor(strict_validation=False)
        self.news_classifier = NewsClassifier(self.settings)

        self.news_store = None
        if self.notion_client and self.settings.notion.intel_db_id:
            try:
                self.news_store = NotionNewsSeenStore(
                    self.notion_client,
                    self.settings.notion.intel_db_id,
                    self.settings.notion.companies_db_id,
                )
            except Exception as exc:
                logger.warning("Failed to initialize Notion news store", error=str(exc))

        logger.info(
            "Intelligence aggregator initialized",
            notion_enabled=notion_client is not None,
            intelligent_classification=True,
        )

    def _get_notion_company_data(self, callsigns: List[str]) -> Optional[Dict[str, Dict[str, Any]]]:
        """Fetch Notion company metadata for supplied callsigns."""
        if not self.notion_client or not self.settings.notion.companies_db_id:
            logger.debug(
                "Notion client not configured; new-account detection will rely on CSV flags"
            )
            return None

        filtered_callsigns = [cs for cs in callsigns if cs]
        if not filtered_callsigns:
            return {}

        try:
            notion_data = self.notion_client.get_all_companies_domain_data(
                self.settings.notion.companies_db_id,
                filtered_callsigns,
            )
            logger.debug(
                "Fetched Notion metadata for new-account detection",
                requested=len(filtered_callsigns),
                received=len(notion_data or {}),
            )
            return notion_data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch Notion company metadata; falling back to CSV new-account flags",
                error=str(exc),
                companies=len(filtered_callsigns),
            )
            return None

    @track_performance("get_latest_movements")
    @with_retry(max_attempts=3)
    @cache_movements(ttl=300)  # Cache for 5 minutes
    def get_latest_movements(self, days: int = 7) -> List[Movement]:
        """
        Get latest account movements from CSV data.

        Args:
            days: Number of days to look back for data

        Returns:
            List of Movement objects with current and historical data
        """
        try:
            logger.info("Fetching latest movements", days=days)

            # Get CSV data from Gmail if available, otherwise use csv_source_path
            manager_name = self.settings.relationship_manager_name

            if self.gmail_client:
                df = self.gmail_client.get_latest_csv_from_query(max_messages=5)
                if df is None:
                    logger.warning("No CSV data found in Gmail")
                    return []
                df = filter_dataframe_by_relationship_manager(df, manager_name)
                if df.empty:
                    logger.warning(
                        "CSV data empty after relationship manager filter",
                        relationship_manager=manager_name,
                    )
                    return []
            elif self.settings.csv_source_path:
                import pandas as pd

                df = pd.read_csv(self.settings.csv_source_path)
                logger.info("Loaded CSV from file path", path=self.settings.csv_source_path)
                df = filter_dataframe_by_relationship_manager(df, manager_name)
                if df.empty:
                    logger.warning(
                        "CSV file empty after relationship manager filter",
                        relationship_manager=manager_name,
                        path=self.settings.csv_source_path,
                    )
                    return []
            else:
                logger.warning(
                    "No CSV data source configured (no Gmail client and no csv_source_path)"
                )
                return []

            # Parse into Company objects
            companies = self.csv_processor.parse_companies_csv(df)

            callsigns = [company.callsign for company in companies if company.callsign]
            notion_company_data = self._get_notion_company_data(callsigns)

            # Convert to Movement objects
            movements = []
            for company in companies:
                notion_entry = (
                    notion_company_data.get(company.callsign.lower())
                    if notion_company_data is not None
                    else None
                )
                page_exists = bool(notion_entry and notion_entry.get("page_id"))
                is_new_account = (
                    not page_exists
                    if notion_company_data is not None
                    else bool(company.is_new_account)
                )

                movement = Movement(
                    callsign=company.callsign,
                    company_name=company.dba or company.callsign,
                    current_balance=company.curr_balance or 0,
                    percentage_change=company.balance_pct_delta_pct,
                    rank=getattr(company, "rank", None),
                    is_new_account=is_new_account,
                    products=getattr(company, "products", []),
                )

                # Determine movement type
                if movement.is_new_account:
                    movement.movement_type = MovementType.NEW_ACCOUNT
                elif movement.percentage_change is not None:
                    if movement.percentage_change > 30:
                        movement.movement_type = MovementType.TOP_GAINER
                    elif movement.percentage_change < -30:
                        movement.movement_type = MovementType.TOP_LOSER
                    elif abs(movement.percentage_change) > 10:
                        movement.movement_type = MovementType.SIGNIFICANT_CHANGE
                    else:
                        movement.movement_type = MovementType.STABLE

                movements.append(movement)

            logger.info(
                "Movements processed",
                total=len(movements),
                new_accounts=len([m for m in movements if m.is_new_account]),
                significant_changes=len(
                    [
                        m
                        for m in movements
                        if m.movement_type
                        in [
                            MovementType.TOP_GAINER,
                            MovementType.TOP_LOSER,
                            MovementType.SIGNIFICANT_CHANGE,
                        ]
                    ]
                ),
            )

            return movements

        except Exception as e:
            logger.error("Failed to get latest movements", error=str(e))
            raise WorkflowError(f"Failed to fetch movement data: {e}")

    @track_performance("get_company_profile")
    @with_retry(max_attempts=3)
    @cache_company_profile(ttl=3600)  # Cache for 1 hour
    def get_company_profile(self, callsign: str) -> Optional[CompanyProfile]:
        """
        Get company profile from Notion.

        Args:
            callsign: Company callsign to lookup

        Returns:
            CompanyProfile object or None if not found
        """
        if not self.notion_client or not self.settings.notion.companies_db_id:
            logger.debug("Notion not configured, skipping profile lookup")
            return None

        try:
            logger.debug("Fetching company profile", callsign=callsign)

            # Get company page from Notion
            companies_data = self.notion_client.get_all_companies_domain_data(
                self.settings.notion.companies_db_id, callsigns=[callsign]
            )

            key = callsign.lower()
            if not companies_data or key not in companies_data:
                logger.debug("Company not found in Notion", callsign=callsign)
                return None

            company_data = companies_data[key]

            profile = CompanyProfile(
                callsign=callsign,
                company_name=company_data.get("company_name", callsign),
                website=company_data.get("website"),
                domain=company_data.get("domain"),
                owners=company_data.get("owners", []),
                tags=company_data.get("tags", []),
                products=company_data.get("products", []),
                needs_dossier=company_data.get("needs_dossier", False),
                notion_page_id=company_data.get("page_id"),
            )

            logger.debug("Company profile loaded", callsign=callsign)
            return profile

        except Exception as e:
            logger.warning("Failed to get company profile", callsign=callsign, error=str(e))
            return None

    @track_performance("get_company_news")
    @with_retry(max_attempts=3)
    def get_company_news(self, callsign: str, days: int = 90) -> List[NewsItem]:
        """
        Get news for a company from the "Latest Intel" field in Companies database.

        Args:
            callsign: Company callsign
            days: Number of days of history to retrieve (used for filtering by Latest Intel At)

        Returns:
            List of NewsItem objects
        """
        if self.news_store:
            items = self.news_store.get_recent(callsign, days)
            return items

        if not self.notion_client or not self.settings.notion.companies_db_id:
            logger.debug("Notion companies DB not configured")
            return []

        try:
            logger.debug("Fetching company news", callsign=callsign, days=days)

            # Get company data from Companies database including latest intel
            companies_data = self.notion_client.get_all_companies_domain_data(
                self.settings.notion.companies_db_id, callsigns=[callsign]
            )

            if not companies_data or callsign.lower() not in companies_data:
                logger.debug("Company not found", callsign=callsign)
                return []

            company_data = companies_data[callsign.lower()]
            latest_intel = company_data.get("latest_intel")
            latest_intel_at = company_data.get("latest_intel_at")

            news_items = []

            # Check if there's actual intel content (not "0 new items")
            if latest_intel and latest_intel.strip() and not latest_intel.startswith("0 new items"):
                # Calculate if the intel is within our date range
                intel_date = datetime.now()
                if latest_intel_at:
                    try:
                        intel_date = datetime.fromisoformat(latest_intel_at.replace("Z", "+00:00"))
                    except ValueError:
                        logger.debug("Could not parse intel date", date=latest_intel_at)

                # Check if intel is within the requested time range
                cutoff_date = datetime.now() - timedelta(days=days)
                if intel_date >= cutoff_date:
                    # Create a NewsItem from the latest intel
                    news_item = NewsItem(
                        title=f"Latest Intel: {callsign.upper()}",
                        url="",
                        source="Notion Companies DB",
                        published_at=(
                            intel_date.isoformat() if intel_date else datetime.now().isoformat()
                        ),
                        summary=latest_intel,
                        news_type=NewsType.OTHER_NOTABLE,  # Classified later
                        relevance_score=0.8,  # High relevance since it's the latest intel
                        sentiment="neutral",  # Default to neutral
                        company_mentions=[callsign.upper()],
                    )
                    news_items.append(news_item)

            logger.debug("Company news loaded", callsign=callsign, count=len(news_items))
            return news_items

        except Exception as e:
            logger.warning("Failed to get company news", callsign=callsign, error=str(e))
            return []

    @track_performance("get_company_360")
    def get_company_360(self, callsign: str) -> CompanyIntelligence:
        """
        Get complete intelligence profile for a company.

        Args:
            callsign: Company callsign

        Returns:
            CompanyIntelligence object with all available data
        """
        logger.info("Building 360-degree company intelligence", callsign=callsign)

        # Get company profile
        profile = self.get_company_profile(callsign)
        if not profile:
            # Create basic profile from callsign
            profile = CompanyProfile(callsign=callsign, company_name=callsign)

        # Get movement data
        movements = self.get_latest_movements()
        movement = next((m for m in movements if m.callsign == callsign), None)

        # Get news history
        news_history = self.get_company_news(callsign, days=90)

        # Get latest intel
        latest_intel, last_intel_date = self._get_latest_intel(callsign)

        intelligence = CompanyIntelligence(
            profile=profile,
            movement=movement,
            news_history=news_history,
            latest_intel=latest_intel,
            last_intel_date=last_intel_date,
        )

        logger.info(
            "Company intelligence compiled",
            callsign=callsign,
            news_items=len(news_history),
        )

        return intelligence

    @track_performance("get_portfolio_movements")
    def get_portfolio_movements(self, threshold: float = 10.0) -> Dict[str, List[Movement]]:
        """
        Get significant portfolio movements categorized by type.

        Args:
            threshold: Minimum percentage change to be considered significant

        Returns:
            Dict categorizing movements by type
        """
        movements = self.get_latest_movements()

        categorized = {
            "top_gainers": [],
            "top_losers": [],
            "new_accounts": [],
            "significant_changes": [],
        }

        for movement in movements:
            if movement.movement_type == MovementType.NEW_ACCOUNT:
                categorized["new_accounts"].append(movement)
            elif movement.movement_type == MovementType.TOP_GAINER:
                categorized["top_gainers"].append(movement)
            elif movement.movement_type == MovementType.TOP_LOSER:
                categorized["top_losers"].append(movement)
            elif movement.movement_type == MovementType.SIGNIFICANT_CHANGE:
                categorized["significant_changes"].append(movement)

        return categorized

    @track_performance("get_news_stream")
    def get_news_stream(self, days: int = 7) -> List[NewsItem]:
        """
        Get all news across the portfolio for a time period.

        Args:
            days: Number of days to look back

        Returns:
            List of all NewsItem objects across portfolio
        """
        # Get all companies from movements
        movements = self.get_latest_movements()

        # Use parallel processing to fetch news for all companies
        processor = get_parallel_processor(max_workers=min(10, len(movements)))
        callsigns = [movement.callsign for movement in movements]

        # Fetch news in parallel
        news_by_company = processor.parallel_fetch_news(
            companies=callsigns, fetch_news_func=self.get_company_news, days=days
        )

        # Flatten news items
        all_news = []
        for company_news in news_by_company.values():
            all_news.extend(company_news)

        # Classify news items using intelligent categorization
        if all_news:
            # Batch news items for parallel classification
            batch_size = 20
            news_batches = [
                all_news[i : i + batch_size] for i in range(0, len(all_news), batch_size)
            ]

            # Classify in parallel batches
            all_news = processor.batch_classify_news(
                news_items_batches=news_batches,
                classify_func=self.news_classifier.classify_news_items,
            )

        # Sort by date (most recent first)
        all_news.sort(key=lambda x: x.published_at, reverse=True)

        logger.info(
            "News stream compiled and classified",
            total_items=len(all_news),
            days=days,
            companies=len(movements),
        )

        return all_news

    def get_companies_by_category(self, news_items: List[NewsItem]) -> Dict[NewsType, List[str]]:
        """
        Group companies by news category for digest generation.

        Args:
            news_items: List of classified news items

        Returns:
            Dict mapping news categories to lists of company callsigns
        """
        companies_by_category = {}

        for item in news_items:
            category = item.news_type
            companies = item.company_mentions

            if category not in companies_by_category:
                companies_by_category[category] = set()

            companies_by_category[category].update(companies)

        # Convert sets to sorted lists
        result = {}
        for category, company_set in companies_by_category.items():
            result[category] = sorted(list(company_set))

        logger.debug(
            "Companies grouped by category",
            categories=len(result),
            total_companies=sum(len(companies) for companies in result.values()),
        )

        return result

    def _classify_news_type(self, title: str) -> NewsType:
        """Classify news type based on title keywords (legacy fallback)."""
        title_lower = title.lower()

        if any(
            word in title_lower for word in ["funding", "raise", "series", "investment", "investor"]
        ):
            return NewsType.FUNDING
        elif any(
            word in title_lower for word in ["partnership", "partner", "collaboration", "alliance"]
        ):
            return NewsType.PARTNERSHIPS
        elif any(word in title_lower for word in ["launch", "release", "product", "feature"]):
            return NewsType.PRODUCT_LAUNCH
        elif any(
            word in title_lower
            for word in ["ceo", "cto", "founder", "executive", "hire", "appointment"]
        ):
            return NewsType.LEADERSHIP
        elif any(word in title_lower for word in ["acquisition", "acquire", "merger", "bought"]):
            return NewsType.ACQUISITION
        elif any(word in title_lower for word in ["revenue", "growth", "milestone", "users"]):
            return NewsType.GROWTH_METRICS
        elif any(word in title_lower for word in ["legal", "regulatory", "compliance", "lawsuit"]):
            return NewsType.LEGAL_REGULATORY
        elif any(
            word in title_lower for word in ["technical", "outage", "security", "infrastructure"]
        ):
            return NewsType.TECHNICAL
        else:
            return NewsType.OTHER_NOTABLE

    def _get_latest_intel(self, callsign: str) -> tuple[Optional[str], Optional[datetime]]:
        """Get the latest intelligence summary for a company."""
        news = self.get_company_news(callsign, days=30)
        if not news:
            return None, None

        latest = news[0]  # Already sorted by date
        try:
            latest_date = datetime.fromisoformat(latest.published_at.replace("Z", "+00:00"))
        except Exception:
            latest_date = None

        return latest.summary or latest.title, latest_date
