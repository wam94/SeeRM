"""OpenAI helpers for the fine-tuned voice model."""

from __future__ import annotations

import json
from typing import List, Optional

from openai import OpenAI

from .config import MessagingSettings
from .notion_ingest import CompanyContext, NewsHighlight


class VoicePromptBuilder:
    """Creates prompts for the fine-tuned voice model."""

    @staticmethod
    def build_messages(
        raw_blurb: str,
        manual_notes: Optional[str],
        knowledge_base_text: Optional[str],
    ) -> List[dict]:
        manual_section = manual_notes.strip() if manual_notes else "N/A"
        kb_section = knowledge_base_text.strip() if knowledge_base_text else "N/A"

        system_content = (
            "You're a model fine tuned to write in the voice of Will Mitchell."
            "Your job is to rewrite the provided blurb so it sounds like Will—"
            "while maintaining the original meaning and intent."
            "It is a failure if you repeat the original wording—always restate it in fresh language."
        )

        user_content = f"""Blurb draft:
{raw_blurb.strip()}

Manual notes:
{manual_section}

Knowledge base excerpt:
{kb_section}
"""

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]


class VoiceModelClient:
    """Thin wrapper around OpenAI's chat completions for the fine-tuned model."""

    def __init__(self, settings: MessagingSettings) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.voice_model_id
        self._temperature = settings.voice_model_temperature

    def generate_html(self, messages: List[dict]) -> str:
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
        )
        return completion.choices[0].message.content or ""


class ReasoningModelClient:
    """Uses a higher-capacity model to plan the blurb content."""

    def __init__(self, settings: MessagingSettings) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.reasoning_model_id
        self._temperature = settings.reasoning_model_temperature

    def build_raw_blurb(
        self,
        company: CompanyContext,
        manual_notes: Optional[str],
        knowledge_base_text: Optional[str] = None,
    ) -> str:
        system = (
            "You’re helping Will write quick, natural outreach notes to founders. "
            "Each note should sound like something he’d actually send in an email or LinkedIn comment—"
            "friendly, professional, conversational, with no marketing polish. "
            "Rules: keep it to 1-3 sentences under 50 words; use plain language; react to a specific "
            "company milestone; avoid summarizing; only use simple adjectives if natural ('great', 'exciting'); "
            "no PR/sales tone; no em dashes."
        )

        user = f"""Company dossier:
{_format_company_section(company)}

Recent news:
{_format_news(company.news_highlights) or 'No recent highlights provided.'}

Manual notes:
{manual_notes or 'N/A'}

Knowledge base excerpt:
{knowledge_base_text or 'N/A'}

Return JSON only.
"""

        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self._temperature,
        )
        raw = (completion.choices[0].message.content or "").strip()
        return _extract_note_text(raw)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def _format_company_section(company: CompanyContext) -> str:
    summary = company.summary or "No summary captured."
    updated = company.last_intel_update.isoformat() if company.last_intel_update else "Unknown"
    owners = ", ".join(company.owners) if company.owners else "Unassigned"
    return (
        f"Name: {company.name}\n"
        f"Callsign: {company.callsign}\n"
        f"Owner(s): {owners}\n"
        f"Last Intel Update: {updated}\n"
        f"Summary: {summary}"
    )


def _format_news(news: List[NewsHighlight]) -> str:
    if not news:
        return ""
    lines = []
    for item in news:
        week = item.week_of.isoformat() if item.week_of else "recent"
        summary = item.summary or ""
        lines.append(f"- ({week}) {item.title}: {summary} {item.url or ''}".strip())
    return "\n".join(lines)


def _extract_note_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        stripped = text.strip()
        stripped = stripped.lstrip("`").rstrip("`")
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[1]
        text = stripped.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("note", "blurb", "text"):
                value = data.get(key)
                if value:
                    return str(value).strip()
    except json.JSONDecodeError:
        pass
    return text
