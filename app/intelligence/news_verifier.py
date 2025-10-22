"""LLM-backed relevance verification for collected news items."""

from __future__ import annotations

import hashlib
import logging
import os
import textwrap
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.intelligence.models import NewsItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("NEWS_VERIFIER_MODEL", "gpt-5-nano")
MAX_DOSSIER_CHARS = int(os.getenv("NEWS_VERIFIER_MAX_DOSSIER_CHARS", "2800") or "2800")


@dataclass
class VerificationDecision:
    """Structured result for a single news verification attempt."""

    url: str
    verdict: str


class LLMNewsVerifier:
    """Use an LLM to verify whether news items are about the target company."""

    def __init__(self, model: Optional[str] = None):
        """Initialise verifier with optional model override."""
        self.model = (model or DEFAULT_MODEL).strip()
        self._api_key = os.getenv("OPENAI_API_KEY")
        self._client = None
        self._enabled = False
        self._cache: Dict[str, VerificationDecision] = {}

        if not self._api_key:
            logger.debug("LLM verifier disabled: OPENAI_API_KEY not set")
            return

        try:
            import openai  # type: ignore

            self._client = openai.OpenAI(api_key=self._api_key)
            self._enabled = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM verifier initialisation failed", error=str(exc))
            self._client = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        """Return True when the verifier can call an LLM."""
        return self._enabled and self._client is not None

    def filter_items(
        self,
        company_callsign: str,
        items: Sequence[NewsItem],
        dossier_text: Optional[str] = None,
        company_context: Optional[Dict[str, object]] = None,
    ) -> Tuple[List[NewsItem], List[NewsItem]]:
        """Return (accepted, rejected) news items after LLM verification."""
        if not items:
            return [], []

        if not self.enabled:
            accepted = list(items)
            for item in accepted:
                item.llm_verdict = "STRICT_YES"
            return accepted, []

        accepted: List[NewsItem] = []
        rejected: List[NewsItem] = []
        dossier_hash = self._hash_text(dossier_text or "")

        for item in items:
            decision = self._verify_single(
                company_callsign=company_callsign,
                item=item,
                dossier_text=dossier_text,
                dossier_hash=dossier_hash,
                company_context=company_context or {},
            )
            item.llm_verdict = decision.verdict
            if decision.verdict == "STRICT_YES":
                accepted.append(item)
            else:
                rejected.append(item)

        return accepted, rejected

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest() if text else "none"

    def _verify_single(
        self,
        company_callsign: str,
        item: NewsItem,
        dossier_text: Optional[str],
        dossier_hash: str,
        company_context: Dict[str, object],
    ) -> VerificationDecision:
        cache_key = self._cache_key(company_callsign, item, dossier_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt = self._build_prompt(
            company_callsign=company_callsign,
            dossier_text=dossier_text,
            company_context=company_context,
            item=item,
        )
        system_prompt = (
            "You are a strict news relevance checker. "
            "Decide if an article is primarily about the specified company. "
            "Only respond with STRICT_YES or STRICT_NO. "
            "If uncertain, respond STRICT_NO."
        )

        verdict = "STRICT_NO"
        if self.enabled:
            verdict = self._call_llm(system_prompt, prompt)

        decision = VerificationDecision(url=item.url, verdict=verdict)
        self._cache[cache_key] = decision
        return decision

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the configured LLM and parse the STRICT_YES/STRICT_NO verdict."""
        if not self.enabled:
            return "STRICT_YES"

        assert self._client is not None  # Satisfy type checker

        try:
            is_gpt5 = self.model.lower().startswith("gpt-5")
            if is_gpt5:
                response = self._client.responses.create(
                    model=self.model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                raw_output = (response.output_text or "").strip()
            else:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0,
                    max_tokens=4,
                )
                raw_output = (response.choices[0].message.content or "").strip()

            verdict = raw_output.upper().split()[0]
            if verdict not in {"STRICT_YES", "STRICT_NO"}:
                logger.debug(
                    "LLM returned non-strict verdict",
                    verdict=raw_output,
                    model=self.model,
                )
                return "STRICT_NO"
            return verdict
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM verifier call failed", model=self.model, error=str(exc))
            return "STRICT_NO"

    @staticmethod
    def _cache_key(company_callsign: str, item: NewsItem, dossier_hash: str) -> str:
        url_or_title = item.url or item.title or ""
        token = hashlib.sha1(url_or_title.encode("utf-8")).hexdigest()
        return f"{company_callsign.lower()}::{token}::{dossier_hash}"

    @staticmethod
    def _clean_context_value(value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            parts = [str(v).strip() for v in value if str(v).strip()]
            return ", ".join(parts) if parts else None
        text = str(value).strip()
        return text or None

    def _build_prompt(
        self,
        company_callsign: str,
        dossier_text: Optional[str],
        company_context: Dict[str, object],
        item: NewsItem,
    ) -> str:
        dossier_excerpt = (dossier_text or "").strip()
        if dossier_excerpt:
            dossier_excerpt = textwrap.shorten(
                dossier_excerpt, width=MAX_DOSSIER_CHARS, placeholder=" […]"
            )
        else:
            dossier_excerpt = "No dossier available."

        context_lines: List[str] = [f"Callsign: {company_callsign}"]
        for key in ("dba", "company", "aka_names", "owners", "website", "domain_root", "tags"):
            value = self._clean_context_value(company_context.get(key))
            if value:
                pretty_key = key.replace("_", " ").title()
                context_lines.append(f"{pretty_key}: {value}")

        summary_text = item.summary or ""
        if not summary_text:
            summary_text = "No summary available."

        article_lines = [
            f"Title: {item.title or 'Untitled'}",
            f"Summary: {summary_text}",
            f"Source: {item.source or 'Unknown'}",
            f"Published At: {item.published_at or 'Unknown'}",
            f"URL: {item.url or 'N/A'}",
        ]

        prompt = (
            "Company dossier (excerpt):\n"
            f"{dossier_excerpt}\n\n"
            "Company context:\n"
            f"{os.linesep.join(context_lines)}\n\n"
            "Article under review:\n"
            f"{os.linesep.join(article_lines)}\n\n"
            f"Question: Is this article primarily about {company_callsign.upper()} "
            "or its direct subsidiaries? Respond STRICT_YES or STRICT_NO only."
        )

        return prompt


def filter_news_items_with_llm(
    verifier: LLMNewsVerifier,
    company_callsign: str,
    items: Iterable[NewsItem],
    dossier_text: Optional[str],
    company_context: Optional[Dict[str, object]] = None,
) -> Tuple[List[NewsItem], List[NewsItem]]:
    """Filter items with an existing verifier instance."""
    return verifier.filter_items(
        company_callsign=company_callsign,
        items=list(items),
        dossier_text=dossier_text,
        company_context=company_context or {},
    )
