"""High level greeting orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import List, Optional

import structlog

from .config import MessagingSettings
from .gmail import GmailDraftService
from .gmail_template import GmailTemplateService
from .llm import ReasoningModelClient, VoiceModelClient, VoicePromptBuilder
from .notion_ingest import NotionContextFetcher

logger = structlog.get_logger(__name__)


@dataclass
class GreetingRequest:
    callsign: str
    recipients: List[str]
    first_names: str
    gift_link: str
    manual_notes: Optional[str] = None
    knowledge_base_text: Optional[str] = None
    subject: Optional[str] = None


@dataclass
class GreetingResult:
    html: str
    draft_response: dict


class GreetingService:
    """Coordinates Notion fetch, LLM prompt, and Gmail draft creation."""

    def __init__(self, settings: MessagingSettings) -> None:
        self.settings = settings
        self.notion = NotionContextFetcher(
            api_key=settings.notion_api_key,
            companies_db_id=settings.notion_companies_db_id,
            intel_db_id=settings.notion_intel_db_id,
        )
        self.reasoner = ReasoningModelClient(settings)
        self.llm = VoiceModelClient(settings)
        self.gmail = GmailDraftService(settings)
        template_loader = GmailTemplateService(settings)
        template_html = template_loader.fetch_template_html(
            label=settings.gmail_template_label,
            subject_tag=settings.gmail_template_subject_tag,
        )
        if template_html:
            template_html = _normalize_template_placeholders(template_html)
            logger.info(
                "Loaded Gmail template",
                label=settings.gmail_template_label,
                tag=settings.gmail_template_subject_tag,
            )
            self.template_html = template_html
        else:
            logger.info("Falling back to bundled template HTML")
            self.template_html = settings.load_template()

    def generate(self, request: GreetingRequest, *, create_draft: bool = True) -> GreetingResult:
        company = self.notion.get_company_context(request.callsign)
        if not company:
            raise ValueError(f"No company found in Notion for callsign '{request.callsign}'")

        literal_slots = {
            "gift_link": request.gift_link,
            "first_name_block": request.first_names,
        }
        raw_blurb = self.reasoner.build_raw_blurb(
            company=company,
            manual_notes=request.manual_notes,
            knowledge_base_text=request.knowledge_base_text,
        )
        logger.debug("Reasoning blurb draft", text=raw_blurb, callsign=request.callsign)

        builder = VoicePromptBuilder()
        messages = builder.build_messages(
            raw_blurb=raw_blurb,
            manual_notes=request.manual_notes,
            knowledge_base_text=request.knowledge_base_text,
        )

        logger.info("Submitting blurb prompt to voice model", callsign=request.callsign)
        voice_response = self.llm.generate_html(messages)
        logger.debug("Voice model response", response=voice_response, callsign=request.callsign)
        blurb_html = _format_blurb_html(voice_response)
        logger.debug("Final blurb html", html=blurb_html, callsign=request.callsign)

        html = self.template_html
        replacements = {
            "{first_name_block}": request.first_names,
            "{first_name}": request.first_names,
            "{gift_link}": request.gift_link,
            "{company_blurb}": blurb_html,
            "{Blurb}": blurb_html,
            "{blurb}": blurb_html,
        }
        for placeholder, value in replacements.items():
            html = html.replace(placeholder, value)

        draft = {}
        if create_draft:
            subject = request.subject or self.settings.default_subject
            draft = self.gmail.create_draft(
                to=request.recipients,
                subject=subject,
                html_body=html,
            )
            logger.info(
                "Gmail draft created",
                draft_id=draft.get("id"),
                recipients=request.recipients,
                subject=subject,
            )

        return GreetingResult(html=html, draft_response=draft)


def _format_blurb_html(raw_output: str) -> str:
    text = (raw_output or "").strip()
    # remove stray JSON wrapper if present
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("blurb"):
            text = parsed["blurb"]
    except json.JSONDecodeError:
        pass

    text = text.replace("{", "").replace("}", "").strip()
    if not text:
        text = "Excited to see everything you're building."

    words = text.split()
    if len(words) > 50:
        text = " ".join(words[:50]).rstrip(",")

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    sentences = sentences[:2] or ["Excited to see everything you're building."]
    paragraph = " ".join(sentences)
    if not paragraph.endswith((".", "!", "?")):
        paragraph += "."

    return f"<p>{paragraph}</p>"
def _normalize_template_placeholders(template: str) -> str:
    html = template
    replacements = {
        "{Blurb}": "{company_blurb}",
        "{blurb}": "{company_blurb}",
        "{{Blurb}}": "{company_blurb}",
        "{first_name}": "{first_name_block}",
        "{{first_name}}": "{first_name_block}",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html
