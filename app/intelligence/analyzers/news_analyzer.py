"""News analysis functionality for intelligence reports."""

from collections import Counter, defaultdict
from typing import Dict, List

import structlog

from ..models import NewsItem, NewsType, WeeklyNewsDigest

logger = structlog.get_logger(__name__)


class NewsAnalyzer:
    """Analyzes news data for intelligence reports."""

    def categorize_news(self, news_items: List[NewsItem]) -> Dict[NewsType, List[NewsItem]]:
        """
        Categorize news items by type.

        Args:
            news_items: List of news items to categorize

        Returns:
            Dict mapping news types to lists of items
        """
        categorized = defaultdict(list)

        for item in news_items:
            categorized[item.news_type].append(item)

        # Sort each category by date (most recent first)
        for news_type, items in categorized.items():
            items.sort(key=lambda x: x.published_at, reverse=True)

        logger.debug("News categorized", total_items=len(news_items), categories=len(categorized))

        return dict(categorized)

    def group_by_company(self, news_items: List[NewsItem]) -> Dict[str, List[NewsItem]]:
        """
        Group news items by company.

        Args:
            news_items: List of news items to group

        Returns:
            Dict mapping company names to their news
        """
        by_company = defaultdict(list)

        for item in news_items:
            for company in item.company_mentions:
                by_company[company].append(item)

        # Sort each company's news by date
        for company, items in by_company.items():
            items.sort(key=lambda x: x.published_at, reverse=True)

        return dict(by_company)

    def extract_themes(self, news_items: List[NewsItem]) -> List[str]:
        """
        Extract key themes from news items.

        Args:
            news_items: List of news items to analyze

        Returns:
            List of theme strings
        """
        themes = []

        # Count news types
        type_counts = Counter(item.news_type for item in news_items)

        # Generate themes based on patterns
        if type_counts[NewsType.FUNDING] > 3:
            themes.append(
                f"Active funding period - {type_counts[NewsType.FUNDING]} fundraising events"
            )

        if type_counts[NewsType.PARTNERSHIPS] > 2:
            themes.append(
                f"Partnership expansion - {type_counts[NewsType.PARTNERSHIPS]} new partnerships"
            )

        if type_counts[NewsType.LEADERSHIP] > 2:
            themes.append(
                f"Leadership changes - {type_counts[NewsType.LEADERSHIP]} executive updates"
            )

        if type_counts[NewsType.PRODUCT_LAUNCH] > 2:
            themes.append(
                f"Product innovation - {type_counts[NewsType.PRODUCT_LAUNCH]} new launches"
            )

        # Sentiment analysis themes
        sentiment_counts = Counter(item.sentiment for item in news_items if item.sentiment)

        if sentiment_counts.get("positive", 0) > sentiment_counts.get("negative", 0) * 2:
            themes.append("Predominantly positive news sentiment")
        elif sentiment_counts.get("negative", 0) > sentiment_counts.get("positive", 0) * 2:
            themes.append("Concerning negative news trend")

        # Company activity themes
        company_news = self.group_by_company(news_items)
        most_active = max(company_news.items(), key=lambda x: len(x[1]), default=None)

        if most_active and len(most_active[1]) > 3:
            themes.append(f"High activity from {most_active[0]} - {len(most_active[1])} news items")

        return themes[:5]  # Return top 5 themes

    def identify_notable_items(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """
        Identify the most notable news items.

        Args:
            news_items: List of news items to analyze

        Returns:
            List of notable news items
        """
        notable = []

        # High relevance score items
        high_relevance = [item for item in news_items if item.relevance_score > 0.8]
        notable.extend(high_relevance)

        # Funding news (always notable)
        funding_news = [item for item in news_items if item.news_type == NewsType.FUNDING]
        notable.extend(funding_news)

        # Acquisition news (always notable)
        acquisition_news = [item for item in news_items if item.news_type == NewsType.ACQUISITION]
        notable.extend(acquisition_news)

        # Remove duplicates and sort by relevance
        seen_urls = set()
        unique_notable = []

        for item in notable:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_notable.append(item)

        # Sort by relevance score
        unique_notable.sort(key=lambda x: x.relevance_score, reverse=True)

        return unique_notable[:10]  # Top 10 notable items

    def generate_weekly_digest(self, news_items: List[NewsItem], week_of: str) -> WeeklyNewsDigest:
        """
        Generate structured weekly news digest.

        Args:
            news_items: News items for the week
            week_of: Week identifier (e.g., "2024-03-01")

        Returns:
            WeeklyNewsDigest object
        """
        logger.info("Generating weekly news digest", items=len(news_items), week=week_of)

        digest = WeeklyNewsDigest(
            week_of=week_of,
            total_items=len(news_items),
            by_type=self.categorize_news(news_items),
            by_company=self.group_by_company(news_items),
            key_themes=self.extract_themes(news_items),
            notable_items=self.identify_notable_items(news_items),
            summary=self._generate_summary_text(news_items),
        )

        return digest

    def format_for_email(self, digest: WeeklyNewsDigest) -> str:
        """
        Format weekly digest for email delivery.

        Args:
            digest: WeeklyNewsDigest object

        Returns:
            Formatted text for email
        """
        lines = [
            f"# Weekly News Digest - Week of {digest.week_of}",
            f"Total Items: {digest.total_items}",
            "",
        ]

        # Summary
        if digest.summary:
            lines.extend(["## Executive Summary", digest.summary, ""])

        # Key themes
        if digest.key_themes:
            lines.extend(["## Key Themes", ""])
            for theme in digest.key_themes:
                lines.append(f"• {theme}")
            lines.append("")

        # Notable items
        if digest.notable_items:
            lines.extend(["## Notable Items", ""])
            for item in digest.notable_items[:5]:  # Top 5
                lines.append(f"• **{item.title}**")
                lines.append(f"  Source: {item.source} | Type: {item.news_type.value}")
                lines.append(f"  Companies: {', '.join(item.company_mentions)}")
                if item.url:
                    lines.append(f"  Link: {item.url}")
                lines.append("")

        # By category
        lines.extend(["## News by Category", ""])

        for news_type, items in digest.by_type.items():
            if not items:
                continue

            lines.append(f"### {news_type.value.title().replace('_', ' ')} ({len(items)})")
            lines.append("")

            for item in items[:3]:  # Top 3 per category
                lines.append(f"• {item.title}")
                if item.company_mentions:
                    lines.append(f"  ({', '.join(item.company_mentions)})")
            lines.append("")

        return "\n".join(lines)

    def _generate_summary_text(self, news_items: List[NewsItem]) -> str:
        """Generate AI-style summary text."""
        if not news_items:
            return "No news items to summarize."

        # Count by type
        type_counts = Counter(item.news_type for item in news_items)
        company_counts = Counter()
        for item in news_items:
            for company in item.company_mentions:
                company_counts[company] += 1

        summary_parts = []

        # Overall activity
        summary_parts.append(f"This week saw {len(news_items)} news items across the portfolio")

        # Top categories
        if type_counts:
            top_type = type_counts.most_common(1)[0]
            top_category = top_type[0].value.replace("_", " ")
            summary_parts.append(
                ("with " f"{top_category} being the most active category " f"({top_type[1]} items)")
            )

        # Most active company
        if company_counts:
            top_company = company_counts.most_common(1)[0]
            if top_company[1] > 1:
                summary_parts.append(
                    f"Most active company was {top_company[0]} with {top_company[1]} news items"
                )

        return ". ".join(summary_parts) + "."
