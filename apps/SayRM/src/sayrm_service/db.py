"""Database models and helpers for SayRM."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, Text
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select


class Company(SQLModel, table=True):
    """Tracked company metadata keyed by callsign."""

    callsign: str = Field(primary_key=True)
    name: Optional[str] = None
    last_seen: datetime = Field(default_factory=datetime.utcnow)


class Summary(SQLModel, table=True):
    """External or internal brief captured from upstream sources."""

    id: Optional[int] = Field(default=None, primary_key=True)
    callsign: str = Field(foreign_key="company.callsign")
    summary_type: str
    content: str
    raw_context: Optional[str] = Field(default=None, sa_column=Column(Text))
    model_used: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source: Optional[str] = None


class Draft(SQLModel, table=True):
    """LLM generated drafts."""

    id: Optional[int] = Field(default=None, primary_key=True)
    callsign: str = Field(foreign_key="company.callsign")
    template_id: Optional[str] = None
    body: str
    prompt: Optional[str] = Field(default=None, sa_column=Column(Text))
    context_snapshot: Optional[str] = Field(default=None, sa_column=Column(Text))
    model_used: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DraftLabel(SQLModel, table=True):
    """Manual labels attached to drafts for future fine-tuning."""

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: int = Field(foreign_key="draft.id")
    key: str
    value: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None


def create_engine_for_path(db_path: Path) -> Engine:
    """Return an SQLite engine for the provided path."""
    connection = f"sqlite:///{db_path}"
    return create_engine(
        connection,
        echo=False,
        connect_args={"check_same_thread": False},
    )


def init_db(engine: Engine) -> None:
    """Ensure all tables exist."""
    SQLModel.metadata.create_all(engine)


def get_session(engine: Engine) -> Session:
    """Create a session bound to the shared engine."""
    return Session(engine)


def latest_summary(session: Session, callsign: str, summary_type: str) -> Optional[Summary]:
    """Return the most recent summary of a given type."""
    statement = (
        select(Summary)
        .where(Summary.callsign == callsign, Summary.summary_type == summary_type)
        .order_by(Summary.created_at.desc())
    )
    return session.exec(statement).first()


def recent_drafts(session: Session, callsign: Optional[str] = None, limit: int = 5) -> list[Draft]:
    """Return recent drafts optionally filtered by callsign."""
    statement = select(Draft).order_by(Draft.created_at.desc()).limit(limit)
    if callsign:
        statement = statement.where(Draft.callsign == callsign)
    return list(session.exec(statement))
