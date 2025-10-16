"""Scaffolding for news relevance scoring and dossier snapshots.

This module currently provides placeholder logic so we can integrate a
structured filtering stage without changing behaviour yet. The TODO
markers highlight where richer heuristics and data sourcing will go.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import structlog

from app.core.config import IntelligenceConfig
from app.core.models import Company, NewsItem

logger = structlog.get_logger(__name__)


def _generate_snapshot_id(callsign: str) -> str:
    """Return a deterministic snapshot identifier for auditing."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{callsign.lower()}::{ts}"


@dataclass
class CompanyDossierSnapshot:
    """Lightweight view of company identifiers used for relevance checks."""

    callsign: str
    snapshot_id: str
    primary_name: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    domains: List[str] = field(default_factory=list)
    executives: List[str] = field(default_factory=list)
    product_terms: List[str] = field(default_factory=list)
    negative_terms: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def keyword_universe(self) -> List[str]:
        """Return the full set of positive keywords."""
        keywords = set()
        for value in [self.primary_name] + self.aliases + self.product_terms:
            if value:
                keywords.add(value.lower())
        return sorted(keywords)


class CompanyDossierBuilder:
    """Construct dossier snapshots from existing data sources."""

    def __init__(self, notion_client: Any = None):
        """Create builder with optional Notion client for future enrichment."""
        self.notion_client = notion_client

    def build_minimal(
        self, company: Company, enhanced_data: Optional[Dict[str, Any]] = None
    ) -> CompanyDossierSnapshot:
        """
        Build a minimal snapshot from the company object and enhanced data.

        TODO: expand to pull structured aliases, product tags, and executives
        from Notion/company dossiers once we wire the data layer in.
        """
        domains: List[str] = []
        primary_domain = (enhanced_data or {}).get("domain") or company.domain_root
        if primary_domain:
            domains.append(str(primary_domain).strip().lower())

        website_domain = (enhanced_data or {}).get("website") or company.website
        if website_domain:
            domains.append(str(website_domain).strip().lower())

        alias_candidates: List[str] = []
        if company.dba:
            alias_candidates.append(company.dba)
        if company.callsign:
            alias_candidates.append(company.callsign)
        aka_names_company = getattr(company, "aka_names", None)
        enhanced = enhanced_data or {}

        if aka_names_company:
            alias_candidates.extend(
                [name.strip() for name in str(aka_names_company).split(",") if name.strip()]
            )

        enhanced_aliases = enhanced.get("aliases") or []
        if isinstance(enhanced_aliases, str):
            enhanced_aliases = [enhanced_aliases]
        alias_candidates.extend([alias for alias in enhanced_aliases if alias])

        enhanced_aka = enhanced.get("aka_names")
        if isinstance(enhanced_aka, str):
            alias_candidates.extend(
                [name.strip() for name in enhanced_aka.split(",") if name.strip()]
            )
        elif isinstance(enhanced_aka, list):
            alias_candidates.extend([name for name in enhanced_aka if name])

        aliases: List[str] = []
        seen_aliases: set[str] = set()
        for alias in alias_candidates:
            cleaned = alias.strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen_aliases:
                continue
            seen_aliases.add(key)
            aliases.append(cleaned)

        executives = [owner for owner in (company.beneficial_owners or []) if owner]
        enhanced_owners = enhanced.get("owners") or []
        if isinstance(enhanced_owners, str):
            enhanced_owners = [enhanced_owners]
        executives.extend([owner for owner in enhanced_owners if owner])

        product_terms = []
        enhanced_products = enhanced.get("products") or []
        if isinstance(enhanced_products, str):
            enhanced_products = [enhanced_products]
        product_terms.extend([prod for prod in enhanced_products if prod])

        enhanced_tags = enhanced.get("tags") or []
        if isinstance(enhanced_tags, str):
            enhanced_tags = [enhanced_tags]
        product_terms.extend([tag for tag in enhanced_tags if tag])

        snapshot = CompanyDossierSnapshot(
            callsign=company.callsign,
            snapshot_id=_generate_snapshot_id(company.callsign),
            primary_name=(enhanced.get("company") or company.dba or company.callsign),
            aliases=aliases,
            domains=sorted({d for d in domains if d}),
            executives=sorted({owner.strip() for owner in executives if owner}),
            product_terms=sorted({term.strip().lower() for term in product_terms if term}),
            metadata={
                "source": "enhanced" if enhanced else "minimal",
                "has_enhanced_data": bool(enhanced),
            },
        )

        return snapshot

    def build(
        self, company: Company, enhanced_data: Optional[Dict[str, Any]] = None
    ) -> CompanyDossierSnapshot:
        """Public entry-point retained for future expansion."""
        # Placeholder to aid future richer integration.
        return self.build_minimal(company, enhanced_data)


@dataclass
class NewsRelevanceDecision:
    """Decision payload returned by the relevance scorer."""

    verdict: str  # "accept" | "review" | "reject"
    score: float
    reasons: List[str] = field(default_factory=list)
    snapshot_id: Optional[str] = None


class NewsRelevanceScorer:
    """Evaluate news items against a dossier snapshot."""

    def __init__(self, config: IntelligenceConfig):
        """Initialise scorer with configuration thresholds and feature flag."""
        self.config = config
        self.enabled = config.news_relevance_enabled
        self.accept_threshold = config.news_relevance_accept_threshold
        self.review_threshold = config.news_relevance_review_threshold

    def _basic_score(
        self, item: NewsItem, snapshot: CompanyDossierSnapshot
    ) -> Tuple[float, List[str]]:
        """Baseline scoring heuristic used until richer logic lands."""
        if not snapshot:
            return 0.0, ["no_snapshot"]

        text = f"{item.title} {item.source}".lower()
        score = 0.0
        reasons: List[str] = []

        for keyword in snapshot.keyword_universe():
            if keyword and keyword in text:
                score += 0.4
                reasons.append(f"keyword:{keyword}")

        for domain in snapshot.domains:
            if domain and domain in item.url.lower():
                score += 0.6
                reasons.append(f"domain:{domain}")

        # Cap score for now to keep thresholds predictable.
        return min(score, 1.5), reasons

    def score_item(self, item: NewsItem, snapshot: CompanyDossierSnapshot) -> NewsRelevanceDecision:
        """Return a relevance decision for a single news item."""
        base_score, reasons = self._basic_score(item, snapshot)

        if base_score >= self.accept_threshold:
            verdict = "accept"
        elif base_score >= self.review_threshold:
            verdict = "review"
        else:
            verdict = "reject"

        return NewsRelevanceDecision(
            verdict=verdict,
            score=base_score,
            reasons=reasons,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
        )

    def filter_items(
        self,
        items: Iterable[NewsItem],
        snapshot: CompanyDossierSnapshot,
    ) -> Tuple[List[NewsItem], List[NewsItem]]:
        """
        Partition items into accepted and rejected lists.

        Currently returns early if the feature flag is disabled to avoid
        behaviour changes until the richer logic is delivered.
        """
        if not self.enabled:
            return list(items), []

        accepted: List[NewsItem] = []
        rejected: List[NewsItem] = []

        for item in items:
            decision = self.score_item(item, snapshot)
            item.relevance_score = decision.score
            item.relevance_verdict = decision.verdict
            item.relevance_snapshot_id = decision.snapshot_id
            item.relevance_reasons = decision.reasons

            if decision.verdict == "reject":
                rejected.append(item)
                continue

            accepted.append(item)

        logger.debug(
            "relevance_filter_completed",
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            accepted=len(accepted),
            rejected=len(rejected),
            enabled=self.enabled,
        )

        return accepted, rejected
