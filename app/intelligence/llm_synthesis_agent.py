"""
LLM-powered dossier synthesis agent.

This module uses GPT-5 to generate comprehensive company dossiers
that answer consistent questions regardless of confidence level,
adapting tone and depth based on available information.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


@dataclass
class CompanyDossier:
    """Structured company dossier output."""

    markdown_content: str
    confidence_note: str
    next_steps: List[str]
    raw_response: str = ""


class LLMSynthesisAgent:
    """Agent that uses LLM to synthesize company intelligence into actionable dossiers."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.3):
        """
        Initialize the synthesis agent.

        Args:
            model: OpenAI model to use (defaults to gpt-5 for better reasoning)
            temperature: Sampling temperature (slightly higher for natural writing)
        """
        if OpenAI is None:
            raise ImportError("openai package required for LLMSynthesisAgent")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable required")

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_LLM_SYNTHESIS_MODEL", "gpt-5")
        self.temperature = temperature

    def generate_dossier(
        self,
        identity: Dict[str, Any],
        funding: Dict[str, Any],
        profile: Optional[Dict[str, Any]] = None,
        news_items: Optional[List[Dict[str, Any]]] = None,
        people_background: Optional[List[Dict[str, Any]]] = None,
        dba: Optional[str] = None,
        owners: Optional[List[str]] = None,
    ) -> CompanyDossier:
        """
        Generate a comprehensive company dossier.

        Args:
            identity: CompanyIdentity.to_dict() output
            funding: FundingIntelligence.to_dict() output
            news_items: Recent news/press mentions
            people_background: Background on founders/team
            dba: Company name
            owners: List of owners/founders

        Returns:
            CompanyDossier with markdown-formatted intelligence brief
        """
        logger.info(
            "dossier_synthesis_started",
            dba=dba,
            identity_confidence=identity.get("confidence", 0),
            funding_confidence=funding.get("confidence", 0),
        )

        prompt = self._build_prompt(
            identity=identity,
            funding=funding,
            profile=profile or {},
            news_items=news_items or [],
            people_background=people_background or [],
            dba=dba,
            owners=owners or [],
        )

        try:
            response = self.client.responses.create(
                model=self.model,
                input=prompt,
                temperature=self.temperature,
            )

            content = getattr(response, "output_text", None)
            if not content:
                raise ValueError("Empty response payload from synthesis agent")

            dossier = self._parse_response(content)

            logger.info("dossier_synthesis_completed", dba=dba)

            return dossier

        except Exception as e:
            logger.error("dossier_synthesis_failed", dba=dba, error=str(e))
            # Return basic fallback dossier
            return CompanyDossier(
                markdown_content=(
                    f"# {dba or 'Unknown Company'}\n\n"
                    f"Unable to generate dossier due to error: {str(e)}"
                ),
                confidence_note="Error during synthesis",
                next_steps=["Retry dossier generation", "Manual review required"],
            )

    def _build_prompt(
        self,
        identity: Dict[str, Any],
        funding: Dict[str, Any],
        profile: Dict[str, Any],
        news_items: List[Dict[str, Any]],
        people_background: List[Dict[str, Any]],
        dba: Optional[str],
        owners: List[str],
    ) -> str:
        """Build the LLM prompt for dossier synthesis."""
        input_data = {
            "identity": identity,
            "funding": funding,
            "profile": profile,
            "news_items": news_items[:8] if news_items else [],  # Limit for context
            "people_background": people_background[:5] if people_background else [],
            "dba": dba,
            "owners": owners,
        }

        return f"""You are writing an intelligence brief for a venture capital relationship manager
about a portfolio company.

COMPANY DATA:
{json.dumps(input_data, indent=2)}

TASK:
Write a comprehensive company dossier that attempts to answer ALL of the following questions,
regardless of confidence level. The dossier should consist of FACTS about the business, not
strategy or recommendations.

STRUCTURE (use these exact section headers):

## Company Identity
- What is the current name and online presence of this company?
- If there's uncertainty about identity, explain what we know and don't know
- If company is in stealth mode, focus on what we can confirm

## Company Overview
**Business Model & Stage:**
- What stage is this company at? (concept, pre-launch, early-stage, growth, etc.)
- What is their business model? (if known) Use the structured profile hints as leads
  and verify with sources.
- If unknown, say "Unable to confirm business model" and explain why

**Product & Market:**
- What do they sell? (product/service description). Leverage the profile data but confirm via
  cited evidence.
- Who do they sell to? (target customers/market)
- If unknown, say "Product details not publicly available" and note what limited info exists

**Team:**
- Who are the beneficial owners/founders?
- What are their relevant backgrounds? (if people_background data available)
- If limited info: provide what we know about the owners from the input data

## Funding
- Latest funding round (amount, type, date, investors)
- Total funding to date (if known)
- Funding stage assessment
- If no public funding found, say "No public funding information available" and note if
  bootstrapped/stealth/unknown

## Recent Activity
- Recent news, press mentions, product launches (from news_items)
- If no recent activity: say "No recent public activity found" and note last known update
- For stealth companies: note "Company in stealth mode, minimal public activity expected"

## Intelligence Quality Note
- Explain confidence level in this dossier (based on identity and funding confidence scores)
- What specific pieces of information are uncertain or missing?
- What makes this assessment reliable or unreliable?

## What Would Improve Intelligence
- Specific, actionable next steps to gather better information
- Examples: "Monitor founder LinkedIn for launch announcement", "Check company blog monthly for
  updates", "Request updated domain from relationship manager"
- List 2-4 concrete steps

IMPORTANT INSTRUCTIONS:
- Stick to FACTS found in the data - do not suggest strategy or recommendations beyond
  intelligence gathering
    - If you're unsure about something, explicitly say "Unable to confirm" or
      "Information not available"
- Do not make up information to fill gaps - acknowledge what you don't know
- Treat the structured profile hints as leads; only include items you can back up with cited
  evidence or clearly flag as unconfirmed.
- It's OK to have short sections if information is limited (better than speculation)
- For stealth/low-confidence companies, provide information about owners if that's all we have
- Write in a professional but concise tone (200-400 words total depending on available information)
- Use markdown formatting for sections and emphasis

Return ONLY the markdown-formatted dossier (no JSON, no preamble)."""

    def _parse_response(self, content: str) -> CompanyDossier:
        """Parse LLM markdown response into CompanyDossier object."""
        # Extract specific sections for structured access
        lines = content.split("\n")
        confidence_note = ""
        next_steps = []

        in_confidence_section = False
        in_next_steps_section = False

        for line in lines:
            if "## Intelligence Quality Note" in line:
                in_confidence_section = True
                in_next_steps_section = False
                continue
            elif "## What Would Improve Intelligence" in line:
                in_confidence_section = False
                in_next_steps_section = True
                continue
            elif line.startswith("##"):
                in_confidence_section = False
                in_next_steps_section = False
                continue

            if in_confidence_section and line.strip():
                confidence_note += line.strip() + " "
            elif in_next_steps_section and line.strip():
                # Extract bullet points or numbered items
                cleaned = line.strip().lstrip("-*0123456789. ")
                if cleaned:
                    next_steps.append(cleaned)

        return CompanyDossier(
            markdown_content=content,
            confidence_note=confidence_note.strip(),
            next_steps=next_steps,
            raw_response=content,
        )
