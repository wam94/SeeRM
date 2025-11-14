"""Fetch Gmail templates stored as labeled drafts."""

from __future__ import annotations

import base64
from typing import Optional

from email.message import EmailMessage

from googleapiclient.errors import HttpError

from .config import MessagingSettings
from .gmail import build_gmail_service


class GmailTemplateService:
    """Retrieves template drafts from Gmail."""

    def __init__(self, settings: MessagingSettings) -> None:
        self.settings = settings
        self._service = build_gmail_service(settings)

    def fetch_template_html(self, *, label: str, subject_tag: str) -> Optional[str]:
        query = f'label:{label} subject:"{subject_tag}"'
        try:
            drafts = (
                self._service.users()
                .drafts()
                .list(userId="me", q=query, maxResults=5)
                .execute()
                or {}
            )
        except HttpError:
            return None

        for draft in drafts.get("drafts", []):
            try:
                detail = (
                    self._service.users()
                    .drafts()
                    .get(userId="me", id=draft["id"], format="full")
                    .execute()
                )
            except HttpError:
                continue

            msg = detail.get("message", {})
            html = _extract_html_from_payload(msg.get("payload"))
            if html:
                return html
        return None


def _extract_html_from_payload(payload: Optional[dict]) -> Optional[str]:
    if not payload:
        return None

    mime_type = payload.get("mimeType")
    body = payload.get("body", {})

    if mime_type == "text/html":
        data = body.get("data")
        if not data:
            return None
        return base64.urlsafe_b64decode(data.encode()).decode()

    if mime_type and mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            html = _extract_html_from_payload(part)
            if html:
                return html

    if mime_type == "text/plain" and body.get("data"):
        return base64.urlsafe_b64decode(body["data"].encode()).decode()

    return None
