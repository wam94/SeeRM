"""
LLM-powered funding intelligence agent.

This module uses GPT-5-mini with web search to research company funding,
handling various scenarios:
- Public funding announcements
- Stealth/undisclosed rounds
- Bootstrapped companies
- Crunchbase data validation
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from app.intelligence.llm_identity_agent import CompanyIdentity
from app.intelligence.research_models import CompanyResearchContext

logger = structlog.get_logger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


@dataclass
class FundingRound:
    """Information about a single funding round."""

    amount_usd: Optional[float] = None
    round_type: Optional[str] = None
    announced_date: Optional[str] = None
    lead_investors: List[str] = field(default_factory=list)
    participants: List[str] = field(default_factory=list)


@dataclass
class FundingIntelligence:
    """Structured funding intelligence returned by the LLM agent."""

    latest_round: Optional[FundingRound] = None
    total_funding_usd: Optional[float] = None
    funding_stage: str = "Unknown"  # Pre-seed|Seed|Series A|B|C|...|Bootstrapped|Unknown
    confidence: float = 0.0
    reasoning: str = ""
    sources: List[str] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for downstream use."""
        result = {
            "funding_stage": self.funding_stage,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "sources": self.sources,
        }

        if self.latest_round:
            result["latest_amount_usd"] = self.latest_round.amount_usd
            result["latest_round_type"] = self.latest_round.round_type
            result["latest_funding_date"] = self.latest_round.announced_date
            result["latest_investors"] = (
                self.latest_round.lead_investors + self.latest_round.participants
            )[:5]

        if self.total_funding_usd:
            result["total_funding_usd"] = self.total_funding_usd

        return result


class LLMFundingAgent:
    """Agent that uses LLM with web search to research company funding."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.2):
        """
        Initialize the funding agent.

        Args:
            model: OpenAI model to use (defaults to gpt-5-mini)
            temperature: Sampling temperature (lower = more factual)
        """
        if OpenAI is None:
            raise ImportError("openai package required for LLMFundingAgent")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable required")

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_LLM_FUNDING_MODEL", "gpt-5-mini")
        self.temperature = temperature

    def research(
        self,
        dba: str,
        owners: Optional[List[str]] = None,
        current_domain: Optional[str] = None,
        current_website: Optional[str] = None,
        company_linkedin: Optional[str] = None,
        identity_status: str = "active",
        identity_confidence: float = 1.0,
        *,
        identity: Optional[CompanyIdentity] = None,
        research_context: Optional[CompanyResearchContext] = None,
        deterministic_hints: Optional[Dict[str, Any]] = None,
        prior_findings: Optional[Dict[str, Any]] = None,
    ) -> FundingIntelligence:
        """
        Research company funding using LLM with web search.

        Args:
            dba: Company legal/brand name
            owners: List of beneficial owners/founders
            current_domain: Current active domain (if known)
            current_website: Current website URL (if known)
            company_linkedin: Company LinkedIn URL
            identity_status: Company identity status (active|stealth|etc)
            identity_confidence: Confidence in company identity (0.0-1.0)

        Returns:
            FundingIntelligence with funding data and confidence score
        """
        logger.info(
            "funding_research_started",
            dba=dba,
            identity_status=identity_status,
            identity_confidence=identity_confidence,
        )

        # Skip funding research for very low confidence identities
        if identity_confidence < 0.3:
            logger.info("funding_research_skipped", reason="low_identity_confidence")
            return FundingIntelligence(
                funding_stage="Unknown",
                confidence=0.0,
                reasoning="Identity confidence too low to research funding reliably",
            )

        try:
            prompt = self._build_prompt(
                dba=dba,
                owners=owners or [],
                current_domain=current_domain,
                current_website=current_website,
                company_linkedin=company_linkedin,
                identity_status=identity_status,
                identity_confidence=identity_confidence,
                identity_snapshot=identity,
                research_context=research_context,
                deterministic_hints=deterministic_hints,
                prior_findings=prior_findings,
            )

            model_name = (self.model or "").strip()
            request_kwargs = {
                "model": model_name,
                "input": prompt,
                "tools": [{"type": "web_search"}],
                "text": {"format": {"type": "json_object"}},
            }
            if self.temperature is not None and model_name.lower() != "gpt-5":
                request_kwargs["temperature"] = self.temperature

            response = self.client.responses.create(**request_kwargs)

            payload = getattr(response, "output_text", None)
            if not payload:
                raise ValueError("Empty response payload from funding agent")

            result = json.loads(payload)
            funding = self._parse_response(result)

            logger.info(
                "funding_research_completed",
                dba=dba,
                stage=funding.funding_stage,
                confidence=funding.confidence,
                has_latest_round=funding.latest_round is not None,
            )

            return funding

        except Exception as e:
            logger.error("funding_research_failed", dba=dba, error=str(e))
            return FundingIntelligence(
                funding_stage="Unknown",
                confidence=0.0,
                reasoning=f"Funding research failed: {str(e)}",
            )

    def _build_prompt(
        self,
        dba: str,
        owners: List[str],
        current_domain: Optional[str],
        current_website: Optional[str],
        company_linkedin: Optional[str],
        identity_status: str,
        identity_confidence: float,
        identity_snapshot: Optional[CompanyIdentity],
        research_context: Optional[CompanyResearchContext],
        deterministic_hints: Optional[Dict[str, Any]],
        prior_findings: Optional[Dict[str, Any]],
    ) -> str:
        """Build the LLM prompt for funding research."""
        input_data: Dict[str, Any] = {
            "dba": dba,
            "beneficial_owners": owners,
            "current_domain": current_domain,
            "current_website": current_website,
            "company_linkedin": company_linkedin,
            "identity_status": identity_status,
            "identity_confidence": identity_confidence,
        }

        if identity_snapshot:
            input_data["identity_snapshot"] = identity_snapshot.to_dict()
            input_data["identity_snapshot"]["status"] = identity_snapshot.status
            input_data["identity_snapshot"]["reasoning"] = identity_snapshot.reasoning

        if research_context:
            input_data["research_context"] = asdict(research_context)

        if deterministic_hints:
            input_data["deterministic_hints"] = deterministic_hints

        if prior_findings:
            input_data["prior_findings"] = prior_findings

        return f"""You are a venture capital analyst researching funding for a portfolio company.

COMPANY IDENTITY:
{json.dumps(input_data, indent=2)}

TASK:
1. Find the most recent funding round announcement
   - Search "{{dba}}" + "raises" OR "funding" OR "seed" OR "series"
   - Check company blog/press section if website available
   - Search founder LinkedIn activity for funding announcements
   - Check Crunchbase, PitchBook for funding history
   - Look for press releases on BusinessWire, PRNewswire, TechCrunch

2. Extract structured funding data:
   - Amount (in USD, as a number)
   - Round type (Pre-seed, Seed, Series A, etc.)
   - Date announced (YYYY-MM-DD format)
   - Lead investors (who led the round)
   - Participating investors
   - Total funding to date (if mentioned)

3. For stealth/pre-launch companies:
   - Look for "pre-seed" or "angel" rounds even if not publicly announced
   - Check if founders mention raising in LinkedIn posts
   - Note if company is "bootstrapped" or "pre-funding"

4. Distinguish between scenarios:
   - "No public information found" = unable to find funding data
   - "Bootstrapped" = evidence company is self-funded
   - "Stealth/Not Disclosed" = company exists but funding not public

5. IMPORTANT INSTRUCTIONS:
   - Do not provide information unless you have found actual evidence
   - If you're unsure about funding amount, say "Unable to confirm" rather than guessing
   - If the provided parameters are too constraining, use your best judgement
   - Do not make up funding data just to provide an answer

6. Return ONLY this structured JSON (no markdown, no explanations outside JSON):
{{
  "latest_round": {{
    "amount_usd": number or null,
    "round_type": "string or null",
    "announced_date": "YYYY-MM-DD or null",
    "lead_investors": ["investor1", "investor2"],
    "participants": ["investor3", "investor4"]
  }},
  "total_funding_usd": number or null,
  "funding_stage": "Pre-seed|Seed|Series A|Series B|Series C|Series D+|Bootstrapped|Unknown",
  "confidence": 0.0-1.0,
  "reasoning": "Explain your findings in 2-3 sentences. Be specific about what you found and
  what's uncertain.",
  "sources": ["URL1", "URL2", "URL3"]
}}

CONFIDENCE GUIDANCE:
- 0.9-1.0: Official press release or company announcement with full details
- 0.7-0.8: Multiple credible sources (TechCrunch, Crunchbase) confirm funding
- 0.5-0.6: Some evidence of funding but details incomplete or from single source
- 0.3-0.4: Indirect evidence (founder mentions, partial information)
- 0.0-0.2: No reliable information found or highly uncertain

IMPORTANT:
- If no public funding found, set stage to "Unknown" and confidence to 0.0-0.2
- For bootstrapped companies, provide evidence (founder statement, no funding history)
- For stealth, low confidence (0.3-0.5) is acceptable and honest
- Don't confuse "total funding" with "latest round amount"
- If you can't find a specific detail (like investor names), leave it null rather than guessing"""

    def _parse_response(self, result: Dict[str, Any]) -> FundingIntelligence:
        """Parse LLM JSON response into FundingIntelligence object."""
        latest_round_data = result.get("latest_round", {})
        latest_round = None

        if latest_round_data and any(
            latest_round_data.get(k)
            for k in ["amount_usd", "round_type", "announced_date", "lead_investors"]
        ):
            latest_round = FundingRound(
                amount_usd=latest_round_data.get("amount_usd"),
                round_type=latest_round_data.get("round_type"),
                announced_date=latest_round_data.get("announced_date"),
                lead_investors=latest_round_data.get("lead_investors", []),
                participants=latest_round_data.get("participants", []),
            )

        return FundingIntelligence(
            latest_round=latest_round,
            total_funding_usd=result.get("total_funding_usd"),
            funding_stage=result.get("funding_stage", "Unknown"),
            confidence=float(result.get("confidence", 0.0)),
            reasoning=result.get("reasoning", ""),
            sources=result.get("sources", []),
            raw_response=result,
        )
