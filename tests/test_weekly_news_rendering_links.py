"""
Tests for correct interpolation of links/titles in WeeklyNewsReport HTML rendering.
"""

from app.intelligence.models import NewsItem, NewsType, WeeklyNewsDigest
from app.reports.weekly_news import WeeklyNewsReport


class DummyAggregator:
    def __init__(self):
        self.gmail_client = None


def test_weekly_news_html_contains_links():
    aggregator = DummyAggregator()
    report_gen = WeeklyNewsReport(aggregator=aggregator, notion_client=None)

    # Build digest with notable item and category items
    item1 = NewsItem(
        title="Example Title",
        url="https://example.com/a",
        source="example.com",
        published_at="2025-01-01",
        news_type=NewsType.PRODUCT_LAUNCH,
        company_mentions=["ACME"],
        relevance_score=0.9,
    )

    digest = WeeklyNewsDigest(
        week_of="2025-01-01",
        total_items=1,
        by_type={NewsType.PRODUCT_LAUNCH: [item1]},
        by_company={"ACME": [item1]},
        key_themes=[],
        notable_items=[item1],
        summary="",
    )

    html = report_gen._render_html_report(digest)

    assert "https://example.com/a" in html
    assert ">Example Title<" in html
    # Category list section
    assert '<li><a href="https://example.com/a">Example Title</a>' in html
