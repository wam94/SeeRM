"""Notion helpers for discovering SeeRM report artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, List, Optional

from notion_client import Client


@dataclass
class ReportIndexEntry:
    report_id: str
    notion_page_id: str
    week_of: date
    artifact_url: Optional[str] = None


class NotionReportReader:
    """Thin wrapper around the Notion API for the Reports DB."""

    def __init__(self, api_key: str, reports_db_id: str) -> None:
        self._client = Client(auth=api_key)
        self._reports_db_id = reports_db_id

    def list_reports(self, since: Optional[date] = None, page_size: int = 20) -> List[ReportIndexEntry]:
        """Return recent reports ordered by week."""
        query: dict = {
            "database_id": self._reports_db_id,
            "page_size": page_size,
            "sorts": [{"property": "Week Of", "direction": "descending"}],
        }
        if since:
            query["filter"] = {
                "property": "Week Of",
                "date": {"on_or_after": since.isoformat()},
            }

        resp = self._client.databases.query(**query)
        return [self._parse_page(result) for result in resp.get("results", [])]

    def _parse_page(self, page: dict) -> ReportIndexEntry:
        props = page.get("properties", {})
        week_prop = props.get("Week Of", {}).get("date", {})
        week_value = week_prop.get("start")
        week_of = datetime.fromisoformat(week_value).date() if week_value else date.today()

        report_id_prop = props.get("Report ID", {}).get("rich_text", [])
        report_id = report_id_prop[0]["text"]["content"] if report_id_prop else page["id"]

        files = props.get("Attachment", {}).get("files", [])
        artifact_url = files[0].get("file", {}).get("url") if files else None

        return ReportIndexEntry(
            report_id=report_id,
            notion_page_id=page["id"],
            week_of=week_of,
            artifact_url=artifact_url,
        )
