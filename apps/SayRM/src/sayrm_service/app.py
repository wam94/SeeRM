"""FastAPI application wiring for SayRM."""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import Body, Depends, FastAPI, HTTPException

from .clients.internal_client import InternalUsageClient
from .clients.llm_client import LLMClient
from .clients.notion_client import ExternalContextClient
from .config import SayRMSettings
from .db import create_engine_for_path, init_db
from .models import (
    BriefRequest,
    CompanyContextResponse,
    ComposeDraftRequest,
    ComposeDraftResponse,
    DraftListResponse,
    DraftPreview,
    ExternalBriefResponse,
    ExternalContextCard,
    InternalBriefResponse,
    InternalContextCard,
    InternalUsageSnapshotModel,
    LabelRequest,
    TemplateInfo,
)
from .services.drafts import DraftService
from .services.summaries import ExternalBrief, InternalBrief, SummaryService
from .services.templates import TemplateService
from .store import DataStore


def build_app(settings: SayRMSettings | None = None) -> FastAPI:
    """Create a configured FastAPI instance."""
    settings = settings or SayRMSettings()
    engine = create_engine_for_path(settings.resolved_database_path())
    init_db(engine)

    store = DataStore(engine)
    template_service = TemplateService(settings.resolved_template_path())

    summary_service = SummaryService(
        notion_client=ExternalContextClient(settings),
        internal_client=InternalUsageClient(settings),
        llm_client=LLMClient(settings),
        store=store,
    )
    draft_service = DraftService(
        llm_client=LLMClient(settings),
        template_service=template_service,
        store=store,
    )

    app = FastAPI(title="SayRM Service", version="0.1.0")

    # Dependency factories
    def get_store() -> DataStore:
        return store

    def get_summary_service() -> SummaryService:
        return summary_service

    def get_draft_service() -> DraftService:
        return draft_service

    def get_template_service() -> TemplateService:
        return template_service

    def serialize_external_brief(brief: ExternalBrief) -> ExternalBriefResponse:
        return ExternalBriefResponse(
            summary_id=brief.summary_id,
            callsign=brief.callsign,
            company_name=brief.company_name,
            product=brief.product,
            news=brief.news,
            announcements=brief.announcements,
            raw_text=brief.raw_text,
            created_at=brief.created_at,
        )

    def serialize_internal_brief(brief: InternalBrief) -> InternalBriefResponse:
        return InternalBriefResponse(
            summary_id=brief.summary_id,
            callsign=brief.callsign,
            notes=brief.notes,
            status=brief.snapshot.status,
            created_at=brief.created_at,
        )

    def serialize_templates(service: TemplateService) -> List[TemplateInfo]:
        return [
            TemplateInfo(
                id=template.id,
                title=template.title,
                description=template.description,
                body=template.body,
                tags=template.tags,
            )
            for template in service.list_templates()
        ]

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model": summary_service.llm_model,
            "db": str(settings.resolved_database_path()),
            "timestamp": datetime.utcnow().isoformat(),
        }

    @app.post("/companies/{callsign}/briefs/external", response_model=ExternalBriefResponse)
    def create_external_brief(
        callsign: str,
        request: BriefRequest,
        svc: SummaryService = Depends(get_summary_service),
    ) -> ExternalBriefResponse:
        try:
            brief = svc.build_external_brief(callsign, manual_highlights=request.manual_highlights)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive, surfaced for debugging
            raise HTTPException(
                status_code=502,
                detail="Failed to build external brief.",
            ) from exc
        return serialize_external_brief(brief)

    @app.post("/companies/{callsign}/briefs/internal", response_model=InternalBriefResponse)
    def create_internal_brief(
        callsign: str,
        svc: SummaryService = Depends(get_summary_service),
    ) -> InternalBriefResponse:
        brief = svc.build_internal_brief(callsign)
        return serialize_internal_brief(brief)

    @app.post("/companies/{callsign}/context", response_model=CompanyContextResponse)
    def build_company_context(
        callsign: str,
        request: BriefRequest = Body(default_factory=BriefRequest),
        svc: SummaryService = Depends(get_summary_service),
        templates: TemplateService = Depends(get_template_service),
    ) -> CompanyContextResponse:
        try:
            external_brief = svc.build_external_brief(
                callsign, manual_highlights=request.manual_highlights
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        internal_brief = svc.build_internal_brief(callsign)

        external_card = ExternalContextCard(
            brief=serialize_external_brief(external_brief),
            context=external_brief.context_snapshot,
        )
        internal_card = InternalContextCard(
            brief=serialize_internal_brief(internal_brief),
            snapshot=InternalUsageSnapshotModel(
                status=internal_brief.snapshot.status,
                owners=internal_brief.snapshot.owners,
                products=internal_brief.snapshot.products,
                notes=internal_brief.snapshot.notes,
                raw=internal_brief.snapshot.raw,
            ),
        )
        return CompanyContextResponse(
            callsign=callsign,
            external=external_card,
            internal=internal_card,
            templates=serialize_templates(templates),
        )

    @app.get("/templates", response_model=List[TemplateInfo])
    def list_templates(
        service: TemplateService = Depends(get_template_service),
    ) -> List[TemplateInfo]:
        return serialize_templates(service)

    @app.get("/templates/{template_id}", response_model=TemplateInfo)
    def get_template(
        template_id: str,
        service: TemplateService = Depends(get_template_service),
    ) -> TemplateInfo:
        template = service.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return TemplateInfo(
            id=template.id,
            title=template.title,
            description=template.description,
            body=template.body,
            tags=template.tags,
        )

    @app.post("/drafts/compose", response_model=ComposeDraftResponse)
    def compose_draft(
        request: ComposeDraftRequest,
        svc: DraftService = Depends(get_draft_service),
        store: DataStore = Depends(get_store),
    ) -> ComposeDraftResponse:
        external_text = request.external_summary
        internal_text = request.internal_summary
        if not external_text:
            latest = store.latest_summary(request.callsign, "external")
            if latest:
                external_text = latest.content
        if not internal_text:
            latest = store.latest_summary(request.callsign, "internal")
            if latest:
                internal_text = latest.content

        result = svc.compose(
            request.callsign,
            template_id=request.template_id,
            instructions=request.instructions,
            manual_snippets=request.manual_snippets,
            external_summary=external_text,
            internal_summary=internal_text,
        )
        return ComposeDraftResponse(
            draft_id=result.draft_id,
            callsign=result.callsign,
            template_id=result.template_id,
            body=result.body,
            created_at=result.created_at,
        )

    @app.get("/drafts/recent", response_model=DraftListResponse)
    def recent_drafts(
        callsign: str | None = None,
        limit: int = 5,
        store: DataStore = Depends(get_store),
    ) -> DraftListResponse:
        drafts = store.list_recent_drafts(callsign, limit)
        return DraftListResponse(
            drafts=[
                DraftPreview(
                    id=draft.id,
                    callsign=draft.callsign,
                    template_id=draft.template_id,
                    body=draft.body,
                    created_at=draft.created_at,
                )
                for draft in drafts
            ]
        )

    @app.post("/drafts/labels")
    def add_labels(
        request: LabelRequest,
        store: DataStore = Depends(get_store),
    ) -> dict:
        labels = list(request.labels.items())
        applied = store.add_labels(request.draft_id, labels, created_by=request.created_by)
        return {
            "draft_id": request.draft_id,
            "applied": [{"key": label.key, "value": label.value} for label in applied],
        }

    return app


app = build_app()
