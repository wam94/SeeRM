"""
Shared data structures for the LLM research pipeline.

These models capture the structured inputs (context + deterministic hints)
and the structured outputs returned by the LLM tiers so downstream code
can persist, inspect, and enrich them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class EvidenceItem:
    """Single piece of evidence backing an LLM claim."""

    url: str
    title: Optional[str] = None
    source: Optional[str] = None


@dataclass(slots=True)
class CompanyResearchContext:
    """
    Normalised set of identifiers passed to every LLM tier.

    The orchestrator builds this from CSV / Notion intake so each agent
    sees a consistent snapshot of what we already know.
    """

    callsign: str
    dba: str
    owners: List[str] = field(default_factory=list)
    ingested_domain: Optional[str] = None
    ingested_website: Optional[str] = None
    alias_names: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    linkedin_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    crunchbase_url: Optional[str] = None
    supplementary_hints: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompanyProfileIntel:
    """
    Structured company profile surfaced by the profile agent.

    Intentionally mirrors the fields relationship managers ask for so we
    can persist this alongside the dossier markdown.
    """

    products: List[str] = field(default_factory=list)
    target_customers: List[str] = field(default_factory=list)
    value_proposition: Optional[str] = None
    business_model: Optional[str] = None
    revenue_model: Optional[str] = None
    go_to_market: Optional[str] = None
    headcount_range: Optional[str] = None
    headquarters: Optional[str] = None
    notable_customers: List[str] = field(default_factory=list)
    differentiation: Optional[str] = None
    open_questions: List[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    sources: List[str] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to primitive dict for downstream JSON serialisation."""
        return {
            "products": self.products,
            "target_customers": self.target_customers,
            "value_proposition": self.value_proposition,
            "business_model": self.business_model,
            "revenue_model": self.revenue_model,
            "go_to_market": self.go_to_market,
            "headcount_range": self.headcount_range,
            "headquarters": self.headquarters,
            "notable_customers": self.notable_customers,
            "differentiation": self.differentiation,
            "open_questions": self.open_questions,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "sources": self.sources,
        }


@dataclass(slots=True)
class ResearchBundle:
    """Aggregate of all tier outputs for a company."""

    context: CompanyResearchContext
    identity: Any
    funding: Any
    profile: Optional[CompanyProfileIntel] = None
    notes: Dict[str, Any] = field(default_factory=dict)
