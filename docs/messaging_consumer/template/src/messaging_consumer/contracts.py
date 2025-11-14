"""Shared payload + validation helpers for the messaging consumer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _ROOT / "schema" / "weekly_news_digest.schema.json"


def _load_schema() -> Dict[str, Any]:
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema not found at {_SCHEMA_PATH}")
    return json.loads(_SCHEMA_PATH.read_text())


_SCHEMA = _load_schema()
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)


class SummaryStats(BaseModel):
    total_items: int
    unique_companies: int
    categories_active: int
    notable_items: int

    model_config = ConfigDict(extra="forbid")


class NotableItem(BaseModel):
    title: str
    source: str
    url: str
    companies: List[str]
    type: str
    relevance_score: float
    excerpt: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class RenderedContent(BaseModel):
    html: str
    markdown: str

    model_config = ConfigDict(extra="forbid")


class Attachment(BaseModel):
    label: str
    url: str

    model_config = ConfigDict(extra="forbid")


class CompanyCategories(BaseModel):
    company: str
    categories: List[str]

    model_config = ConfigDict(extra="forbid")


class WeeklyNewsDigestPayload(BaseModel):
    report_id: str
    week_of: str
    generated_at: str
    summary_stats: SummaryStats
    by_type: Dict[str, int]
    most_active_companies: List[List[Any]] = Field(default_factory=list)
    company_categories: List[CompanyCategories] = Field(default_factory=list)
    key_themes: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    notable_items: List[NotableItem]
    rendered: RenderedContent
    attachments: List[Attachment] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


def validate_payload(payload: Dict[str, Any]) -> WeeklyNewsDigestPayload:
    """Validate an in-memory payload against the JSON Schema + Pydantic model."""
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda e: e.path)
    if errors:
        raise jsonschema.ValidationError(errors)
    return WeeklyNewsDigestPayload.model_validate(payload)


def load_weekly_digest(path: Path | str) -> WeeklyNewsDigestPayload:
    """Load and validate a digest JSON file."""
    data = json.loads(Path(path).read_text())
    return validate_payload(data)
