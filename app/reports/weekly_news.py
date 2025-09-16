"""
Weekly News Digest Report Generator.

Creates weekly bulletized news summaries categorized by type
and organized by company for portfolio intelligence.
"""

import html
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import structlog

from app.core.config import Settings
from app.data.email_delivery import create_robust_email_delivery
from app.data.notion_client import EnhancedNotionClient
from app.intelligence.analyzers import NewsAnalyzer
from app.intelligence.data_aggregator import IntelligenceAggregator
from app.intelligence.models import NewsType, Report, ReportMetadata, WeeklyNewsDigest
from app.utils.reliability import track_performance

logger = structlog.get_logger(__name__)


class WeeklyNewsReport:
    """
    Generates weekly news digest reports.

    Provides categorized news summaries, theme analysis,
    and notable item highlighting across the portfolio.
    """

    def __init__(
        self,
        aggregator: IntelligenceAggregator,
        notion_client: Optional[EnhancedNotionClient] = None,
        settings: Optional[Settings] = None,
    ):
        """Initialize the weekly news report generator."""
        self.aggregator = aggregator
        self.notion_client = notion_client
        self.settings = settings or Settings()
        self.news_analyzer = NewsAnalyzer()

        logger.info("Weekly news report generator initialized")

    @track_performance("generate_weekly_news_report")
    def generate(self, days: int = 7, include_email: bool = True) -> Optional[Report]:
        """
        Generate weekly news digest report.

        Args:
            days: Number of days to look back for news
            include_email: Whether to send email with report

        Returns:
            Report object or None if no news
        """
        start_time = datetime.utcnow()
        week_of = (start_time - timedelta(days=days)).strftime("%Y-%m-%d")
        report_id = f"weekly_news_{week_of}_{start_time.strftime('%H%M%S')}"

        logger.info(
            "Generating weekly news report",
            days=days,
            week_of=week_of,
            report_id=report_id,
        )

        try:
            # Step 1: Gather news data
            news_items = self.aggregator.get_news_stream(days=days)

            if not news_items:
                logger.info("No news items found for this period")
                return None

            logger.info("Processing news items", count=len(news_items))

            # Step 2: Generate news digest
            digest = self.news_analyzer.generate_weekly_digest(news_items, week_of)

            # Step 3: Create report content
            content = self._create_report_content(digest)
            html = self._render_html_report(digest)
            markdown = self._render_markdown_report(digest)

            # Step 4: Create report metadata
            metadata = ReportMetadata(
                report_id=report_id,
                report_type="weekly_news_digest",
                generated_at=start_time,
                data_sources=["notion_intel"],
                parameters={"days": days, "week_of": week_of},
                duration_seconds=(datetime.utcnow() - start_time).total_seconds(),
            )

            # Step 5: Create final report
            report = Report(
                metadata=metadata,
                title=f"Weekly News Digest - Week of {week_of}",
                content=content,
                html=html,
                markdown=markdown,
            )

            # Step 6: Deliver report
            if include_email:
                self._send_email_report(report, digest)

            if self.notion_client:
                report.notion_page_id = self._create_notion_report(report, digest)

            logger.info(
                "Weekly news report completed",
                news_items=len(news_items),
                report_id=report_id,
                duration=metadata.duration_seconds,
            )

            return report

        except Exception as e:
            logger.error("Failed to generate weekly news report", error=str(e))
            raise

    def _create_report_content(self, digest: WeeklyNewsDigest) -> Dict[str, Any]:
        """Create structured report content."""
        # Count by type for summary
        type_counts = {news_type.value: len(items) for news_type, items in digest.by_type.items()}

        # Most active companies
        company_activity = [(company, len(items)) for company, items in digest.by_company.items()]
        company_activity.sort(key=lambda x: x[1], reverse=True)

        return {
            "week_of": digest.week_of,
            "summary_stats": {
                "total_items": digest.total_items,
                "unique_companies": len(digest.by_company),
                "categories_active": len([t for t in digest.by_type.values() if t]),
                "notable_items": len(digest.notable_items),
            },
            "by_type": type_counts,
            "most_active_companies": company_activity[:10],
            "key_themes": digest.key_themes,
            "notable_items": [
                {
                    "title": item.title,
                    "source": item.source,
                    "url": item.url,
                    "companies": item.company_mentions,
                    "type": item.news_type.value,
                    "relevance_score": item.relevance_score,
                }
                for item in digest.notable_items
            ],
            "summary": digest.summary,
        }

    def _render_html_report(self, digest: WeeklyNewsDigest) -> str:
        """Render HTML version of the report."""
        html_parts = [
            f"<h1>Weekly News Digest - Week of {digest.week_of}</h1>",
            (
                f"<p><strong>Generated:</strong> "
                f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}</p>'
            ),
            (
                f"<p><strong>Total Items:</strong> {digest.total_items} across "
                f"{len(digest.by_company)} companies</p>"
            ),
            "<hr>",
        ]

        # Executive summary
        if digest.summary:
            html_parts.extend(
                [
                    "<h2>Executive Summary</h2>",
                    f"<p>{digest.summary}</p>",
                ]
            )

        # Key themes
        if digest.key_themes:
            html_parts.extend(["<h2>Key Themes</h2>", "<ul>"])
            for theme in digest.key_themes:
                html_parts.append(f"<li>{theme}</li>")
            html_parts.extend(["</ul>"])

        # Notable items
        if digest.notable_items:
            html_parts.extend(
                [
                    "<h2>Notable Items</h2>",
                ]
            )

            for item in digest.notable_items[:10]:  # Top 10
                news_type_label = item.news_type.value.replace("_", " ").title()
                companies_str = ", ".join(item.company_mentions)
                html_parts.extend(
                    [
                        (
                            '<div style="margin-bottom: 15px; padding: 10px; '
                            'border-left: 3px solid #007acc;">'
                        ),
                        f'<h4><a href="{item.url}">{item.title}</a></h4>',
                        f"<p><strong>Source:</strong> {item.source} | ",
                        f"<strong>Type:</strong> {news_type_label} | ",
                        f"<strong>Companies:</strong> {companies_str}</p>",
                        "</div>",
                    ]
                )

        # By category
        html_parts.extend(
            [
                "<h2>News by Category</h2>",
            ]
        )

        for news_type, items in digest.by_type.items():
            if not items:
                continue

            category_name = news_type.value.replace("_", " ").title()
            html_parts.extend([f"<h3>{category_name} ({len(items)})</h3>", "<ul>"])

            # Sort by relevance and show top items
            sorted_items = sorted(items, key=lambda x: x.relevance_score, reverse=True)
            for item in sorted_items[:5]:  # Top 5 per category
                html_parts.append(f'<li><a href="{item.url}">{item.title}</a>')
                html_parts.append(
                    f'<br><small>{item.source} - {", ".join(item.company_mentions)}</small></li>'
                )

            html_parts.extend(["</ul>"])

        # Most active companies
        if digest.by_company:
            company_activity = [
                (company, len(items)) for company, items in digest.by_company.items()
            ]
            company_activity.sort(key=lambda x: x[1], reverse=True)

            html_parts.extend(["<h2>Most Active Companies</h2>", "<ul>"])

            for company, count in company_activity[:10]:
                html_parts.append(f"<li><strong>{company}:</strong> {count} news items</li>")

            html_parts.extend(["</ul>"])

        html_parts.extend(["<hr>", "<p><small>Generated by SeeRM Intelligence Reports</small></p>"])

        return "\n".join(html_parts)

    def _render_markdown_report(self, digest: WeeklyNewsDigest) -> str:
        """Render Markdown version of the report."""
        md_parts = [
            f"# Weekly News Digest - Week of {digest.week_of}",
            f'**Generated:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
            f"**Total Items:** {digest.total_items} across {len(digest.by_company)} companies",
            "",
        ]

        # Executive summary
        if digest.summary:
            md_parts.extend(["## Executive Summary", digest.summary, ""])

        # Key themes
        if digest.key_themes:
            md_parts.extend(["## Key Themes", ""])
            for theme in digest.key_themes:
                md_parts.append(f"• {theme}")
            md_parts.append("")

        # Notable items
        if digest.notable_items:
            md_parts.extend(["## Notable Items", ""])

            for item in digest.notable_items[:10]:
                news_type_label = item.news_type.value.replace("_", " ").title()
                md_parts.extend(
                    [
                        f"### [{item.title}]({item.url})",
                        f"**Source:** {item.source} | **Type:** {news_type_label}",
                        f"**Companies:** {', '.join(item.company_mentions)}",
                        "",
                    ]
                )

        # By category
        md_parts.extend(["## News by Category", ""])

        for news_type, items in digest.by_type.items():
            if not items:
                continue

            category_name = news_type.value.replace("_", " ").title()
            md_parts.extend([f"### {category_name} ({len(items)})", ""])

            # Sort by relevance and show top items
            sorted_items = sorted(items, key=lambda x: x.relevance_score, reverse=True)
            for item in sorted_items[:5]:
                md_parts.append(f"• [{item.title}]({item.url})")
                md_parts.append(f'  {item.source} - {", ".join(item.company_mentions)}')

            md_parts.append("")

        # Most active companies
        if digest.by_company:
            company_activity = [
                (company, len(items)) for company, items in digest.by_company.items()
            ]
            company_activity.sort(key=lambda x: x[1], reverse=True)

            md_parts.extend(["## Most Active Companies", ""])

            for company, count in company_activity[:10]:
                md_parts.append(f"• **{company}:** {count} news items")

            md_parts.append("")

        md_parts.extend(["---", "_Generated by SeeRM Intelligence Reports_"])

        return "\n".join(md_parts)

    def _send_email_report(self, report: Report, digest: WeeklyNewsDigest):
        """Send report via email with robust delivery and fallback options."""
        try:
            # Create robust email delivery system
            email_delivery = create_robust_email_delivery(
                gmail_client=self.aggregator.gmail_client,
                fallback_directory="./reports/email_fallbacks",
            )

            # Create bulletized email content
            email_content = self._create_email_bulletin(digest)
            subject = f"Weekly News Digest - {digest.total_items} items across portfolio"

            logger.info(
                "Starting robust email delivery",
                news_items=digest.total_items,
                content_size=len(email_content),
                subject=subject,
            )

            # Resolve recipients: prefer digest settings, fallback to Gmail user
            to_recipient = self.settings.digest.to or self.settings.gmail.user
            cc_recipients = self.settings.digest.cc
            bcc_recipients = self.settings.digest.bcc

            # Attempt delivery with automatic retry and fallback
            delivery_result = email_delivery.send_with_fallback(
                to=to_recipient,
                subject=subject,
                html=email_content,
                cc=cc_recipients,
                bcc=bcc_recipients,
            )

            # Update report based on delivery method
            if delivery_result["delivered"]:
                report.email_sent = True

                if delivery_result["method"] == "email":
                    logger.info(
                        "Weekly news report emailed successfully",
                        news_items=digest.total_items,
                        message_id=delivery_result["response"].get("id"),
                        attempts=delivery_result["attempts"],
                    )
                elif delivery_result["method"] == "file":
                    logger.warning(
                        "Email delivery failed - Report saved as HTML file",
                        news_items=digest.total_items,
                        fallback_file=delivery_result["fallback_file"],
                        original_error=delivery_result.get("error", "Unknown"),
                        attempts=delivery_result["attempts"],
                    )
                    # Add fallback file info to report metadata
                    if not hasattr(report.metadata, "additional_info"):
                        report.metadata.additional_info = {}
                    report.metadata.additional_info["fallback_file"] = delivery_result[
                        "fallback_file"
                    ]
                    report.metadata.additional_info["delivery_method"] = "file_fallback"

        except Exception as e:
            logger.error(
                "Complete email delivery failure",
                news_items=digest.total_items,
                error=str(e),
                error_type=type(e).__name__,
            )
            # Don't re-raise - allow report generation to continue
            report.email_sent = False

    def _get_company_page_urls(self, digest: WeeklyNewsDigest) -> Dict[str, Optional[str]]:
        """
        Get Notion page URLs for all companies mentioned in the news digest.

        Args:
            digest: WeeklyNewsDigest containing news items

        Returns:
            Dict mapping company callsigns to their Notion page URLs
            (or None if unavailable)
        """
        # Extract all unique company callsigns from the digest
        all_companies = set()
        for news_type, items in digest.by_type.items():
            for item in items:
                all_companies.update(item.company_mentions)

        company_callsigns = list(all_companies)

        if not company_callsigns or not self.notion_client:
            logger.debug("No companies or Notion client unavailable for URL mapping")
            return {company: None for company in company_callsigns}

        try:
            # Batch fetch company data including page IDs
            companies_data = self.notion_client.get_all_companies_domain_data(
                self.settings.notion.companies_db_id, company_callsigns
            )

            # Convert page IDs to URLs
            company_urls = {}
            for callsign in company_callsigns:
                callsign_lower = callsign.lower()
                company_data = companies_data.get(callsign_lower, {})
                page_id = company_data.get("page_id")

                if page_id:
                    company_urls[callsign] = self.notion_client.get_notion_page_url(page_id)
                else:
                    company_urls[callsign] = None

            logger.debug(
                "Company page URLs retrieved",
                total_companies=len(company_callsigns),
                companies_with_urls=len([url for url in company_urls.values() if url]),
            )

            return company_urls

        except Exception as e:
            logger.warning("Failed to get company page URLs", error=str(e))
            # Return None for all companies on error
            return {company: None for company in company_callsigns}

    def _create_email_bulletin(self, digest: WeeklyNewsDigest) -> str:
        """Create scannable intelligence digest optimized for executive review."""
        # Get category display information
        from app.intelligence.news_classifier import create_news_classifier

        classifier = create_news_classifier(self.settings)
        category_info = classifier.get_category_display_info()

        # Get Notion page URLs for all companies in the digest
        company_urls = self._get_company_page_urls(digest)

        parts = [
            "<h2>📊 Portfolio Intelligence Digest</h2>",
            (
                f"<p><strong>Week of {digest.week_of}</strong> • "
                f"{digest.total_items} intel items analyzed</p>"
            ),
            "<hr>",
        ]

        # Group companies by category from the digest data
        companies_by_category = {}
        for news_type, items in digest.by_type.items():
            if items:
                companies = set()
                for item in items:
                    companies.update(item.company_mentions)
                companies_by_category[news_type] = sorted(list(companies))

        # Display categories with activity (scannable format)
        active_categories = []

        # Priority order for display
        category_priority = [
            NewsType.FUNDING,
            NewsType.ACQUISITION,
            NewsType.PRODUCT_LAUNCH,
            NewsType.PARTNERSHIPS,
            NewsType.LEADERSHIP,
            NewsType.GROWTH_METRICS,
            NewsType.LEGAL_REGULATORY,
            NewsType.TECHNICAL,
            NewsType.OTHER_NOTABLE,
        ]

        for category in category_priority:
            if category in companies_by_category and companies_by_category[category]:
                info = category_info.get(category, {"emoji": "📰", "title": category.value.title()})
                companies = companies_by_category[category]

                # Create company links
                # (HTML links for companies with page URLs, plain text for others)
                company_links = []
                for company in companies:
                    page_url = company_urls.get(company)
                    if page_url:
                        # Escape both the URL and company name to prevent XSS
                        escaped_url = html.escape(page_url, quote=True)
                        escaped_company = html.escape(company)
                        company_links.append(
                            (
                                f'<a href="{escaped_url}" '
                                f'style="text-decoration: none; color: #0066cc;">'
                                f"{escaped_company}</a>"
                            )
                        )
                    else:
                        company_links.append(html.escape(company))

                companies_str = ", ".join(company_links)
                parts.append(
                    (
                        f"<p><strong>{info['emoji']} {info['title'].upper()} "
                        f"({len(companies)} companies)</strong><br>"
                        f"• {companies_str}</p>"
                    )
                )
                active_categories.append(category)

        # Show empty categories (for completeness)
        empty_categories = []
        for category in category_priority:
            if category not in companies_by_category or not companies_by_category[category]:
                info = category_info.get(category, {"emoji": "📰", "title": category.value.title()})
                empty_categories.append(f"{info['emoji']} {info['title']}")

        if empty_categories:
            parts.extend(
                [
                    "<hr>",
                    f"<p><small>No activity in: {', '.join(empty_categories)}</small></p>",
                ]
            )

        # Summary footer
        parts.extend(
            [
                "<hr>",
                "<p><strong>📈 Activity Summary</strong><br>",
                f"• {len(active_categories)} categories with activity<br>",
                f"• {len(set().union(*companies_by_category.values()))} companies with news<br>",
                f"• {digest.total_items} total intelligence items</p>",
                "",
                (
                    "<p><small>Full details available in Notion • "
                    "Generated by SeeRM Intelligence Reports</small></p>"
                ),
            ]
        )

        return "\n".join(parts)

    def _create_notion_report(self, report: Report, digest: WeeklyNewsDigest) -> Optional[str]:
        """Create report page in Notion."""
        try:
            if not self.notion_client or not self.settings.notion.reports_db_id:
                logger.debug("Notion not configured for report storage")
                return None

            # Prepare metadata for the report
            metadata = {
                "News Items": digest.total_items,
                "Unique Companies": len(digest.by_company),
                "Notable Items": len(digest.notable_items),
                "Categories Active": len([t for t in digest.by_type.values() if t]),
                "Duration": f"{report.metadata.duration_seconds:.1f}s",
                "Week Of": digest.week_of,
                "Key Themes": digest.key_themes,  # Multi-select if supported
            }

            # Add category breakdown
            for news_type, items in digest.by_type.items():
                if items:
                    category_name = news_type.value.replace("_", " ").title()
                    metadata[f"{category_name} Count"] = len(items)

            # Create the report page
            page_id = self.notion_client.create_report_page(
                database_id=self.settings.notion.reports_db_id,
                title=report.title,
                report_type="weekly_news",
                content_markdown=report.markdown,
                metadata=metadata,
            )

            if page_id:
                logger.info(
                    "Weekly news report created in Notion",
                    news_items=digest.total_items,
                    page_id=page_id,
                    report_id=report.metadata.report_id,
                )

            return page_id

        except Exception as e:
            logger.error(
                "Failed to create Notion report",
                news_items=digest.total_items,
                error=str(e),
            )
            return None
