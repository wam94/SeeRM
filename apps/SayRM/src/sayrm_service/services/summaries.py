"""Summary orchestration for SayRM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import structlog

from shared.core.notion_context import CompanyContext, NewsHighlight

from ..clients.internal_client import InternalUsageClient, InternalUsageSnapshot
from ..clients.llm_client import LLMClient
from ..clients.notion_client import ExternalContextClient
from ..store import DataStore

logger = structlog.get_logger(__name__)


@dataclass
class ExternalBrief:
    """Serialized external summary payload."""

    summary_id: int
    callsign: str
    company_name: Optional[str]
    product: str
    news: List[str]
    announcements: List[str]
    raw_text: str
    context_snapshot: dict
    created_at: datetime


@dataclass
class InternalBrief:
    """Serialized internal summary payload."""

    summary_id: int
    callsign: str
    notes: str
    snapshot: InternalUsageSnapshot
    created_at: datetime


class SummaryService:
    """Coordinates context fetching, LLM summarisation, and logging."""

    def __init__(
        self,
        *,
        notion_client: ExternalContextClient,
        internal_client: InternalUsageClient,
        llm_client: LLMClient,
        store: DataStore,
    ) -> None:
        """Wire summary dependencies for external + internal briefs."""
        self._notion = notion_client
        self._internal = internal_client
        self._llm = llm_client
        self._store = store

    @property
    def llm_model(self) -> str:
        """Expose the configured LLM model name."""
        return self._llm.model

    def fetch_company_context(self, callsign: str) -> CompanyContext:
        """Return the raw Notion context for a company or raise if missing."""
        company = self._notion.get_company(callsign)
        if not company:
            raise ValueError(f"No Notion context found for {callsign}")
        return company

    # ------------------------------------------------------------------ #
    # External
    # ------------------------------------------------------------------ #

    def build_external_brief(
        self,
        callsign: str,
        *,
        manual_highlights: Optional[List[str]] = None,
    ) -> ExternalBrief:
        """Build an external-facing brief from Notion context."""
        company = self.fetch_company_context(callsign)
        context_snapshot = _company_to_dict(company)

        prompt = self._compose_external_prompt(company, manual_highlights or [])
        system_msg = (
            "You help Will Mitchell scan company dossiers and turn them into "
            "quick reference cards. Write concise, factual bullets with no "
            "marketing speak and no emojis."
        )
        try:
            response = self._llm.run_chat(
                [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            parsed = self._parse_external_response(response)
        except Exception as exc:  # pragma: no cover - defensive fallback
            # If the LLM call fails (network / model issues), fall back to a
            # simple summary based on the Notion context so the endpoint still
            # returns something useful instead of a 500.
            logger.error(
                "External brief LLM failed; using fallback summary",
                error=str(exc),
                callsign=callsign,
            )
            parsed = {
                "product": company.summary or "No summary recorded.",
                "news": [item.title for item in company.news_highlights],
                "announcements": [],
            }

        body = self._format_external_body(parsed)
        summary = self._store.save_summary(
            callsign,
            "external",
            body,
            raw_context=context_snapshot,
            model_used=self._llm.model,
            source="notion",
        )
        return ExternalBrief(
            summary_id=summary.id,  # type: ignore[arg-type]
            callsign=callsign,
            company_name=company.name,
            product=parsed["product"],
            news=parsed["news"],
            announcements=parsed["announcements"],
            raw_text=body,
            context_snapshot=context_snapshot,
            created_at=summary.created_at,
        )

    def _compose_external_prompt(
        self, company: CompanyContext, manual_highlights: List[str]
    ) -> str:
        news_lines = []
        for item in company.news_highlights:
            week = item.week_of.isoformat() if item.week_of else "recent"
            bits = [item.title]
            if item.summary:
                bits.append(item.summary)
            if item.url:
                bits.append(item.url)
            news_lines.append(f"- ({week}) {' — '.join(bits)}")
        manual = "\n".join(f"- {line}" for line in manual_highlights if line.strip())
        news_block = "\n".join(news_lines) or "No recent news logged."
        last_intel = "unknown"
        if company.last_intel_update:
            last_intel = company.last_intel_update.isoformat()
        return f"""Company overview
Name: {company.name}
Callsign: {company.callsign}
Summary: {company.summary or 'No summary recorded.'}
Last Intel Update: {last_intel}

Recent news items:
{news_block}

Manual highlights:
{manual or 'None'}

Return JSON with keys product (string), news (list of short bullets),
announcements (list of short bullets).
"""

    def _parse_external_response(self, text: str) -> dict:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"product": text.strip(), "news": [], "announcements": []}
        product = str(data.get("product") or "No product description provided.").strip()

        def _normalize_list(value) -> List[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str):
                return [line.strip() for line in value.splitlines() if line.strip()]
            s = str(value).strip()
            return [s] if s else []

        news = _normalize_list(data.get("news"))
        announcements = _normalize_list(data.get("announcements"))
        return {
            "product": product,
            "news": news,
            "announcements": announcements,
        }

    def _format_external_body(self, parsed: dict) -> str:
        lines = []
        lines.append("What they do:\n" + parsed["product"])
        if parsed["news"]:
            lines.append("\nLatest news:")
            lines.extend(f"- {item}" for item in parsed["news"])
        if parsed["announcements"]:
            lines.append("\nRecent announcements (<=6mo):")
            lines.extend(f"- {item}" for item in parsed["announcements"])
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def build_internal_brief(self, callsign: str) -> InternalBrief:
        """Build an internal usage snapshot brief."""
        snapshot = self._internal.fetch(callsign)
        notes = self._format_internal_snapshot(snapshot)
        summary = self._store.save_summary(
            callsign,
            "internal",
            notes,
            raw_context=snapshot.raw,
            model_used=None,
            source=snapshot.status,
        )
        return InternalBrief(
            summary_id=summary.id,  # type: ignore[arg-type]
            callsign=callsign,
            notes=notes,
            snapshot=snapshot,
            created_at=summary.created_at,
        )

    def _format_internal_snapshot(self, snapshot: InternalUsageSnapshot) -> str:
        owners = ", ".join(snapshot.owners) or "No owner assigned."
        products = ", ".join(snapshot.products) or "No usage recorded."
        sections = [
            f"Status: {snapshot.status}",
            f"Primary contacts: {owners}",
            f"Products in use: {products}",
            f"Notes: {snapshot.notes or 'None'}",
        ]
        return "\n".join(sections)


def _company_to_dict(company: CompanyContext) -> dict:
    return {
        "name": company.name,
        "callsign": company.callsign,
        "summary": company.summary,
        "last_intel_update": (
            company.last_intel_update.isoformat() if company.last_intel_update else None
        ),
        "owners": company.owners,
        "news_highlights": [_news_to_dict(item) for item in company.news_highlights],
    }


def _news_to_dict(item: NewsHighlight) -> dict:
    return {
        "title": item.title,
        "summary": item.summary,
        "url": item.url,
        "week_of": item.week_of.isoformat() if item.week_of else None,
    }
