"""Messaging consumer package scaffolding."""

from .config import MessagingSettings
from .contracts import WeeklyNewsDigestPayload, load_weekly_digest
from .notion_ingest import CompanyContext, NotionContextFetcher, NotionReportReader, ReportIndexEntry

__all__ = [
    "MessagingSettings",
    "WeeklyNewsDigestPayload",
    "load_weekly_digest",
    "NotionReportReader",
    "ReportIndexEntry",
    "NotionContextFetcher",
    "CompanyContext",
]
