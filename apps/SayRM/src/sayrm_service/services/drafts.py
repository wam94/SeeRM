"""Draft composition helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ..clients.llm_client import LLMClient
from ..store import DataStore
from .templates import TemplateDefinition, TemplateService


@dataclass
class DraftContext:
    """Normalized context for composing drafts."""

    instructions: str
    manual_snippets: List[str]
    external_summary: str
    internal_summary: str

    def to_dict(self) -> dict:
        return {
            "instructions": self.instructions,
            "manual_snippets": self.manual_snippets,
            "external_summary": self.external_summary,
            "internal_summary": self.internal_summary,
        }

    def snippet_block(self) -> str:
        if not self.manual_snippets:
            return "None"
        return "\n".join(f"- {snippet}" for snippet in self.manual_snippets)


@dataclass
class DraftResult:
    draft_id: int
    callsign: str
    template_id: Optional[str]
    body: str
    context_snapshot: dict
    created_at: datetime


class DraftService:
    """Builds LLM assisted drafts and logs the output for future fine-tuning."""

    def __init__(self, *, llm_client: LLMClient, template_service: TemplateService, store: DataStore) -> None:
        self._llm = llm_client
        self._templates = template_service
        self._store = store

    def compose(
        self,
        callsign: str,
        *,
        template_id: Optional[str],
        instructions: Optional[str],
        manual_snippets: Optional[List[str]],
        external_summary: Optional[str],
        internal_summary: Optional[str],
    ) -> DraftResult:
        template = self._resolve_template(template_id)
        context = self._build_context(
            instructions=instructions,
            manual_snippets=manual_snippets,
            external_summary=external_summary,
            internal_summary=internal_summary,
        )
        prompt = self._compose_prompt(template, context)
        system_msg = (
            "You are Will Mitchell's drafting assistant. "
            "Respect the template voice, keep things under 180 words, "
            "and never fabricate product usage details."
        )
        body = self._llm.run_chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
        ).strip()

        draft = self._store.save_draft(
            callsign,
            template_id=template.id if template else None,
            body=body,
            prompt=prompt,
            context_snapshot=context.to_dict(),
            model_used=self._llm.model,
        )
        return DraftResult(
            draft_id=draft.id,  # type: ignore[arg-type]
            callsign=callsign,
            template_id=draft.template_id,
            body=body,
            context_snapshot=context.to_dict(),
            created_at=draft.created_at,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _resolve_template(self, template_id: Optional[str]) -> Optional[TemplateDefinition]:
        if not template_id:
            return None
        template = self._templates.get_template(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")
        return template

    def _build_context(
        self,
        *,
        instructions: Optional[str],
        manual_snippets: Optional[List[str]],
        external_summary: Optional[str],
        internal_summary: Optional[str],
    ) -> DraftContext:
        snippets = [snippet.strip() for snippet in (manual_snippets or []) if snippet and snippet.strip()]
        return DraftContext(
            instructions=(instructions or "").strip(),
            manual_snippets=snippets,
            external_summary=(external_summary or "").strip(),
            internal_summary=(internal_summary or "").strip(),
        )

    def _compose_prompt(self, template: Optional[TemplateDefinition], context: DraftContext) -> str:
        template_block = template.body if template else "No template provided; write a concise note."
        return f"""Template:
{template_block}

Instructions:
{context.instructions or 'No extra instructions.'}

External summary:
{context.external_summary or 'N/A'}

Internal usage summary:
{context.internal_summary or 'N/A'}

Manual snippets to incorporate:
{context.snippet_block()}

Write the full body of the email ready to paste into Gmail (no subject line)."""
