"""Unit tests for the news quality scorer."""

from datetime import datetime, timedelta

from app.core.config import IntelligenceConfig
from app.core.models import Company
from app.intelligence.models import NewsItem
from app.intelligence.news_quality import MIN_SCORE, NewsQualityScorer


def make_scorer() -> NewsQualityScorer:
    """Construct a scorer with deterministic preferences."""
    config = IntelligenceConfig()
    config.trusted_domains = ["example.com"]
    config.blocked_domains = ["blocked.com"]
    config.demoted_domains = ["pressrelease.com"]
    config.positive_keywords = ["milestone"]
    config.negative_keywords = ["hiring"]
    return NewsQualityScorer(config)


def make_company() -> Company:
    """Create a baseline company instance."""
    return Company(
        callsign="acme",
        dba="ACME Corp",
        website="https://acme.com",
        domain_root="acme.com",
    )


def make_item(**overrides) -> NewsItem:
    """Create a news item with optional overrides."""
    item = NewsItem(
        title="Acme announces new partnership",
        url="https://example.com/acme/partnership",
        source="example.com",
        published_at=datetime.utcnow().isoformat(),
    )
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def test_trusted_domain_scores_high():
    """Trusted domains should receive a positive score."""
    scorer = make_scorer()
    company = make_company()
    item = make_item()

    score, blocked = scorer.score_item(company, item)

    assert not blocked
    assert score > 1.0


def test_blocked_domain_is_dropped():
    """Blocked domains should be discarded entirely."""
    scorer = make_scorer()
    company = make_company()
    item = make_item(url="https://blocked.com/acme/news", source="blocked.com")

    score, blocked = scorer.score_item(company, item)

    assert blocked
    assert score < 0


def test_keywords_adjust_score():
    """Positive keywords should outweigh negative ones."""
    scorer = make_scorer()
    company = make_company()

    positive_item = make_item(title="Acme hits new milestone")
    negative_item = make_item(title="Acme is hiring engineers")

    positive_score, _ = scorer.score_item(company, positive_item)
    negative_score, _ = scorer.score_item(company, negative_item)

    assert positive_score > MIN_SCORE
    assert negative_score < positive_score


def test_recency_penalty():
    """Older news should receive a lower score than recent news."""
    scorer = make_scorer()
    company = make_company()

    fresh = make_item()
    old_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    item = make_item(published_at=old_date)

    fresh_score, _ = scorer.score_item(company, fresh)
    old_score, _ = scorer.score_item(company, item)

    assert old_score < fresh_score


def test_company_match():
    """Company mentions should boost relevance."""
    scorer = make_scorer()
    company = make_company()
    item = make_item(title="ACME Corp expands globally")

    score, _ = scorer.score_item(company, item)

    assert score > MIN_SCORE


def test_rank_items_filters_low_score():
    """Ranking should prefer high-quality items."""
    scorer = make_scorer()
    company = make_company()
    good = make_item()
    bad = make_item(url="https://pressrelease.com/acme", source="pressrelease.com")

    ranked = scorer.rank_items(company, [good, bad], max_items=5)

    assert ranked[0].source == "example.com"
    assert ranked[0].relevance_score > MIN_SCORE


def test_rank_items_limits_results():
    """Ranking should respect the requested limit."""
    scorer = make_scorer()
    company = make_company()
    items = [make_item(title=f"Item {idx}") for idx in range(10)]

    ranked = scorer.rank_items(company, items, max_items=3)

    assert len(ranked) == 3


def test_company_domain_bonus():
    """Links hosted on the company domain should be strongly favoured."""
    scorer = make_scorer()
    company = make_company()
    company_item = make_item(
        url="https://news.acme.com/update",
        source="news.acme.com",
    )
    external_item = make_item(
        url="https://unknown.com/acme",
        source="unknown.com",
    )

    company_score, _ = scorer.score_item(company, company_item)
    external_score, _ = scorer.score_item(company, external_item)

    assert company_score > external_score
