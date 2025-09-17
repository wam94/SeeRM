"""Utilities for tracking seen news items and retrieving new ones."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import structlog
from dateutil import parser as date_parser

from app.intelligence.models import NewsItem, NewsType

logger = structlog.get_logger(__name__)


def _normalize_url(url: str) -> str:
    """Canonicalize URLs for deduping (strip fragments & tracking params)."""
    if not url:
        return ""

    parts = urlsplit(url)
    query_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"ref", "gclid", "fbclid"}
    ]
    normalized_query = "&".join(f"{k}={v}" if v else k for k, v in query_params)

    normalized = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            normalized_query,
            "",
        )
    )
    return normalized


def _safe_parse_date(date_value: Optional[str]) -> Optional[str]:
    """Parse date strings to ISO format (YYYY-MM-DD)."""
    if not date_value:
        return None
    try:
        dt = date_parser.parse(str(date_value))
        return dt.date().isoformat()
    except Exception:
        return None


@dataclass
class NewsSeenRecord:
    """Record of a single news item with timestamps."""

    url: str
    callsign: str
    first_seen: datetime
    last_seen: datetime
    published_at: Optional[str] = None


class LocalNewsSeenStore:
    """Simple JSON-based store for environments without Notion."""

    def __init__(self, state_path: Path):
        """Initialise the store backing JSON file if necessary."""
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> Dict[str, NewsSeenRecord]:
        """Load existing records from disk."""
        if not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            data = {}
            for key, record in raw.items():
                data[key] = NewsSeenRecord(
                    url=record["url"],
                    callsign=record["callsign"],
                    first_seen=datetime.fromisoformat(record["first_seen"]),
                    last_seen=datetime.fromisoformat(record["last_seen"]),
                    published_at=record.get("published_at"),
                )
            return data
        except Exception as exc:
            logger.warning("Failed to load seen store", error=str(exc))
            return {}

    def _save(self) -> None:
        """Persist current records to disk."""
        raw = {
            key: {
                "url": record.url,
                "callsign": record.callsign,
                "first_seen": record.first_seen.isoformat(),
                "last_seen": record.last_seen.isoformat(),
                "published_at": record.published_at,
            }
            for key, record in self._data.items()
        }
        self.state_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def ingest(
        self, callsign: str, items: Iterable[NewsItem]
    ) -> Tuple[List[NewsItem], List[NewsItem]]:
        """Record items as seen and return (new, existing)."""
        new_items: List[NewsItem] = []
        existing_items: List[NewsItem] = []
        now = datetime.utcnow()

        for item in items:
            norm_url = _normalize_url(item.url)
            if not norm_url:
                continue

            record = self._data.get(norm_url)
            if record:
                record.last_seen = now
                existing_items.append(item)
            else:
                self._data[norm_url] = NewsSeenRecord(
                    url=norm_url,
                    callsign=callsign,
                    first_seen=now,
                    last_seen=now,
                    published_at=_safe_parse_date(item.published_at),
                )
                new_items.append(item)

        self._save()
        return new_items, existing_items

    def get_recent(self, callsign: str, days: int) -> List[NewsItem]:
        """Return items whose first_seen timestamp lies within the window."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        results = []
        for record in self._data.values():
            if record.callsign.lower() != callsign.lower():
                continue
            if record.first_seen >= cutoff:
                results.append(
                    NewsItem(
                        title="",
                        url=record.url,
                        source="",
                        published_at=record.published_at or "",
                        summary=None,
                        news_type=NewsType.OTHER_NOTABLE,
                        relevance_score=0.0,
                        sentiment=None,
                        company_mentions=[callsign.upper()],
                    )
                )
        return results


class NotionNewsSeenStore:
    """Notion-backed store for news items."""

    def __init__(
        self,
        notion_client,
        intel_db_id: str,
        companies_db_id: Optional[str] = None,
    ):
        """Initialise the store with Notion client and database identifiers."""
        self.client = notion_client
        self.intel_db_id = intel_db_id
        self.companies_db_id = companies_db_id
        self._schema = self._load_schema()
        self._company_page_cache: Dict[str, Optional[str]] = {}

    def _load_schema(self):
        """Load schema metadata for news items database."""
        raw_schema = self.client.get_database_schema(self.intel_db_id)
        properties = raw_schema.get("properties", {})

        def prefer(name: str, prop_type: str) -> Optional[str]:
            if name in properties and properties[name]["type"] == prop_type:
                return name
            for key, value in properties.items():
                if value.get("type") == prop_type:
                    return key
            return None

        title_prop = prefer("Title", "title")
        url_prop = prefer("URL", "url")
        first_seen_prop = prefer("First Seen", "date")
        last_seen_prop = prefer("Last Seen", "date")
        callsign_rel_prop = prefer("Callsign", "relation")
        source_prop = prefer("Source", "select") or prefer("Source", "rich_text")
        published_prop = prefer("Published At", "date")
        summary_prop = prefer("Summary", "rich_text")

        if not title_prop or not url_prop:
            raise ValueError("News Items database requires Title and URL properties")

        return {
            "title": title_prop,
            "url": url_prop,
            "first_seen": first_seen_prop,
            "last_seen": last_seen_prop,
            "callsign_rel": callsign_rel_prop,
            "source": source_prop,
            "published": published_prop,
            "summary": summary_prop,
        }

    def _get_company_page_id(self, callsign: str) -> Optional[str]:
        """Resolve company page IDs with caching."""
        if not self.companies_db_id:
            return None
        key = callsign.lower()
        if key in self._company_page_cache:
            return self._company_page_cache[key]
        try:
            page_id = self.client.find_company_page(self.companies_db_id, callsign)
        except Exception as exc:
            logger.warning("Failed to find company page", callsign=callsign, error=str(exc))
            page_id = None
        self._company_page_cache[key] = page_id
        return page_id

    def _build_source_property(self, source_value: str) -> Dict[str, any]:
        """Build a property payload for the source field."""
        prop = self._schema["source"]
        if not prop or not source_value:
            return {}
        db_schema = self.client.get_database_schema(self.intel_db_id)
        prop_type = None
        properties = db_schema.get("properties", {})
        if prop in properties:
            prop_type = properties[prop].get("type")
        if prop_type == "select":
            return {prop: {"select": {"name": source_value[:100]}}}
        if prop_type == "multi_select":
            return {prop: {"multi_select": [{"name": source_value[:100]}]}}
        # default: rich text
        return {
            prop: {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": source_value[:100]},
                    }
                ]
            }
        }

    def _create_page_properties(
        self,
        callsign: str,
        company_page_id: Optional[str],
        item: NewsItem,
        first_seen_iso: str,
        last_seen_iso: str,
    ) -> Dict[str, any]:
        """Create Notion properties for a news item."""
        props = {
            self._schema["title"]: {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": (item.title or callsign)[:200]},
                    }
                ]
            },
            self._schema["url"]: {"url": _normalize_url(item.url)},
        }

        if self._schema["first_seen"]:
            props[self._schema["first_seen"]] = {"date": {"start": first_seen_iso}}
        if self._schema["last_seen"]:
            props[self._schema["last_seen"]] = {"date": {"start": last_seen_iso}}
        if company_page_id and self._schema["callsign_rel"]:
            props[self._schema["callsign_rel"]] = {"relation": [{"id": company_page_id}]}
        if item.source and self._schema["source"]:
            props.update(self._build_source_property(item.source))
        published_iso = _safe_parse_date(item.published_at)
        if published_iso and self._schema["published"]:
            props[self._schema["published"]] = {"date": {"start": published_iso}}
        if item.summary and self._schema["summary"]:
            props[self._schema["summary"]] = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": item.summary[:2000]},
                    }
                ]
            }
        return props

    def ingest(
        self,
        callsign: str,
        company_page_id: Optional[str],
        items: Iterable[NewsItem],
    ) -> Tuple[List[NewsItem], List[NewsItem]]:
        """Ingest items into Notion and classify them as new or existing."""
        now_iso = datetime.utcnow().isoformat()
        new_items: List[NewsItem] = []
        existing_items: List[NewsItem] = []

        for item in items:
            norm_url = _normalize_url(item.url)
            if not norm_url:
                continue

            existing_page = self.client.find_news_item_by_url(
                self.intel_db_id, self._schema["url"], norm_url
            )
            props = self._create_page_properties(
                callsign,
                company_page_id or self._get_company_page_id(callsign),
                item,
                first_seen_iso=now_iso,
                last_seen_iso=now_iso,
            )

            if existing_page:
                if self._schema["first_seen"]:
                    props.pop(self._schema["first_seen"], None)
                try:
                    self.client.update_page(existing_page, props)
                except Exception as exc:
                    logger.warning("Failed to update news item", url=norm_url, error=str(exc))
                existing_items.append(item)
            else:
                try:
                    self.client.create_page(self.intel_db_id, props)
                    new_items.append(item)
                except Exception as exc:
                    logger.warning("Failed to create news item", url=norm_url, error=str(exc))

        return new_items, existing_items

    def filter_new_items(
        self,
        callsign: str,
        items: Iterable[NewsItem],
    ) -> Tuple[List[NewsItem], List[NewsItem]]:
        """Return items split into (new, existing) without mutating Notion."""
        new_items: List[NewsItem] = []
        existing_items: List[NewsItem] = []

        try:
            recent_items = self.get_recent(callsign, days=365)
            seen_urls = {
                _normalize_url(item.url) for item in recent_items if getattr(item, "url", None)
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load recent seen items", callsign=callsign, error=str(exc))
            seen_urls = set()

        for item in items:
            norm_url = _normalize_url(item.url)
            if not norm_url:
                continue

            if norm_url in seen_urls:
                existing_items.append(item)
            else:
                new_items.append(item)
                seen_urls.add(norm_url)

        return new_items, existing_items

    def get_recent(self, callsign: str, days: int) -> List[NewsItem]:
        """Return recently seen items for a callsign."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        cutoff_iso = cutoff.date().isoformat()

        company_page_id = self._get_company_page_id(callsign)
        filter_and = []
        if self._schema["first_seen"]:
            filter_and.append(
                {
                    "property": self._schema["first_seen"],
                    "date": {"on_or_after": cutoff_iso},
                }
            )
        if company_page_id and self._schema["callsign_rel"]:
            filter_and.append(
                {
                    "property": self._schema["callsign_rel"],
                    "relation": {"contains": company_page_id},
                }
            )

        filter_obj = {"and": filter_and} if filter_and else None

        pages = self.client.query_database_iter(self.intel_db_id, filter_obj=filter_obj)
        items: List[NewsItem] = []
        for page in pages:
            props = page.get("properties", {})
            url_value = (
                props.get(self._schema["url"], {}).get("url") if self._schema["url"] else None
            )
            if not url_value:
                continue
            title_prop = props.get(self._schema["title"], {})
            title = ""
            if title_prop.get("type") == "title" and title_prop.get("title"):
                title = "".join(t.get("plain_text", "") for t in title_prop.get("title", []))
            source = ""
            if self._schema["source"]:
                source_data = props.get(self._schema["source"], {})
                if source_data.get("type") == "select" and source_data.get("select"):
                    source = source_data["select"].get("name", "")
                elif source_data.get("type") == "multi_select" and source_data.get("multi_select"):
                    source = source_data["multi_select"][0].get("name", "")
                elif source_data.get("type") == "rich_text" and source_data.get("rich_text"):
                    source = "".join(
                        t.get("plain_text", "") for t in source_data.get("rich_text", [])
                    )
            published_at = None
            if self._schema["published"]:
                date_data = props.get(self._schema["published"], {})
                if date_data.get("type") == "date" and date_data.get("date"):
                    published_at = date_data["date"].get("start")
            summary = None
            if self._schema["summary"]:
                summary_data = props.get(self._schema["summary"], {})
                if summary_data.get("type") == "rich_text" and summary_data.get("rich_text"):
                    summary = "".join(
                        t.get("plain_text", "") for t in summary_data.get("rich_text", [])
                    )

            items.append(
                NewsItem(
                    title=title or url_value,
                    url=url_value,
                    source=source,
                    published_at=published_at or "",
                    summary=summary,
                    news_type=NewsType.OTHER_NOTABLE,
                    relevance_score=0.0,
                    sentiment=None,
                    company_mentions=[callsign.upper()],
                )
            )

        return items
