"""Tests for NewsCollector query building to ensure variables are interpolated."""

from app.core.config import IntelligenceConfig
from app.core.models import Company
from app.services.news_service import NewsCollector


def test_build_search_queries_interpolates_values():
    """Ensure query builder embeds company metadata."""
    config = IntelligenceConfig()
    collector = NewsCollector(config)

    company = Company(
        callsign="acme",
        dba="ACME Corp",
        website="https://www.acme.io",
        domain_root="acme.io",
        beneficial_owners=["Jane Doe"],
    )

    queries = collector.build_search_queries(company)

    assert any('"ACME Corp" news' in q for q in queries)
    assert any('"ACME Corp" (acquisition' in q for q in queries)
    assert any(q.startswith('site:acme.io "ACME Corp"') for q in queries)
