"""
LLM-powered company identity resolution agent.

This module uses GPT-5-mini with web search to intelligently determine
a company's current online identity, handling edge cases like:
- Domain redirects/changes
- Stealth companies (pre-launch)
- Inactive/personal domains
- Social profile fallback (LinkedIn, Twitter)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from app.intelligence.research_models import CompanyResearchContext

logger = structlog.get_logger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


@dataclass
class CompanyIdentity:
    """Structured company identity returned by the LLM agent."""

    # Primary identity
    current_domain: Optional[str] = None
    current_website: Optional[str] = None
    status: str = "unknown"  # active|redirect|inactive|stealth|defunct
    redirect_from: Optional[str] = None
    confidence: float = 0.0

    # Alternative identifiers
    company_linkedin: Optional[str] = None
    founder_linkedin_urls: List[str] = field(default_factory=list)
    twitter_handle: Optional[str] = None
    crunchbase_url: Optional[str] = None

    # Context
    reasoning: str = ""
    sources: List[str] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for downstream use."""
        return {
            "current_domain": self.current_domain,
            "current_website": self.current_website,
            "status": self.status,
            "redirect_from": self.redirect_from,
            "confidence": self.confidence,
            "company_linkedin": self.company_linkedin,
            "founder_linkedin_urls": self.founder_linkedin_urls,
            "twitter_handle": self.twitter_handle,
            "crunchbase_url": self.crunchbase_url,
            "reasoning": self.reasoning,
            "sources": self.sources,
        }


class LLMIdentityAgent:
    """Agent that uses LLM with web search to resolve company identity."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.2):
        """
        Initialize the identity agent.

        Args:
            model: OpenAI model to use (defaults to gpt-5-mini)
            temperature: Sampling temperature (lower = more factual)
        """
        if OpenAI is None:
            raise ImportError("openai package required for LLMIdentityAgent")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable required")

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_LLM_IDENTITY_MODEL", "gpt-5-mini")
        self.temperature = temperature

    def resolve(
        self,
        callsign: str,
        dba: str,
        owners: Optional[List[str]] = None,
        csv_domain: Optional[str] = None,
        csv_website: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        twitter_handle: Optional[str] = None,
        crunchbase_url: Optional[str] = None,
        context: Optional[str] = None,
        *,
        research_context: Optional[CompanyResearchContext] = None,
        deterministic_hints: Optional[Dict[str, Any]] = None,
        prior_findings: Optional[Dict[str, Any]] = None,
    ) -> CompanyIdentity:
        """
        Resolve company identity using LLM with web search.

        Args:
            callsign: Internal identifier for the company
            dba: Company legal/brand name
            owners: List of beneficial owners/founders
            csv_domain: Domain from CSV (may be outdated/incorrect)
            csv_website: Website from CSV (may be outdated/incorrect)
            linkedin_url: Company or founder LinkedIn URL
            twitter_handle: Company or founder Twitter handle
            crunchbase_url: Crunchbase profile URL
            context: Additional context about the company

        Returns:
            CompanyIdentity with current online identity and confidence score
        """
        logger.info(
            "identity_resolution_started",
            callsign=callsign,
            dba=dba,
            has_csv_domain=bool(csv_domain),
        )

        try:
            prompt = self._build_prompt(
                callsign=callsign,
                dba=dba,
                owners=owners or [],
                csv_domain=csv_domain,
                csv_website=csv_website,
                linkedin_url=linkedin_url,
                twitter_handle=twitter_handle,
                crunchbase_url=crunchbase_url,
                context=context,
                research_context=research_context,
                deterministic_hints=deterministic_hints,
                prior_findings=prior_findings,
            )

            model_name = (self.model or "").strip()
            tools = [{"type": "web_search"}]
            request_kwargs = {
                "model": model_name,
                "input": prompt,
                "tools": tools,
                "tool_choice": {
                    "type": "allowed_tools",
                    "mode": "required",
                    "tools": tools,
                },
            }
            if self.temperature is not None and not model_name.lower().startswith("gpt-5"):
                request_kwargs["temperature"] = self.temperature

            response = self.client.responses.create(**request_kwargs)

            payload = getattr(response, "output_text", None)
            if not payload:
                raise ValueError("Empty response payload from identity agent")

            result = json.loads(payload)
            identity = self._parse_response(result)

            logger.info(
                "identity_resolution_completed",
                callsign=callsign,
                status=identity.status,
                confidence=identity.confidence,
                current_domain=identity.current_domain,
            )

            return identity

        except Exception as e:
            logger.error("identity_resolution_failed", callsign=callsign, error=str(e))
            # Return low-confidence identity with error info
            return CompanyIdentity(
                status="error",
                confidence=0.0,
                reasoning=f"Identity resolution failed: {str(e)}",
            )

    def _build_prompt(
        self,
        callsign: str,
        dba: str,
        owners: List[str],
        csv_domain: Optional[str],
        csv_website: Optional[str],
        linkedin_url: Optional[str],
        twitter_handle: Optional[str],
        crunchbase_url: Optional[str],
        context: Optional[str],
        research_context: Optional[CompanyResearchContext],
        deterministic_hints: Optional[Dict[str, Any]],
        prior_findings: Optional[Dict[str, Any]],
    ) -> str:
        """Build the LLM prompt for identity resolution."""
        input_data: Dict[str, Any] = {
            "callsign": callsign,
            "dba": dba,
            "beneficial_owners": owners,
            "csv_domain": csv_domain,
            "csv_website": csv_website,
            "linkedin_url": linkedin_url,
            "twitter_handle": twitter_handle,
            "crunchbase_url": crunchbase_url,
            "context": context or "Portfolio company, likely early-stage startup",
        }

        if research_context:
            input_data["research_context"] = asdict(research_context)

        if deterministic_hints:
            input_data["deterministic_hints"] = deterministic_hints

        if prior_findings:
            input_data["prior_findings"] = prior_findings

        return f"""You are a company intelligence analyst. Given this portfolio company profile,
determine their current online identity.

COMPANY PROFILE:
{json.dumps(input_data, indent=2)}

TASK:
1. Verify if the provided domain/website is still active and belongs to this company
   - If domain redirects, follow to the current domain
   - If domain is inactive/parked, note this
   - If domain appears personal/unrelated to the company,
     flag it and search for the real company site
   - Use your best judgement: if the CSV data seems incorrect or contradictory,
     find the correct information

2. If domain is missing, inactive, or questionable, use alternative discovery:
   - Search for "{{dba}}" + founder names
   - Check founder LinkedIn profiles for company affiliation and company website
   - Look for recent press mentions, funding announcements
   - Check Twitter/X for company/founder activity
   - Search Crunchbase, AngelList for company listings

3. For stealth companies (no website yet):
   - Identify as stealth based on founder profiles saying "Stealth" or no company listed
   - Provide founder social URLs as primary identifiers
   - Note expected launch timeline if mentioned anywhere

4. IMPORTANT INSTRUCTIONS:
   - Do not provide information unless you have found actual evidence
   - If you're unsure about something, say "Unable to confirm" rather than speculating
   - If the provided parameters are too constraining or seem incorrect,
     use your best judgement to find the truth
   - Do not make up information just to provide an answer - it's better to return
     low confidence with reasoning

5. Return ONLY this structured JSON (no markdown, no explanations outside JSON):
{{
  "identity": {{
    "current_domain": "string or null",
    "current_website": "string or null (full URL with https://)",
    "status": "active|redirect|inactive|stealth|defunct",
    "redirect_from": "string or null (if domain changed)",
    "confidence": 0.0-1.0
  }},
  "alternative_identifiers": {{
    "company_linkedin": "URL or null",
    "founder_linkedin_urls": ["URL1", "URL2"],
    "twitter_handle": "@handle or null",
    "crunchbase_url": "URL or null"
  }},
  "reasoning": "Explain your conclusion in 2-3 sentences. Be specific about what you found
  and what's uncertain.",
  "sources": ["URL1", "URL2", "URL3"]
}}

CONFIDENCE GUIDANCE:
- 0.9-1.0: Domain verified, matches company name, active site with relevant content
- 0.7-0.8: Domain works, strong evidence it's correct (founder profiles link to it,
  press mentions)
- 0.5-0.6: Multiple signals point to this identity but some uncertainty remains
- 0.3-0.4: Limited information, relying on social profiles or weak signals
- 0.0-0.2: Very uncertain, conflicting information, or unable to find company

IMPORTANT:
- For stealth companies, confidence can still be 0.7+ if founder profiles clearly
  confirm the company exists
- If domain redirected, provide both old and new in appropriate fields
- If you find the company has a new name/domain, explain in reasoning
- Better to return "stealth" with founder info than guess wrong domain"""

    def _parse_response(self, result: Dict[str, Any]) -> CompanyIdentity:
        """Parse LLM JSON response into CompanyIdentity object."""
        identity_data = result.get("identity", {})
        alt_ids = result.get("alternative_identifiers", {})

        return CompanyIdentity(
            current_domain=identity_data.get("current_domain"),
            current_website=identity_data.get("current_website"),
            status=identity_data.get("status", "unknown"),
            redirect_from=identity_data.get("redirect_from"),
            confidence=float(identity_data.get("confidence", 0.0)),
            company_linkedin=alt_ids.get("company_linkedin"),
            founder_linkedin_urls=alt_ids.get("founder_linkedin_urls", []),
            twitter_handle=alt_ids.get("twitter_handle"),
            crunchbase_url=alt_ids.get("crunchbase_url"),
            reasoning=result.get("reasoning", ""),
            sources=result.get("sources", []),
            raw_response=result,
        )
