"""
Data models and type definitions for SeeRM application.

Provides type-safe data structures with validation for all application data.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowType(str, Enum):
    """Types of workflows in the system."""

    DIGEST = "digest"
    NEWS = "news"
    BASELINE = "baseline"


class CompanyStatus(str, Enum):
    """Company status in the system."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    NEW = "new"
    REMOVED = "removed"


class NewsItemSource(str, Enum):
    """Sources of news items."""

    RSS = "rss"
    GOOGLE_SEARCH = "google_search"
    MANUAL = "manual"


class ProcessingStatus(str, Enum):
    """Status of processing operations."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# Base Models


class BaseEntity(BaseModel):
    """Base class for all entities."""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(use_enum_values=True, validate_assignment=True)


# Company and Account Models


class Company(BaseEntity):
    """Company/Account information."""

    callsign: str = Field(..., min_length=1, max_length=200)
    dba: Optional[str] = Field(None, max_length=1000)
    website: Optional[str] = Field(None, max_length=500)
    domain_root: Optional[str] = Field(None, max_length=200)
    blog_url: Optional[str] = Field(None, max_length=500)

    # Business information
    beneficial_owners: List[str] = Field(default_factory=list)
    aka_names: Optional[str] = Field(None, max_length=1000)
    industry_tags: Optional[str] = Field(None, max_length=500)

    # Status tracking
    status: CompanyStatus = Field(default=CompanyStatus.ACTIVE)
    is_new_account: bool = Field(default=False)
    is_removed_account: bool = Field(default=False)
    needs_dossier: bool = Field(default=False)

    # Change tracking
    dba_changed: bool = Field(default=False)
    website_changed: bool = Field(default=False)
    owners_changed: bool = Field(default=False)
    balance_changed: bool = Field(default=False)
    any_change: bool = Field(default=False)

    # Financial data
    curr_balance: Optional[float] = Field(None)
    prev_balance: Optional[float] = Field(None)
    balance_delta: Optional[float] = Field(None)
    balance_pct_delta_pct: Optional[float] = Field(None)

    # Product changes
    product_flips_json: Optional[str] = Field(None)

    @field_validator("callsign")
    @classmethod
    def validate_callsign(cls, v):
        """Normalize callsign values."""
        return v.strip().lower()

    @field_validator("beneficial_owners", mode="before")
    @classmethod
    def parse_owners(cls, v):
        """Parse lists of beneficial owners from strings."""
        if isinstance(v, str):
            try:
                import json

                return json.loads(v)
            except Exception:
                # Fallback to comma-separated parsing
                return [owner.strip().strip('"') for owner in v.split(",") if owner.strip()]
        return v or []

    @model_validator(mode="after")
    def calculate_balance_delta(self):
        """Populate balance delta from current/previous balances."""
        if (
            self.balance_delta is None
            and self.curr_balance is not None
            and self.prev_balance is not None
        ):
            self.balance_delta = self.curr_balance - self.prev_balance
        return self


class AccountMovement(BaseEntity):
    """Represents account balance movement data."""

    callsign: str
    percentage_change: float
    balance_delta: Optional[float] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "callsign": "97labs",
                "percentage_change": 15.5,
                "balance_delta": 50000.0,
            }
        }
    )


# News and Intelligence Models


class NewsItem(BaseEntity):
    """Individual news item."""

    title: str = Field(..., min_length=1, max_length=500)
    url: str = Field(..., max_length=1000)
    source: str = Field(..., max_length=200)
    published_at: Optional[Union[str, datetime]] = None

    # Metadata
    source_type: NewsItemSource = Field(default=NewsItemSource.MANUAL)
    callsign: Optional[str] = Field(None, max_length=200)
    relevance_score: Optional[float] = Field(default=None)
    relevance_verdict: Optional[str] = Field(default=None, max_length=32)
    relevance_snapshot_id: Optional[str] = Field(default=None, max_length=100)
    relevance_reasons: List[str] = Field(default_factory=list)

    @field_validator("published_at", mode="before")
    @classmethod
    def parse_published_at(cls, v):
        """Normalize published dates to ISO strings."""
        if v is None or v == "":
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v).strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        """Ensure URLs include a scheme."""
        if not v.startswith(("http://", "https://")):
            return f"https://{v}"
        return v


class CompanyIntelligence(BaseEntity):
    """Intelligence data for a company."""

    callsign: str
    date_collected: date = Field(default_factory=date.today)

    # News items
    news_items: List[NewsItem] = Field(default_factory=list)

    # Summary
    summary: Optional[str] = Field(None, max_length=5000)

    # Product changes
    product_starts: List[Dict[str, str]] = Field(default_factory=list)
    product_stops: List[Dict[str, str]] = Field(default_factory=list)

    # Processing metadata
    processing_status: ProcessingStatus = Field(default=ProcessingStatus.PENDING)
    error_message: Optional[str] = None

    @field_validator("news_items", mode="before")
    @classmethod
    def validate_news_items(cls, v):
        """Coerce dictionaries into NewsItem instances."""
        if isinstance(v, list):
            return [NewsItem(**item) if isinstance(item, dict) else item for item in v]
        return v or []


# Digest Models


class DigestStats(BaseModel):
    """Statistics for the weekly digest."""

    total_accounts: int = Field(default=0, ge=0)
    changed_accounts: int = Field(default=0, ge=0)
    new_accounts: int = Field(default=0, ge=0)
    removed_accounts: int = Field(default=0, ge=0)
    total_product_flips: int = Field(default=0, ge=0)


class DigestData(BaseEntity):
    """Complete digest data structure."""

    # Metadata
    generated_date: date = Field(default_factory=date.today)
    subject: str = Field(default="Client Weekly Digest")

    # Statistics
    stats: DigestStats = Field(default_factory=DigestStats)

    # Movement data
    top_pct_gainers: List[AccountMovement] = Field(default_factory=list)
    top_pct_losers: List[AccountMovement] = Field(default_factory=list)

    # Product changes
    product_starts: List[Dict[str, str]] = Field(default_factory=list)
    product_stops: List[Dict[str, str]] = Field(default_factory=list)


# Processing Models


class ProcessingResult(BaseEntity):
    """Result of a processing operation."""

    workflow_type: WorkflowType
    status: ProcessingStatus = Field(default=ProcessingStatus.PENDING)

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    # Results
    items_processed: int = Field(default=0, ge=0)
    items_successful: int = Field(default=0, ge=0)
    items_failed: int = Field(default=0, ge=0)

    # Error tracking
    error_message: Optional[str] = None
    error_details: Dict[str, Any] = Field(default_factory=dict)

    # Data
    data: Dict[str, Any] = Field(default_factory=dict)


class BatchProcessingResult(BaseEntity):
    """Result of batch processing operations."""

    workflow_type: WorkflowType
    correlation_id: str

    # Overall status
    status: ProcessingStatus = Field(default=ProcessingStatus.PENDING)

    # Individual results
    results: List[ProcessingResult] = Field(default_factory=list)

    # Summary stats
    total_items: int = Field(default=0, ge=0)
    successful_items: int = Field(default=0, ge=0)
    failed_items: int = Field(default=0, ge=0)
    skipped_items: int = Field(default=0, ge=0)

    # Performance
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_duration_seconds: Optional[float] = None

    @model_validator(mode="after")
    def calculate_summary_stats(self):
        """Aggregate summary statistics from individual results."""
        if self.results:
            self.total_items = sum(r.items_processed for r in self.results)
            self.successful_items = sum(r.items_successful for r in self.results)
            self.failed_items = sum(r.items_failed for r in self.results)
        return self


# Notion Models


class NotionPage(BaseEntity):
    """Represents a Notion page."""

    page_id: str
    database_id: str
    title: str
    properties: Dict[str, Any] = Field(default_factory=dict)

    # Status
    created: bool = Field(default=False)
    updated: bool = Field(default=False)


class NotionUpdateOperation(BaseModel):
    """Represents a Notion update operation."""

    operation_type: str  # "create", "update", "delete"
    page_id: Optional[str] = None
    database_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)

    # For dry-run mode
    dry_run: bool = Field(default=False)
    would_create: bool = Field(default=False)
    would_update: bool = Field(default=False)


# API Response Models


class APIResponse(BaseModel):
    """Standard API response structure."""

    success: bool = Field(default=True)
    message: str = Field(default="")
    data: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)

    # Timing
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: Optional[float] = None
