"""Utilities for onboarding new companies with fresh dossiers."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import structlog

from app.core.models import Company, NotionPage
from app.data.notion_client import EnhancedNotionClient
from app.intelligence.llm_research_pipeline import LLMResearchPipeline
from app.intelligence.llm_synthesis_agent import LLMSynthesisAgent
from app.notion_client import replace_dossier_blocks

logger = structlog.get_logger(__name__)


class DossierOnboardingService:
    """Run the tiered LLM dossier workflow for newly discovered companies."""

    def __init__(
        self,
        notion_client: Optional[EnhancedNotionClient],
        companies_db_id: Optional[str],
        *,
        throttle_seconds: float = 0.35,
    ) -> None:
        """Create the onboarding service with optional Notion + rate limiting support."""
        self.notion_client = notion_client
        self.companies_db_id = companies_db_id
        self.throttle_seconds = throttle_seconds
        self._pipeline: Optional[LLMResearchPipeline] = None
        self._synthesiser: Optional[LLMSynthesisAgent] = None
        self._pipeline_error: Optional[str] = None
        self._synth_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """Return True when required dependencies are configured."""
        return bool(self.notion_client and self.companies_db_id)

    def _get_pipeline(self) -> LLMResearchPipeline:
        if self._pipeline_error:
            raise RuntimeError(self._pipeline_error)
        if self._pipeline is None:
            try:
                self._pipeline = LLMResearchPipeline()
            except Exception as exc:  # noqa: BLE001
                self._pipeline_error = str(exc)
                raise
        return self._pipeline

    def _get_synthesiser(self) -> LLMSynthesisAgent:
        if self._synth_error:
            raise RuntimeError(self._synth_error)
        if self._synthesiser is None:
            try:
                self._synthesiser = LLMSynthesisAgent()
            except Exception as exc:  # noqa: BLE001
                self._synth_error = str(exc)
                raise
        return self._synthesiser

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [str(value).strip()]

    def _build_org_payload(
        self,
        company: Company,
        enhanced_entry: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        enhanced = enhanced_entry or {}
        owners = company.beneficial_owners or []
        if not owners:
            owners = self._as_list(enhanced.get("owners"))

        aka_names_parts: List[str] = []
        if company.aka_names:
            aka_names_parts.extend(
                [name.strip() for name in str(company.aka_names).split(",") if name.strip()]
            )
        aka_names_parts.extend(self._as_list(enhanced.get("aka_names")))
        aka_names = ", ".join(sorted({name for name in aka_names_parts if name}))

        tags = enhanced.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]

        products = enhanced.get("products") or []
        if isinstance(products, str):
            products = [products]

        org: Dict[str, Any] = {
            "callsign": company.callsign,
            "dba": enhanced.get("company") or company.dba or company.callsign,
            "website": enhanced.get("website") or company.website,
            "domain_root": enhanced.get("domain") or company.domain_root,
            "aka_names": aka_names,
            "owners": owners,
            "industry_tags": ", ".join(sorted({tag for tag in tags if tag})),
            "products": products,
        }

        # Preserve existing CSV hints when available
        for optional_key in (
            "linkedin_url",
            "twitter_handle",
            "crunchbase_url",
            "blog_url",
            "rss_feeds",
        ):
            value = getattr(company, optional_key, None)
            if value:
                org[optional_key] = value

        return org

    def _upsert_company_page(self, org: Dict[str, Any]) -> Optional[NotionPage]:
        if not self.notion_client or not self.companies_db_id:
            return None

        notion_company = Company(
            callsign=org.get("callsign"),
            dba=org.get("dba"),
            website=org.get("website"),
            domain_root=org.get("domain_root"),
            beneficial_owners=org.get("owners") or [],
            needs_dossier=False,
        )
        page = self.notion_client.upsert_company_page(self.companies_db_id, notion_company)
        return page

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def generate_dossier(
        self,
        company: Company,
        enhanced_entry: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate and store a dossier, returning updated Notion metadata.

        Returns:
            Updated enhanced entry dict or None if generation failed/unavailable.
        """
        if not self.is_available():
            logger.debug("dossier_onboarding_unavailable", reason="notion_unconfigured")
            return None

        try:
            org = self._build_org_payload(company, enhanced_entry)

            pipeline = self._get_pipeline()
            bundle = pipeline.run(org)

            synth = self._get_synthesiser()
            dossier = synth.generate_dossier(
                identity=bundle.identity.to_dict() if bundle.identity else {},
                funding=bundle.funding.to_dict() if bundle.funding else {},
                profile=bundle.profile.to_dict() if bundle.profile else {},
                news_items=[],
                people_background=[],
                dba=org.get("dba"),
                owners=org.get("owners") or [],
            )

            page = self._upsert_company_page(org)
            if not page:
                logger.warning(
                    "dossier_onboarding_failed",
                    callsign=company.callsign,
                    reason="no_page",
                )
                return None

            try:
                replace_dossier_blocks(page.page_id, dossier.markdown_content)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dossier_markdown_write_failed",
                    callsign=company.callsign,
                    error=str(exc),
                )

            try:
                self.notion_client.set_needs_dossier(page.page_id, False)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "dossier_flag_update_failed",
                    callsign=company.callsign,
                    error=str(exc),
                )

            aliases = set()
            aliases.add(org.get("dba") or "")
            aliases.add(company.callsign)
            for alias in self._as_list(enhanced_entry.get("aliases") if enhanced_entry else []):
                aliases.add(alias)

            updated_entry = {
                "domain": org.get("domain_root"),
                "website": org.get("website"),
                "verified_domain": org.get("domain_root"),
                "latest_intel": (enhanced_entry.get("latest_intel") if enhanced_entry else None),
                "latest_intel_at": (
                    enhanced_entry.get("latest_intel_at") if enhanced_entry else None
                ),
                "page_id": page.page_id,
                "company": org.get("dba"),
                "owners": org.get("owners") or [],
                "aliases": [alias for alias in aliases if alias],
                "aka_names": self._as_list(org.get("aka_names")),
                "tags": self._as_list(org.get("industry_tags")),
                "products": org.get("products") or [],
                "needs_dossier": False,
                "primary_contact": (
                    enhanced_entry.get("primary_contact") if enhanced_entry else None
                ),
            }

            logger.info(
                "dossier_onboarding_completed",
                callsign=company.callsign,
                page_id=page.page_id,
                identity_confidence=getattr(bundle.identity, "confidence", None),
            )

            if self.throttle_seconds and self.throttle_seconds > 0:
                time.sleep(self.throttle_seconds)

            return updated_entry

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dossier_onboarding_failed",
                callsign=company.callsign,
                error=str(exc),
            )
            return None
