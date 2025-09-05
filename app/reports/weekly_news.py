"""
Weekly News Digest Report Generator

Creates weekly bulletized news summaries categorized by type
and organized by company for portfolio intelligence.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import structlog

from app.core.config import Settings
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
            "Generating weekly news report", days=days, week_of=week_of, report_id=report_id
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
            f'<p><strong>Generated:</strong> {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}</p>',
            f"<p><strong>Total Items:</strong> {digest.total_items} across {len(digest.by_company)} companies</p>",
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
                html_parts.extend(
                    [
                        f'<div style="margin-bottom: 15px; padding: 10px; border-left: 3px solid #007acc;">',
                        f'<h4><a href="{item.url}">{item.title}</a></h4>',
                        f"<p><strong>Source:</strong> {item.source} | ",
                        f'<strong>Type:</strong> {item.news_type.value.replace("_", " ").title()} | ',
                        f'<strong>Companies:</strong> {", ".join(item.company_mentions)}</p>',
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
                md_parts.extend(
                    [
                        f"### [{item.title}]({item.url})",
                        f'**Source:** {item.source} | **Type:** {item.news_type.value.replace("_", " ").title()}',
                        f'**Companies:** {", ".join(item.company_mentions)}',
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
        """Send report via email."""
        try:
            if not self.aggregator.gmail_client:
                logger.warning("Gmail client not available for email delivery")
                return

            # Create bulletized email content
            email_content = self._create_email_bulletin(digest)

            subject = f"Weekly News Digest - {digest.total_items} items across portfolio"

            response = self.aggregator.gmail_client.send_html_email(
                to=self.settings.gmail.user, subject=subject, html=email_content
            )

            report.email_sent = True
            logger.info(
                "Weekly news report emailed",
                news_items=digest.total_items,
                message_id=response.get("id"),
            )

        except Exception as e:
            logger.error("Failed to send weekly news report email", error=str(e))

    def _create_email_bulletin(self, digest: WeeklyNewsDigest) -> str:
        """Create scannable intelligence digest optimized for executive review."""

        # Get category display information
        from app.intelligence.news_classifier import create_news_classifier

        classifier = create_news_classifier(self.settings)
        category_info = classifier.get_category_display_info()

        parts = [
            f"<h2>📊 Portfolio Intelligence Digest</h2>",
            f"<p><strong>Week of {digest.week_of}</strong> • {digest.total_items} intel items analyzed</p>",
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

                parts.append(
                    f"<p><strong>{info['emoji']} {info['title'].upper()} ({len(companies)} companies)</strong><br>"
                    f"• {', '.join(companies)}</p>"
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
                ["<hr>", f"<p><small>No activity in: {', '.join(empty_categories)}</small></p>"]
            )

        # Summary footer
        parts.extend(
            [
                "<hr>",
                f"<p><strong>📈 Activity Summary</strong><br>",
                f"• {len(active_categories)} categories with activity<br>",
                f"• {len(set().union(*companies_by_category.values()))} companies with news<br>",
                f"• {digest.total_items} total intelligence items</p>",
                "",
                "<p><small>Full details available in Notion • Generated by SeeRM Intelligence Reports</small></p>",
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
                "Failed to create Notion report", news_items=digest.total_items, error=str(e)
            )
            return None
