"""
LLM-powered profile enrichment agent.

Complements the identity and funding tiers by extracting product,
customer, and go-to-market details so dossiers can populate the
remaining query sections in a structured way.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, Optional

import structlog

from app.intelligence.llm_funding_agent import FundingIntelligence
from app.intelligence.llm_identity_agent import CompanyIdentity
from app.intelligence.research_models import CompanyProfileIntel, CompanyResearchContext

logger = structlog.get_logger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None


class LLMProfileAgent:
    """Agent that uses an LLM to reason about product, market, and GTM details."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.25) -> None:
        """Initialise the profile agent with the preferred OpenAI model."""
        if OpenAI is None:
            raise ImportError("openai package required for LLMProfileAgent")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable required")

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_LLM_PROFILE_MODEL", "gpt-5-mini")
        self.temperature = temperature

    def profile(
        self,
        context: CompanyResearchContext,
        identity: CompanyIdentity,
        funding: Optional[FundingIntelligence] = None,
        deterministic_hints: Optional[Dict[str, Any]] = None,
    ) -> CompanyProfileIntel:
        """Derive structured product/market intelligence for the company."""
        logger.info("profile_research_started", callsign=context.callsign, dba=context.dba)

        prompt = self._build_prompt(
            context=context,
            identity=identity,
            funding=funding,
            deterministic_hints=deterministic_hints,
        )

        try:
            response = self.client.responses.create(
                model=self.model,
                input=prompt,
                tools=[{"type": "web_search"}],
                response_format={"type": "json_object"},
                temperature=self.temperature,
            )

            payload = getattr(response, "output_text", None)
            if not payload:
                raise ValueError("Empty response payload from profile agent")

            data = json.loads(payload)
            intel = self._parse_response(data)

            logger.info(
                "profile_research_completed",
                callsign=context.callsign,
                confidence=intel.confidence,
                products=len(intel.products),
            )
            return intel

        except Exception as exc:  # noqa: BLE001
            logger.error("profile_research_failed", callsign=context.callsign, error=str(exc))
            return CompanyProfileIntel(
                confidence=0.0,
                reasoning=f"Profile research failed: {exc}",
                raw_response={"error": str(exc)},
            )

    def _build_prompt(
        self,
        context: CompanyResearchContext,
        identity: CompanyIdentity,
        funding: Optional[FundingIntelligence],
        deterministic_hints: Optional[Dict[str, Any]],
    ) -> str:
        input_data: Dict[str, Any] = {
            "research_context": asdict(context),
            "identity": identity.to_dict(),
            "identity_status": identity.status,
            "identity_confidence": identity.confidence,
            "identity_reasoning": identity.reasoning,
        }

        if funding:
            input_data["funding"] = funding.to_dict()
            input_data["funding_confidence"] = funding.confidence
            input_data["funding_reasoning"] = funding.reasoning

        if deterministic_hints:
            input_data["deterministic_hints"] = deterministic_hints

        return f"""You are compiling an internal profile of a venture-backed startup.

INPUT DATA:
{json.dumps(input_data, indent=2)}

TASK:
Using web search together with the provided context, fill in the startup's product,
customers, and go-to-market details.
Only include facts that are supported by credible evidence discovered during your reasoning.

Return ONLY this structured JSON (no markdown, no extra text):
{{
  "products": ["list the company's key products or platforms"],
  "target_customers": ["primary customer segments or industries"],
  "value_proposition": "one or two sentence description, or null if unknown",
  "business_model": "B2B SaaS, marketplace, usage-based API, or null if unclear",
  "revenue_model": "subscription, per-seat, transaction fee, or null",
  "go_to_market": "sales motion summary (self-serve, enterprise sales,
  channel partners, etc.) or null",
  "headcount_range": "e.g. '11-50', '51-100', or null if unavailable",
  "headquarters": "City, Region if known",
  "notable_customers": ["list of public customer logos if confirmed"],
  "differentiation": "brief statement of what makes them stand out, or null",
  "open_questions": ["list of follow-up questions or gaps worth investigating"],
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentences summarising evidence and uncertainties",
  "sources": ["URL1", "URL2", "URL3"]
}}

GUIDELINES:
 - Treat CSV/Notion context as hints, not truth. Verify everything through search.
 - Prefer official sources (company site, press releases) or credible press.
 - If you cannot confirm an item, leave it null and note the uncertainty in reasoning.
 - Stealth companies may have minimal data; focus on what can be verified.
"""

    def _parse_response(self, data: Dict[str, Any]) -> CompanyProfileIntel:
        products = [p for p in data.get("products", []) if isinstance(p, str)]
        target_customers = [c for c in data.get("target_customers", []) if isinstance(c, str)]
        notable_customers = [c for c in data.get("notable_customers", []) if isinstance(c, str)]
        open_questions = [q for q in data.get("open_questions", []) if isinstance(q, str)]
        sources = [s for s in data.get("sources", []) if isinstance(s, str)]

        confidence_raw = data.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0

        return CompanyProfileIntel(
            products=products,
            target_customers=target_customers,
            value_proposition=data.get("value_proposition"),
            business_model=data.get("business_model"),
            revenue_model=data.get("revenue_model"),
            go_to_market=data.get("go_to_market"),
            headcount_range=data.get("headcount_range"),
            headquarters=data.get("headquarters"),
            notable_customers=notable_customers,
            differentiation=data.get("differentiation"),
            open_questions=open_questions,
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            sources=sources,
            raw_response=data,
        )
