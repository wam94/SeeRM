"""
Tests for NewsCollector query building to ensure variables are interpolated.
"""

from app.core.config import IntelligenceConfig
from app.core.models import Company
from app.services.news_service import NewsCollector


def test_build_search_queries_interpolates_values():
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

    # Name-based query is interpolated
    assert any('"ACME Corp" (launch' in q for q in queries)
    # Domain-based query is interpolated
    assert any(q.startswith("site:acme.io ") for q in queries)
    # Owner-based query includes owner and name/domain
    assert any('"Jane Doe" ("ACME Corp" OR site:acme.io)' in q for q in queries)
