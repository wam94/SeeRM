"""Data models for SeeRM intelligence and reporting system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MovementType(str, Enum):
    """Types of account movements."""

    TOP_GAINER = "top_gainer"
    TOP_LOSER = "top_loser"
    NEW_ACCOUNT = "new_account"
    SIGNIFICANT_CHANGE = "significant_change"
    STABLE = "stable"


class NewsType(str, Enum):
    """Categories of news items for intelligence digest."""

    FUNDING = "funding"  # 💰 Funding, investment, series rounds
    PRODUCT_LAUNCH = "product_launch"  # 🚀 New products, features, releases
    PARTNERSHIPS = "partnerships"  # 🤝 Strategic alliances, integrations, collaborations
    LEADERSHIP = "leadership"  # 👔 C-suite appointments, key hires, departures
    GROWTH_METRICS = "growth_metrics"  # 📈 User milestones, revenue, expansion
    LEGAL_REGULATORY = "legal_regulatory"  # ⚖️ Compliance, lawsuits, regulations
    TECHNICAL = "technical"  # 🔧 Platform changes, outages, security incidents
    ACQUISITION = "acquisition"  # 🏢 M&A, buyouts, mergers
    OTHER_NOTABLE = "other_notable"  # 📰 Significant but uncategorized


@dataclass
class NewsItem:
    """Individual news item with metadata."""

    title: str
    url: str
    source: str
    published_at: str  # ISO date string
    summary: Optional[str] = None
    news_type: NewsType = NewsType.OTHER_NOTABLE
    relevance_score: float = 0.0
    relevance_verdict: Optional[str] = None
    relevance_snapshot_id: Optional[str] = None
    relevance_reasons: List[str] = field(default_factory=list)
    sentiment: Optional[str] = None  # "positive", "negative", "neutral"
    company_mentions: List[str] = field(default_factory=list)


@dataclass
class Movement:
    """Account balance movement data."""

    callsign: str
    company_name: str
    current_balance: float
    previous_balance: Optional[float] = None
    percentage_change: Optional[float] = None
    movement_type: MovementType = MovementType.STABLE
    rank: Optional[int] = None
    is_new_account: bool = False
    products: List[str] = field(default_factory=list)


@dataclass
class CompanyProfile:
    """Company profile from Notion."""

    callsign: str
    company_name: str
    website: Optional[str] = None
    domain: Optional[str] = None
    owners: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    products: List[str] = field(default_factory=list)
    needs_dossier: bool = False
    notion_page_id: Optional[str] = None


@dataclass
class CompanyIntelligence:
    """Complete intelligence profile for a company."""

    profile: CompanyProfile
    movement: Optional[Movement] = None
    news_history: List[NewsItem] = field(default_factory=list)
    latest_intel: Optional[str] = None
    last_intel_date: Optional[datetime] = None


@dataclass
class ReportMetadata:
    """Metadata for generated reports."""

    report_id: str
    report_type: str
    generated_at: datetime
    data_sources: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: Optional[float] = None


@dataclass
class Report:
    """Base report structure."""

    metadata: ReportMetadata
    title: str
    content: Dict[str, Any]
    html: Optional[str] = None
    markdown: Optional[str] = None
    notion_page_id: Optional[str] = None
    email_sent: bool = False


@dataclass
class NewClientSummary:
    """Summary data for a new client."""

    callsign: str
    company_name: str
    initial_balance: float
    products: List[str]
    recent_news: List[NewsItem] = field(default_factory=list)
    similar_clients: List[str] = field(default_factory=list)


@dataclass
class WeeklyNewsDigest:
    """Weekly news digest structure."""

    week_of: str
    total_items: int
    by_type: Dict[NewsType, List[NewsItem]] = field(default_factory=dict)
    by_company: Dict[str, List[NewsItem]] = field(default_factory=dict)
    key_themes: List[str] = field(default_factory=list)
    notable_items: List[NewsItem] = field(default_factory=list)
    summary: Optional[str] = None


@dataclass
class CompanyDeepDive:
    """Company deep dive report structure."""

    company: CompanyIntelligence
    executive_summary: str
    metrics_analysis: Dict[str, Any]
    news_timeline: List[NewsItem]
    product_analysis: Dict[str, Any]
