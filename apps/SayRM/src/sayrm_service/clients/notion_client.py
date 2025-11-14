"""Thin wrapper around shared Notion context helpers."""

from __future__ import annotations

from typing import Optional

from shared.core.notion_context import CompanyContext, NotionContextFetcher

from ..config import SayRMSettings


class ExternalContextClient:
    """Fetches Notion context for a company callsign."""

    def __init__(self, settings: SayRMSettings) -> None:
        self._fetcher = NotionContextFetcher(
            api_key=settings.notion_api_key,
            companies_db_id=settings.notion_companies_db_id,
            intel_db_id=settings.notion_intel_db_id,
        )

    def get_company(self, callsign: str) -> Optional[CompanyContext]:
        """Return the latest Notion context for a callsign."""
        return self._fetcher.get_company_context(callsign)
