"""LLM-powered enrichment helpers for kickoff dossiers.

Provides a lightweight wrapper around the OpenAI Responses API with
web search enabled so we can resolve company domain and funding
information during ad-hoc dossier refreshes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from app.core.exceptions import OpenAIError

try:  # Optional dependency, only needed when flag is enabled
    from openai import OpenAI
except Exception:  # pragma: no cover - fallback when sdk missing
    OpenAI = None  # type: ignore[misc]

logger = structlog.get_logger(__name__)

# ---------------------------- Data models ----------------------------


@dataclass(slots=True)
class DomainIntel:
    """Structured domain resolution returned by the LLM."""

    domain_root: Optional[str] = None
    website: Optional[str] = None
    confidence: Optional[str] = None
    notes: Optional[str] = None


@dataclass(slots=True)
class FundingIntel:
    """Structured funding resolution returned by the LLM."""

    stage: Optional[str] = None
    latest_round: Optional[str] = None
    latest_amount_usd: Optional[float] = None
    lead_investors: List[str] = field(default_factory=list)
    summary: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Coerce the enrichment into our downstream dictionary format."""
        payload: Dict[str, Any] = {}
        if self.stage:
            payload["funding_stage"] = self.stage
        if self.latest_round:
            payload["latest_round_type"] = self.latest_round
        if self.latest_amount_usd is not None:
            payload["latest_amount_usd"] = int(self.latest_amount_usd)
        if self.lead_investors:
            payload["latest_investors"] = self.lead_investors
        if self.summary:
            payload["latest_funding_title"] = self.summary
        if self.notes:
            payload.setdefault("llm_notes", self.notes)
        return payload


@dataclass(slots=True)
class LLMCompanyIntel:
    """Aggregate enrichment bundle combining domain and funding output."""

    confidence: str
    domain: DomainIntel
    funding: FundingIntel
    sources: List[str] = field(default_factory=list)
    summary: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def funding_payload(self) -> Dict[str, Any]:
        """Return a funding dict compatible with dossier rendering."""
        payload = self.funding.to_dict()
        if self.sources:
            payload["llm_sources"] = self.sources
        payload["llm_confidence"] = self.confidence
        if self.summary:
            payload.setdefault("latest_funding_source", self.summary)
        return payload


# ---------------------------- OpenAI helpers ----------------------------


_RESPONSE_SCHEMA = {
    "name": "CompanyIntel",
    "schema": {
        "type": "object",
        "properties": {
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Model confidence in the overall findings.",
            },
            "domain": {
                "type": "object",
                "properties": {
                    "domain_root": {"type": "string"},
                    "website": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            "funding": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string"},
                    "latest_round": {"type": "string"},
                    "latest_amount_usd": {
                        "anyOf": [
                            {"type": "number"},
                            {"type": "string"},
                            {"type": "null"},
                        ]
                    },
                    "lead_investors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "summary": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Citation URLs backing the answer.",
            },
            "summary": {"type": "string"},
        },
        "required": ["confidence", "domain", "funding", "sources"],
        "additionalProperties": False,
    },
}


def _load_openai_client() -> OpenAI:
    """Initialise OpenAI client or raise an informative error."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIError("OPENAI_API_KEY is not configured")
    if OpenAI is None:
        raise OpenAIError("openai package is not available in this environment")
    return OpenAI(api_key=api_key)


def _default_model() -> str:
    return (
        os.getenv("OPENAI_LLM_INTEL_MODEL")
        or os.getenv("OPENAI_CHAT_MODEL_DOSSIER")
        or os.getenv("OPENAI_CHAT_MODEL")
        or "gpt-5-mini"
    ).strip()


def _default_temperature() -> Optional[float]:
    raw = (
        os.getenv("OPENAI_LLM_INTEL_TEMPERATURE")
        or os.getenv("OPENAI_TEMPERATURE_DOSSIER")
        or os.getenv("OPENAI_TEMPERATURE")
        or ""
    ).strip()
    if raw.lower() in {"", "auto", "none"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _build_prompt(org: Dict[str, Any]) -> str:
    """Format the user instructions fed to the LLM."""
    owners = ", ".join(org.get("owners") or []) or "Unknown"
    aka = org.get("aka_names") or ""
    info_lines = [
        "Using the following internal profile fields, determine the company's identity,",
        "confirm or update the canonical website (with domain root),",
        "and summarize the latest funding stage and lead investors.",
        ("Report the most recent funding round amount in USD " "as a pure number (no commas)."),
        (
            "If the exact figure is unavailable, provide the best published estimate "
            "and note it as such."
        ),
        (
            "When only total funding to date is known, supply that amount "
            "and explain the context in notes."
        ),
        "If information is ambiguous, say so and lower confidence.",
        "Always provide at least one source URL.",
        "",
        f"Internal identifier: {org.get('callsign') or 'Unknown'}",
        f"Company legal name: {org.get('dba') or 'Unknown'}",
        f"Known domain: {org.get('domain_root') or org.get('domain') or 'Unknown'}",
        f"Known website: {org.get('website') or 'Unknown'}",
        f"Owners / key contacts: {owners}",
    ]
    if aka:
        info_lines.append(f"Also known as: {aka}")
    info_lines.extend(
        [
            "Output strictly one JSON object matching this schema (no extra text):",
            "{",
            '  "confidence": "high|medium|low",',
            '  "domain": {"domain_root": string?, "website": string?, "notes": string?},',
            (
                '  "funding": {'
                '"stage": string?, '
                '"latest_round": string?, '
                '"latest_amount_usd": number?, '
                '"lead_investors": [string], '
                '"summary": string?, '
                '"notes": string?},'
            ),
            '  "sources": [string],',
            '  "summary": string?',
            "}",
            "Do not include explanations outside the JSON.",
        ]
    )
    return "\n".join(info_lines)


_AMOUNT_UNIT_MULTIPLIERS = {
    "k": 1_000.0,
    "thousand": 1_000.0,
    "m": 1_000_000.0,
    "mm": 1_000_000.0,
    "mn": 1_000_000.0,
    "million": 1_000_000.0,
    "millions": 1_000_000.0,
    "b": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "billion": 1_000_000_000.0,
    "billions": 1_000_000_000.0,
    "t": 1_000_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
    "trillions": 1_000_000_000_000.0,
}

_AMOUNT_REGEX = re.compile(
    (
        r"(?P<amount>\d+(?:[,\s]\d{3})*(?:\.\d+)?)"
        r"(?:\s*(?P<unit>"
        r"k|m|b|bn|mm|mn|thousand|million|millions|billion|billions|t|trillion|trillions"
        r")\b)?"
    ),
    re.IGNORECASE,
)

_NEGATIVE_AMOUNT_PATTERN = re.compile(
    r"\b(undisclosed|unknown|n/?a|not available|none|not disclosed)\b"
)


def _parse_amount(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        lowered = raw.lower()
        if _NEGATIVE_AMOUNT_PATTERN.search(lowered):
            return None

        cleaned = re.sub(r"[\$€£¥]", "", lowered)
        match = _AMOUNT_REGEX.search(cleaned)
        if not match:
            return None

        amount_token = match.group("amount")
        if not amount_token:
            return None

        normalized = amount_token.replace(",", "").replace(" ", "")
        try:
            amount = float(normalized)
        except ValueError:
            return None

        unit = match.group("unit") or ""
        unit = re.sub(r"[^a-z]", "", unit.lower())
        multiplier = _AMOUNT_UNIT_MULTIPLIERS.get(unit, 1.0)
        return amount * multiplier
    return None


_CACHE: Dict[str, LLMCompanyIntel] = {}


def _coerce_json_payload(text: Optional[str]) -> Dict[str, Any]:
    if not text:
        raise ValueError("Empty payload")

    candidate = text.strip()

    if candidate.startswith("```"):
        parts = candidate.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("{"):
                candidate = part
                break

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            snippet = match.group(0)
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        raise ValueError("Could not extract JSON from payload")


def resolve_company_intel(org: Dict[str, Any], *, force_refresh: bool = False) -> LLMCompanyIntel:
    """Resolve domain and funding using the OpenAI Responses API with web search."""
    cache_key = (org.get("callsign") or org.get("dba") or "").strip().lower()
    if cache_key and not force_refresh and cache_key in _CACHE:
        return _CACHE[cache_key]

    client = _load_openai_client()
    model = _default_model()
    temperature = _default_temperature()

    prompt = _build_prompt(org)

    try:
        tools = [{"type": "web_search"}]
        kwargs: Dict[str, Any] = {
            "model": model,
            "input": prompt,
            "tools": tools,
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": tools,
            },
        }
        if temperature is not None and not model.lower().startswith("gpt-5"):
            kwargs["temperature"] = temperature
        response = client.responses.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        error_payload = {"error": str(exc), "model": model}
        try:
            extra = getattr(exc, "response", None) or getattr(exc, "body", None)
            if extra:
                error_payload["details"] = str(extra)
        except Exception:  # pragma: no cover - defensive
            pass
        logger.warning(
            "OpenAI enrichment call failed", callsign=org.get("callsign"), payload=error_payload
        )
        raise OpenAIError("OpenAI Responses call failed", error_payload) from exc

    raw_text = getattr(response, "output_text", None)
    if not raw_text:
        raise OpenAIError("Empty response from OpenAI", {"model": model})

    try:
        data = _coerce_json_payload(raw_text)
    except ValueError as exc:  # noqa: BLE001
        raise OpenAIError("Failed to parse OpenAI JSON payload", {"payload": raw_text}) from exc

    confidence = str(data.get("confidence") or "unknown").lower()
    domain_payload = data.get("domain") or {}
    funding_payload = data.get("funding") or {}
    sources = [s for s in data.get("sources") or [] if isinstance(s, str) and s.strip()]

    domain = DomainIntel(
        domain_root=(domain_payload.get("domain_root") or None),
        website=(domain_payload.get("website") or None),
        confidence=confidence,
        notes=domain_payload.get("notes") or None,
    )

    funding = FundingIntel(
        stage=funding_payload.get("stage") or None,
        latest_round=funding_payload.get("latest_round") or None,
        latest_amount_usd=_parse_amount(funding_payload.get("latest_amount_usd")),
        lead_investors=[i for i in funding_payload.get("lead_investors") or [] if i],
        summary=funding_payload.get("summary") or None,
        notes=funding_payload.get("notes") or None,
    )

    result = LLMCompanyIntel(
        confidence=confidence,
        domain=domain,
        funding=funding,
        sources=sources,
        summary=data.get("summary") or None,
        raw=data,
    )

    if cache_key:
        _CACHE[cache_key] = result

    logger.info(
        "LLM enrichment completed",
        callsign=org.get("callsign"),
        confidence=confidence,
        domain=domain.domain_root or domain.website,
        sources=len(sources),
    )
    return result


__all__ = [
    "DomainIntel",
    "FundingIntel",
    "LLMCompanyIntel",
    "resolve_company_intel",
]
