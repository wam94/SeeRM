"""Shared Notion helpers for SeeRM apps.

Provides a reusable interface for fetching company context and weekly
reports from Notion so that multiple applications can share the same
logic without depending on each other's internal modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

import structlog

try:  # pragma: no cover - optional dependency for tests
    from notion_client import Client  # type: ignore
except ImportError:  # pragma: no cover
    Client = None  # type: ignore


@dataclass
class ReportIndexEntry:
    """Metadata for a weekly report stored in Notion."""

    report_id: str
    notion_page_id: str
    week_of: date
    artifact_url: Optional[str] = None


@dataclass
class NewsHighlight:
    """Compact summary of a company news item."""

    title: str
    summary: Optional[str]
    url: Optional[str]
    week_of: Optional[date]


@dataclass
class CompanyContext:
    """Normalized company context payload pulled from Notion."""

    notion_page_id: str
    name: str
    callsign: str
    owners: List[str]
    summary: Optional[str]
    last_intel_update: Optional[date]
    news_highlights: List[NewsHighlight]


class NotionReportReader:
    """Thin wrapper around the Notion API for the Reports DB."""

    def __init__(self, api_key: str, reports_db_id: str) -> None:
        """Create a reports DB reader bound to the given API key."""
        if Client is None:  # pragma: no cover
            raise RuntimeError("notion-client is required to use NotionReportReader")
        # Use a Notion API version that still supports the classic
        # ``POST /databases/{database_id}/query`` endpoint.
        self._client = Client(auth=api_key, notion_version="2022-06-28")
        self._reports_db_id = reports_db_id

    def list_reports(
        self, since: Optional[date] = None, page_size: int = 20
    ) -> List[ReportIndexEntry]:
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

        resp = _databases_query(self._client, query)
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


logger = structlog.get_logger(__name__)


def _databases_query(client: "Client", query: dict) -> dict:
    """
    Compatibility wrapper for the Notion Python SDK.

    Older versions expose ``client.databases.query`` while newer 2.x releases
    removed the helper in favour of direct ``POST /databases/{id}/query``.
    This helper calls ``databases.query`` when available, otherwise falls back
    to the raw HTTP request.
    """
    databases = getattr(client, "databases", None)
    if databases is not None and hasattr(databases, "query"):
        return databases.query(**query)

    database_id = query.get("database_id")
    if not database_id:
        raise ValueError("database_id is required for Notion database queries")

    # Some environments store database IDs as 32-character hex strings without
    # dashes. The HTTP API expects the UUID format; normalise if needed.
    db_id_str = str(database_id)
    if "-" not in db_id_str and len(db_id_str) == 32:
        db_id_str = (
            f"{db_id_str[0:8]}-{db_id_str[8:12]}-"
            f"{db_id_str[12:16]}-{db_id_str[16:20]}-{db_id_str[20:32]}"
        )

    body = {k: v for k, v in query.items() if k != "database_id"}
    # The official client exposes a low-level request helper.
    return client.request(
        path=f"databases/{db_id_str}/query",
        method="POST",
        body=body,
    )


class NotionContextFetcher:
    """Fetch dossier + news context for a company by callsign."""

    def __init__(
        self,
        api_key: str,
        companies_db_id: str,
        intel_db_id: Optional[str] = None,
        *,
        max_news_items: int = 5,
        client: Optional[Client] = None,
    ) -> None:
        """Create a context fetcher bound to company + intel databases."""
        if client is not None:
            self._client = client
        else:
            if Client is None:  # pragma: no cover
                raise RuntimeError("notion-client is required to use NotionContextFetcher")
            # Align with the version used in production SeeRM flows so that
            # database queries keep working even as the SDK evolves.
            self._client = Client(auth=api_key, notion_version="2022-06-28")
        self._companies_db_id = companies_db_id
        self._intel_db_id = intel_db_id
        self._max_news_items = max_news_items

    def get_company_context(self, callsign: str) -> Optional[CompanyContext]:
        """Return structured context for a callsign, if found."""
        normalized = callsign.strip().lower()
        resp = _databases_query(
            self._client,
            {
                "database_id": self._companies_db_id,
                "filter": {"property": "Callsign", "rich_text": {"equals": normalized}},
                "page_size": 1,
            },
        )
        results = resp.get("results", [])
        if not results:
            return None
        page = results[0]
        props = page.get("properties", {})

        summary = _first_rich_text(props, ["Intel Summary", "Latest Intel", "Intel Log"])
        last_intel = _first_date(props, ["Last Intel Update", "Latest Intel At"])
        owners = _first_people(
            props,
            ["Relationship Manager(s)", "Relationship Owner(s)", "Owners", "Owner"],
        )

        news_highlights = self._fetch_news_highlights(page, props)

        logger.info(
            "Fetched Notion company context",
            callsign=normalized,
            has_summary=bool(summary),
            highlights=len(news_highlights),
        )

        return CompanyContext(
            notion_page_id=page["id"],
            name=_extract_title(props.get("Name") or props.get("Company")),
            callsign=normalized,
            owners=owners,
            summary=summary,
            last_intel_update=last_intel,
            news_highlights=news_highlights,
        )

    def _fetch_news_highlights(self, company_page: dict, props: dict) -> List[NewsHighlight]:
        highlights: List[NewsHighlight] = []

        relation = props.get("News Items", {}).get("relation") if props.get("News Items") else None
        if relation:
            for rel in relation[: self._max_news_items]:
                news_page = self._client.pages.retrieve(rel["id"])
                highlights.append(self._map_news_page(news_page))

        if not highlights and self._intel_db_id:
            resp = _databases_query(
                self._client,
                {
                    "database_id": self._intel_db_id,
                    "filter": {
                        "property": "Callsign",
                        "relation": {"contains": company_page["id"]},
                    },
                    "sorts": [{"property": "Last Seen", "direction": "descending"}],
                    "page_size": self._max_news_items,
                },
            )
            for item in resp.get("results", []):
                highlights.append(self._map_news_page(item))

        blocks_api = getattr(getattr(self._client, "blocks", None), "children", None)
        if not blocks_api:
            return highlights

        blocks = blocks_api.list(company_page["id"]).get("results", [])
        capture = False
        for block in blocks:
            block_type = block.get("type")
            if block_type == "heading_2":
                heading_text = _extract_block_text(block.get("heading_2"))
                capture = "news" in heading_text.lower()
                continue
            if capture and block_type in {"bulleted_list_item", "paragraph"}:
                text = _extract_rich_text(block.get(block_type))
                if text:
                    highlights.append(
                        NewsHighlight(
                            title=text.lstrip("-• ").strip(),
                            summary=None,
                            url=None,
                            week_of=None,
                        )
                    )

        return highlights[: self._max_news_items]

    def _map_news_page(self, page: dict) -> NewsHighlight:
        props = page.get("properties", {})
        return NewsHighlight(
            title=_extract_title(props.get("Title")),
            summary=_extract_rich_text(props.get("Summary")),
            url=props.get("URL", {}).get("url"),
            week_of=_extract_date(props.get("Week Of")),
        )


def _extract_title(prop: Optional[dict]) -> str:
    return _extract_text(prop, key="title") or "(Untitled)"


def _first_rich_text(props: dict, names: List[str]) -> Optional[str]:
    for name in names:
        value = _extract_rich_text(props.get(name))
        if value:
            return value
    return None


def _first_date(props: dict, names: List[str]) -> Optional[date]:
    for name in names:
        value = _extract_date(props.get(name))
        if value:
            return value
    return None


def _first_people(props: dict, names: List[str]) -> List[str]:
    for name in names:
        value = _extract_people(props.get(name))
        if value:
            return value
    return []


def _extract_rich_text(prop: Optional[dict]) -> Optional[str]:
    if not prop:
        return None
    text = _extract_text(prop, key="rich_text")
    return text or None


def _extract_text(prop: Optional[dict], key: str) -> str:
    if not prop:
        return ""
    blocks = prop.get(key) or []
    return "".join(fragment.get("plain_text", "") for fragment in blocks)


def _extract_block_text(prop: Optional[dict]) -> str:
    if not prop:
        return ""
    return "".join(fragment.get("plain_text", "") for fragment in prop.get("rich_text", []))


def _extract_date(prop: Optional[dict]) -> Optional[date]:
    if not prop:
        return None
    start = (prop.get("date") or {}).get("start")
    if not start:
        return None
    try:
        return datetime.fromisoformat(start).date()
    except Exception:  # pragma: no cover - defensive
        return None


def _extract_people(prop: Optional[dict]) -> List[str]:
    if not prop:
        return []
    people = prop.get("people") or []
    names = []
    for person in people:
        name = person.get("name") or ""
        if name:
            names.append(name)
    return names


__all__ = [
    "ReportIndexEntry",
    "NewsHighlight",
    "CompanyContext",
    "NotionReportReader",
    "NotionContextFetcher",
]
