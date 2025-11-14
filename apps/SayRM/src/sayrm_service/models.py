"""Pydantic models for the FastAPI boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class BriefRequest(BaseModel):
    manual_highlights: List[str] = Field(default_factory=list)


class ExternalBriefResponse(BaseModel):
    summary_id: int
    callsign: str
    company_name: Optional[str]
    product: str
    news: List[str]
    announcements: List[str]
    raw_text: str
    created_at: datetime


class InternalBriefResponse(BaseModel):
    summary_id: int
    callsign: str
    notes: str
    status: str
    created_at: datetime


class ComposeDraftRequest(BaseModel):
    callsign: str
    template_id: Optional[str] = None
    instructions: Optional[str] = None
    manual_snippets: List[str] = Field(default_factory=list)
    external_summary: Optional[str] = None
    internal_summary: Optional[str] = None


class ComposeDraftResponse(BaseModel):
    draft_id: int
    callsign: str
    template_id: Optional[str]
    body: str
    created_at: datetime


class TemplateInfo(BaseModel):
    id: str
    title: str
    description: str
    body: str
    tags: List[str]


class LabelRequest(BaseModel):
    draft_id: int
    labels: dict[str, str]
    created_by: Optional[str] = None


class DraftPreview(BaseModel):
    id: int
    callsign: str
    template_id: Optional[str]
    body: str
    created_at: datetime


class DraftListResponse(BaseModel):
    drafts: List[DraftPreview]


class InternalUsageSnapshotModel(BaseModel):
    status: str
    owners: List[str]
    products: List[str]
    notes: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


class ExternalContextCard(BaseModel):
    brief: ExternalBriefResponse
    context: dict[str, Any]


class InternalContextCard(BaseModel):
    brief: InternalBriefResponse
    snapshot: InternalUsageSnapshotModel


class CompanyContextResponse(BaseModel):
    callsign: str
    external: Optional[ExternalContextCard] = None
    internal: Optional[InternalContextCard] = None
    templates: List[TemplateInfo] = Field(default_factory=list)
