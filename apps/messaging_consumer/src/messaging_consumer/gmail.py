"""Gmail draft helper for Raycast-triggered greetings."""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .config import MessagingSettings

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def build_gmail_service(settings: MessagingSettings):
    creds = Credentials(
        token=None,
        refresh_token=settings.gmail_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailDraftService:
    """Creates Gmail drafts using the same OAuth credentials as SeeRM."""

    def __init__(self, settings: MessagingSettings) -> None:
        self.settings = settings
        self._service = None

    def create_draft(
        self,
        *,
        to: List[str],
        subject: str,
        html_body: str,
        plain_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> dict:
        if not to:
            raise ValueError("At least one recipient email is required")

        message = EmailMessage()
        message["To"] = ", ".join(to)
        message["From"] = self.settings.gmail_user
        message["Subject"] = subject

        if cc:
            message["Cc"] = ", ".join(cc)
        if bcc:
            message["Bcc"] = ", ".join(bcc)

        text_body = plain_body or _html_to_plain_text(html_body)
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        draft = {"message": {"raw": raw}}
        return self._build_service().users().drafts().create(userId="me", body=draft).execute()

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _build_service(self):
        if self._service is None:
            self._service = build_gmail_service(self.settings)
        return self._service


def _html_to_plain_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).strip()
