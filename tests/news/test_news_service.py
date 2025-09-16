"""Behavioural tests for news collection with quality filtering."""

from datetime import datetime, timedelta

from app.core.config import IntelligenceConfig
from app.core.models import Company
from app.intelligence.models import NewsItem
from app.services.news_service import NewsCollector


def make_config() -> IntelligenceConfig:
    """Return a configuration tuned for deterministic tests."""
    cfg = IntelligenceConfig()
    cfg.google_api_key = "test"
    cfg.google_cse_id = "test"
    cfg.blocked_domains = ["bad.com"]
    cfg.trusted_domains = ["good.com"]
    cfg.lookback_days = 7
    cfg.max_per_org = 3
    return cfg


def make_company() -> Company:
    """Return a sample company used across tests."""
    return Company(callsign="acme", dba="Acme", website="https://acme.com")


def test_collect_company_news_filters_blocked_domains(monkeypatch):
    """Blocked domains should not appear in results."""
    collector = NewsCollector(make_config())
    company = make_company()

    def fake_collect_google_search(query, date_restrict=None, num_results=5, exclude_domains=None):
        return [
            NewsItem(
                title="Legit article",
                url="https://good.com/acme",
                source="good.com",
                published_at=datetime.utcnow().isoformat(),
            ),
            NewsItem(
                title="Spam article",
                url="https://bad.com/acme",
                source="bad.com",
                published_at=datetime.utcnow().isoformat(),
            ),
        ]

    monkeypatch.setattr(collector, "collect_google_search", fake_collect_google_search)

    results = collector.collect_company_news(company)

    assert len(results) == 1
    assert results[0].source == "good.com"


def test_collect_company_news_applies_scoring(monkeypatch):
    """Scoring should prioritise high-quality recent items."""
    collector = NewsCollector(make_config())
    company = make_company()

    def fake_collect_google_search(query, date_restrict=None, num_results=5, exclude_domains=None):
        now = datetime.utcnow().isoformat()
        return [
            NewsItem(
                title="Acme raises new funding",
                url="https://good.com/funding",
                source="good.com",
                published_at=now,
            ),
            NewsItem(
                title="Acme posts blog update",
                url="https://unknown.com/blog",
                source="unknown.com",
                published_at=(datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
            ),
        ]

    monkeypatch.setattr(collector, "collect_google_search", fake_collect_google_search)

    results = collector.collect_company_news(company)

    assert results  # not empty
    assert results[0].source == "good.com"
    assert results[0].relevance_score >= 0.2
