"""Smoke tests for news relevance scaffolding."""

from app.core.models import Company, NewsItem
from app.intelligence.news_relevance import CompanyDossierBuilder, NewsRelevanceScorer


class _DummyConfig:
    """Minimal config shim for the scorer tests."""

    def __init__(
        self,
        *,
        enabled: bool,
        accept_threshold: float = 0.9,
        review_threshold: float = 0.4,
    ):
        self.news_relevance_enabled = enabled
        self.news_relevance_accept_threshold = accept_threshold
        self.news_relevance_review_threshold = review_threshold


def test_relevance_filter_noop_when_disabled():
    """Relevance filter should return inputs unchanged when disabled."""
    config = _DummyConfig(enabled=False)
    scorer = NewsRelevanceScorer(config)  # type: ignore[arg-type]
    builder = CompanyDossierBuilder()

    company = Company(callsign="acme")
    snapshot = builder.build(company)
    items = [
        NewsItem(title="Generic headline", url="https://example.com/news", source="Example"),
    ]

    accepted, rejected = scorer.filter_items(items, snapshot)

    assert accepted == items
    assert rejected == []


def test_relevance_filter_sets_metadata_on_accept():
    """Accepted items should have relevance metadata populated."""
    config = _DummyConfig(enabled=True, accept_threshold=0.3, review_threshold=0.2)
    scorer = NewsRelevanceScorer(config)  # type: ignore[arg-type]
    builder = CompanyDossierBuilder()

    company = Company(callsign="acme", dba="Acme Corp")
    snapshot = builder.build(company)
    item = NewsItem(
        title="Acme Corp launches new product",
        url="https://acme.com/news/product-launch",
        source="Example Wire",
    )

    accepted, rejected = scorer.filter_items([item], snapshot)

    assert len(accepted) == 1
    assert not rejected
    assert accepted[0].relevance_score is not None
    assert accepted[0].relevance_verdict == "accept"
    assert accepted[0].relevance_snapshot_id == snapshot.snapshot_id


def test_dossier_builder_merges_enhanced_aliases_and_products():
    """Enhanced Notion data should populate snapshot aliases and product terms."""
    builder = CompanyDossierBuilder()
    company = Company(callsign="uniswapfoundation")
    snapshot = builder.build(
        company,
        {
            "company": "Uniswap Foundation",
            "aliases": ["Uniswap Foundation"],
            "owners": ["Alice Example"],
            "products": ["DeFi Grants"],
            "tags": ["Protocol"],
        },
    )

    assert "Uniswap Foundation" in snapshot.aliases
    assert "Alice Example" in snapshot.executives
    assert "defi grants" in snapshot.product_terms
