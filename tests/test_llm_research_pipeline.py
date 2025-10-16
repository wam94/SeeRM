"""Unit tests for the multi-stage LLM research pipeline."""

from __future__ import annotations

from app.intelligence.llm_funding_agent import FundingIntelligence, FundingRound
from app.intelligence.llm_identity_agent import CompanyIdentity
from app.intelligence.llm_research_pipeline import LLMResearchPipeline
from app.intelligence.research_models import CompanyProfileIntel


class DummyIdentityAgent:
    """Test double that returns a deterministic identity payload."""

    def resolve(self, *args, **kwargs) -> CompanyIdentity:
        """Return canned identity findings."""
        return CompanyIdentity(
            current_domain="example.com",
            current_website="https://example.com",
            status="active",
            confidence=0.92,
            reasoning="Verified via official site and LinkedIn.",
            sources=["https://example.com"],
        )


class DummyFundingAgent:
    """Test double that returns a deterministic funding payload."""

    def research(self, *args, **kwargs) -> FundingIntelligence:
        """Return canned funding intelligence."""
        latest_round = FundingRound(
            amount_usd=5_000_000,
            round_type="Seed",
            announced_date="2024-02-01",
            lead_investors=["Alpha Capital"],
            participants=["Beta Ventures"],
        )
        return FundingIntelligence(
            latest_round=latest_round,
            total_funding_usd=7_500_000,
            funding_stage="Seed",
            confidence=0.75,
            reasoning="Funding round confirmed via press release.",
            sources=["https://news.example.com/funding"],
        )


class DummyProfileAgent:
    """Test double that returns structured profile intelligence."""

    def profile(self, *args, **kwargs) -> CompanyProfileIntel:
        """Return canned profile details."""
        return CompanyProfileIntel(
            products=["Analytics Platform"],
            target_customers=["Series A startups"],
            business_model="B2B SaaS",
            headcount_range="11-50",
            confidence=0.6,
            reasoning="Product page highlights analytics suite for startups.",
            sources=["https://example.com/product"],
        )


def test_pipeline_orchestrates_agents_and_updates_org():
    """Pipeline should call each agent, mutate org, and return bundled outputs."""
    pipeline = LLMResearchPipeline(
        identity_agent=DummyIdentityAgent(),
        funding_agent=DummyFundingAgent(),
        profile_agent=DummyProfileAgent(),
    )

    org = {
        "callsign": "test-co",
        "dba": "Test Company",
        "domain_root": "old.com",
        "website": "https://old.com",
        "owners": ["Alice Founder"],
    }

    bundle = pipeline.run(org, deterministic_hints={"candidate_domains": ["example.com"]})

    assert bundle.identity.current_domain == "example.com"
    assert org["domain_root"] == "example.com"  # org mutated with new identity
    assert bundle.funding.funding_stage == "Seed"
    assert bundle.funding.total_funding_usd == 7_500_000
    assert bundle.profile is not None
    assert bundle.profile.products == ["Analytics Platform"]
    assert bundle.notes["deterministic_hints_used"] is True


def test_company_profile_intel_to_dict():
    """`CompanyProfileIntel.to_dict` should surface primary fields without mutation."""
    profile = CompanyProfileIntel(
        products=["API"],
        target_customers=["Fintech companies"],
        value_proposition="Realtime risk scoring",
        business_model="Usage-based API",
        revenue_model="Per-call billing",
        go_to_market="Self-serve with enterprise assist",
        headcount_range="51-100",
        headquarters="New York, NY",
        notable_customers=["Stripe"],
        differentiation="Speed + accuracy",
        open_questions=["Confirm pricing tiers"],
        confidence=0.55,
        reasoning="Corroborated via press kit.",
        sources=["https://example.com/press"],
    )

    payload = profile.to_dict()
    assert payload["products"] == ["API"]
    assert payload["confidence"] == 0.55
    assert payload["differentiation"] == "Speed + accuracy"
