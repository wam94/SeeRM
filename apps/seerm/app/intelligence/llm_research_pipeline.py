"""
Co-ordinates the tiered LLM research workflow (identity → funding → profile).

This module centralises orchestration so the baseline job and future
workflows can invoke a single entry point and receive structured results.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog

from app.intelligence.llm_funding_agent import FundingIntelligence, LLMFundingAgent
from app.intelligence.llm_identity_agent import CompanyIdentity, LLMIdentityAgent
from app.intelligence.llm_profile_agent import LLMProfileAgent
from app.intelligence.research_models import (
    CompanyProfileIntel,
    CompanyResearchContext,
    ResearchBundle,
)

logger = structlog.get_logger(__name__)


class LLMResearchPipeline:
    """Execute the multi-stage LLM workflow with shared context and hints."""

    def __init__(
        self,
        identity_agent: Optional[LLMIdentityAgent] = None,
        funding_agent: Optional[LLMFundingAgent] = None,
        profile_agent: Optional[LLMProfileAgent] = None,
    ) -> None:
        """Initialise the pipeline with optional pre-configured agents."""
        self.identity_agent = identity_agent or LLMIdentityAgent()
        self.funding_agent = funding_agent or LLMFundingAgent()
        self.profile_agent = profile_agent or LLMProfileAgent()

    def run(
        self,
        org: Dict[str, Any],
        *,
        deterministic_hints: Optional[Dict[str, Any]] = None,
        run_profile: bool = True,
    ) -> ResearchBundle:
        """
        Execute the tiered workflow, returning an aggregate bundle.

        Args:
            org: Canonical organisation dict (CSV/Notion derived).
            deterministic_hints: Optional deterministic evidence to pass as hints.
        """
        context = self._build_context(org)
        hints = deterministic_hints or {}

        identity = self._resolve_identity(org, context, hints)
        funding = self._research_funding(org, context, identity, hints)
        profile: Optional[CompanyProfileIntel] = None
        if run_profile:
            profile = self._profile_company(context, identity, funding, hints)

        return ResearchBundle(
            context=context,
            identity=identity,
            funding=funding,
            profile=profile,
            notes={"deterministic_hints_used": bool(hints)},
        )

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    def _build_context(self, org: Dict[str, Any]) -> CompanyResearchContext:
        return CompanyResearchContext(
            callsign=(org.get("callsign") or "").strip(),
            dba=(org.get("dba") or org.get("company") or org.get("callsign") or "").strip(),
            owners=[o.strip() for o in (org.get("owners") or []) if o],
            ingested_domain=(org.get("domain_root") or org.get("domain")),
            ingested_website=org.get("website"),
            alias_names=[a.strip() for a in (org.get("aka_names") or "").split(",") if a.strip()],
            tags=[t.strip() for t in (org.get("industry_tags") or "").split(",") if t.strip()],
            linkedin_url=org.get("linkedin_url"),
            twitter_handle=org.get("twitter_handle"),
            crunchbase_url=org.get("crunchbase_url"),
            supplementary_hints={
                "csv_row": {k: v for k, v in org.items() if k not in {"news_items", "people_bg"}},
            },
        )

    def _resolve_identity(
        self,
        org: Dict[str, Any],
        context: CompanyResearchContext,
        deterministic_hints: Dict[str, Any],
    ) -> CompanyIdentity:
        extra_hints = {
            "candidate_domains": deterministic_hints.get("candidate_domains") or [],
            "ingested_domain": context.ingested_domain,
            "ingested_website": context.ingested_website,
        }

        identity = self.identity_agent.resolve(
            callsign=context.callsign,
            dba=context.dba,
            owners=context.owners,
            csv_domain=context.ingested_domain,
            csv_website=context.ingested_website,
            linkedin_url=context.linkedin_url,
            twitter_handle=context.twitter_handle,
            crunchbase_url=context.crunchbase_url,
            context="Portfolio company, venture-backed or venture-scale",
            research_context=context,
            deterministic_hints=extra_hints,
            prior_findings=None,
        )

        # Update org dictionary with the latest domain info for downstream tasks
        if identity.current_domain:
            org["domain_root"] = identity.current_domain
            org["domain"] = identity.current_domain
        if identity.current_website:
            org["website"] = identity.current_website

        org["identity_confidence"] = identity.confidence
        org["identity_status"] = identity.status
        org["identity_sources"] = identity.sources
        org["identity_reasoning"] = identity.reasoning

        return identity

    def _research_funding(
        self,
        org: Dict[str, Any],
        context: CompanyResearchContext,
        identity: CompanyIdentity,
        deterministic_hints: Dict[str, Any],
    ) -> FundingIntelligence:
        prior = {"identity": identity.to_dict(), "identity_status": identity.status}

        funding = self.funding_agent.research(
            dba=context.dba,
            owners=context.owners,
            current_domain=identity.current_domain or context.ingested_domain,
            current_website=identity.current_website or context.ingested_website,
            company_linkedin=identity.company_linkedin or context.linkedin_url,
            identity_status=identity.status,
            identity_confidence=identity.confidence,
            identity=identity,
            research_context=context,
            deterministic_hints={
                "cached_funding": org.get("cached_funding_data"),
                "crunchbase_hint": deterministic_hints.get("crunchbase_hint"),
            },
            prior_findings=prior,
        )

        org["funding_confidence"] = funding.confidence
        org["funding_stage"] = funding.funding_stage
        org["funding_sources"] = funding.sources
        org["funding_reasoning"] = funding.reasoning

        return funding

    def _profile_company(
        self,
        context: CompanyResearchContext,
        identity: CompanyIdentity,
        funding,
        deterministic_hints: Dict[str, Any],
    ) -> Optional[CompanyProfileIntel]:
        if not identity or identity.confidence < 0.1:
            logger.info("profile_skipped", reason="identity_confidence_too_low")
            return None

        profile_hints = {
            "news_items": deterministic_hints.get("news_items") or [],
            "people_background": deterministic_hints.get("people_background") or [],
        }

        return self.profile_agent.profile(
            context=context,
            identity=identity,
            funding=funding,
            deterministic_hints=profile_hints,
        )
