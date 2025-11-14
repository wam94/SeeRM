"""High-level persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional

from sqlmodel import Session

from .db import Company, Draft, DraftLabel, Summary, get_session, latest_summary, recent_drafts


def _serialize(data: Optional[object]) -> Optional[str]:
    if data is None:
        return None
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False)
    except TypeError:
        return str(data)


class DataStore:
    """Convenience wrapper for common DB operations."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def session(self) -> Session:
        return get_session(self._engine)

    # ------------------------------------------------------------------ #
    # Company helpers
    # ------------------------------------------------------------------ #

    def touch_company(self, callsign: str, name: Optional[str]) -> Company:
        normalized = callsign.strip().lower()
        with self.session() as session:
            company = session.get(Company, normalized)
            if not company:
                company = Company(callsign=normalized, name=name)
            else:
                if name and not company.name:
                    company.name = name
            company.last_seen = datetime.utcnow()
            session.add(company)
            session.commit()
            session.refresh(company)
            return company

    # ------------------------------------------------------------------ #
    # Summary helpers
    # ------------------------------------------------------------------ #

    def save_summary(
        self,
        callsign: str,
        summary_type: str,
        content: str,
        *,
        raw_context: Optional[object] = None,
        model_used: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Summary:
        company = self.touch_company(callsign, None)
        with self.session() as session:
            summary = Summary(
                callsign=company.callsign,
                summary_type=summary_type,
                content=content,
                raw_context=_serialize(raw_context),
                model_used=model_used,
                source=source,
            )
            session.add(summary)
            session.commit()
            session.refresh(summary)
            return summary

    def latest_summary(self, callsign: str, summary_type: str) -> Optional[Summary]:
        normalized = callsign.strip().lower()
        with self.session() as session:
            return latest_summary(session, normalized, summary_type)

    # ------------------------------------------------------------------ #
    # Draft helpers
    # ------------------------------------------------------------------ #

    def save_draft(
        self,
        callsign: str,
        *,
        template_id: Optional[str],
        body: str,
        prompt: str,
        context_snapshot: Optional[object],
        model_used: Optional[str],
    ) -> Draft:
        company = self.touch_company(callsign, None)
        with self.session() as session:
            draft = Draft(
                callsign=company.callsign,
                template_id=template_id,
                body=body,
                prompt=prompt,
                context_snapshot=_serialize(context_snapshot),
                model_used=model_used,
            )
            session.add(draft)
            session.commit()
            session.refresh(draft)
            return draft

    def fetch_draft(self, draft_id: int) -> Optional[Draft]:
        with self.session() as session:
            return session.get(Draft, draft_id)

    def list_recent_drafts(self, callsign: Optional[str] = None, limit: int = 5) -> list[Draft]:
        target = callsign.strip().lower() if callsign else None
        with self.session() as session:
            return recent_drafts(session, target, limit)

    # ------------------------------------------------------------------ #
    # Labels
    # ------------------------------------------------------------------ #

    def add_labels(
        self,
        draft_id: int,
        labels: Iterable[tuple[str, str]],
        created_by: Optional[str] = None,
    ) -> list[DraftLabel]:
        with self.session() as session:
            draft = session.get(Draft, draft_id)
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            stored: list[DraftLabel] = []
            for key, value in labels:
                label = DraftLabel(
                    draft_id=draft.id,
                    key=key,
                    value=value,
                    created_by=created_by,
                )
                session.add(label)
                stored.append(label)
            session.commit()
            for label in stored:
                session.refresh(label)
            return stored
