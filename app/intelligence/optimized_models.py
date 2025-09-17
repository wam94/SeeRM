"""
Optimized data models for SeeRM intelligence system with reduced memory footprint.

Uses __slots__ for memory efficiency and lazy loading for heavy fields.
"""

from datetime import datetime
from enum import Enum
from functools import cached_property
from typing import Any, Callable, Dict, List, Optional, Union


class MovementType(str, Enum):
    """Types of account movements."""

    TOP_GAINER = "top_gainer"
    TOP_LOSER = "top_loser"
    NEW_ACCOUNT = "new_account"
    SIGNIFICANT_CHANGE = "significant_change"
    STABLE = "stable"


class NewsType(str, Enum):
    """Categories of news items for intelligence digest."""

    FUNDING = "funding"
    PRODUCT_LAUNCH = "product_launch"
    PARTNERSHIPS = "partnerships"
    LEADERSHIP = "leadership"
    GROWTH_METRICS = "growth_metrics"
    LEGAL_REGULATORY = "legal_regulatory"
    TECHNICAL = "technical"
    ACQUISITION = "acquisition"
    OTHER_NOTABLE = "other_notable"


class OptimizedNewsItem:
    """Memory-efficient news item with __slots__."""

    __slots__ = (
        "_title",
        "_url",
        "_source",
        "_published_at",
        "_summary",
        "_news_type",
        "_relevance_score",
        "_sentiment",
        "_company_mentions",
    )

    def __init__(
        self,
        title: str,
        url: str,
        source: str,
        published_at: str,
        summary: Optional[str] = None,
        news_type: NewsType = NewsType.OTHER_NOTABLE,
        relevance_score: float = 0.0,
        sentiment: Optional[str] = None,
        company_mentions: Optional[List[str]] = None,
    ):
        """Initialise the news item with compact storage."""
        self._title = title
        self._url = url
        self._source = source
        self._published_at = published_at
        self._summary = summary
        self._news_type = news_type
        self._relevance_score = relevance_score
        self._sentiment = sentiment
        self._company_mentions = company_mentions or []

    @property
    def title(self) -> str:  # noqa: D102
        return self._title

    @property
    def url(self) -> str:  # noqa: D102
        return self._url

    @property
    def source(self) -> str:  # noqa: D102
        return self._source

    @property
    def published_at(self) -> str:  # noqa: D102
        return self._published_at

    @property
    def summary(self) -> Optional[str]:  # noqa: D102
        return self._summary

    @property
    def news_type(self) -> NewsType:  # noqa: D102
        return self._news_type

    @news_type.setter
    def news_type(self, value: NewsType):  # noqa: D102
        self._news_type = value

    @property
    def relevance_score(self) -> float:  # noqa: D102
        return self._relevance_score

    @property
    def sentiment(self) -> Optional[str]:  # noqa: D102
        return self._sentiment

    @property
    def company_mentions(self) -> List[str]:  # noqa: D102
        return self._company_mentions

    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary for compatibility."""
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "summary": self.summary,
            "news_type": self.news_type.value,
            "relevance_score": self.relevance_score,
            "sentiment": self.sentiment,
            "company_mentions": self.company_mentions,
        }


class OptimizedMovement:
    """Memory-efficient movement data with __slots__."""

    __slots__ = (
        "_callsign",
        "_company_name",
        "_current_balance",
        "_previous_balance",
        "_percentage_change",
        "_movement_type",
        "_rank",
        "_is_new_account",
        "_products",
    )

    def __init__(
        self,
        callsign: str,
        company_name: str,
        current_balance: float,
        previous_balance: Optional[float] = None,
        percentage_change: Optional[float] = None,
        movement_type: MovementType = MovementType.STABLE,
        rank: Optional[int] = None,
        is_new_account: bool = False,
        products: Optional[List[str]] = None,
    ):
        """Initialise a movement record using slot-based storage."""
        self._callsign = callsign
        self._company_name = company_name
        self._current_balance = current_balance
        self._previous_balance = previous_balance
        self._percentage_change = percentage_change
        self._movement_type = movement_type
        self._rank = rank
        self._is_new_account = is_new_account
        self._products = products or []

    @property
    def callsign(self) -> str:  # noqa: D102
        return self._callsign

    @property
    def company_name(self) -> str:  # noqa: D102
        return self._company_name

    @property
    def current_balance(self) -> float:  # noqa: D102
        return self._current_balance

    @property
    def percentage_change(self) -> Optional[float]:  # noqa: D102
        return self._percentage_change

    @property
    def movement_type(self) -> MovementType:  # noqa: D102
        return self._movement_type

    @movement_type.setter
    def movement_type(self, value: MovementType):  # noqa: D102
        self._movement_type = value

    @property
    def rank(self) -> Optional[int]:  # noqa: D102
        return self._rank

    @property
    def is_new_account(self) -> bool:  # noqa: D102
        return self._is_new_account

    @property
    def products(self) -> List[str]:  # noqa: D102
        return self._products


class LazyCompanyProfile:
    """
    Company profile with lazy loading of heavy fields.

    Delays loading of certain fields until they're actually accessed.
    """

    __slots__ = (
        "_callsign",
        "_company_name",
        "_website",
        "_domain",
        "_owners",
        "_tags",
        "_products",
        "_needs_dossier",
        "_notion_page_id",
        "_lazy_loader",
        "_loaded_fields",
    )

    def __init__(
        self,
        callsign: str,
        company_name: str,
        website: Optional[str] = None,
        domain: Optional[str] = None,
        lazy_loader: Optional[Callable[[str], Dict[str, Any]]] = None,
    ):
        """Initialise a lazily fetched company profile."""
        self._callsign = callsign
        self._company_name = company_name
        self._website = website
        self._domain = domain
        self._lazy_loader = lazy_loader
        self._loaded_fields = set()

        # These will be loaded lazily
        self._owners: Optional[List[str]] = None
        self._tags: Optional[List[str]] = None
        self._products: Optional[List[str]] = None
        self._needs_dossier: Optional[bool] = None
        self._notion_page_id: Optional[str] = None

    def _load_if_needed(self, field: str) -> None:
        """Load field data if not already loaded."""
        if field not in self._loaded_fields and self._lazy_loader:
            data = self._lazy_loader(self._callsign)
            if data:
                self._owners = data.get("owners", [])
                self._tags = data.get("tags", [])
                self._products = data.get("products", [])
                self._needs_dossier = data.get("needs_dossier", False)
                self._notion_page_id = data.get("notion_page_id")
                self._loaded_fields.update(
                    ["owners", "tags", "products", "needs_dossier", "notion_page_id"]
                )

    @property
    def callsign(self) -> str:  # noqa: D102
        return self._callsign

    @property
    def company_name(self) -> str:  # noqa: D102
        return self._company_name

    @property
    def website(self) -> Optional[str]:  # noqa: D102
        return self._website

    @property
    def domain(self) -> Optional[str]:  # noqa: D102
        return self._domain

    @property
    def owners(self) -> List[str]:  # noqa: D102
        self._load_if_needed("owners")
        return self._owners or []

    @property
    def tags(self) -> List[str]:  # noqa: D102
        self._load_if_needed("tags")
        return self._tags or []

    @property
    def products(self) -> List[str]:  # noqa: D102
        self._load_if_needed("products")
        return self._products or []

    @property
    def needs_dossier(self) -> bool:  # noqa: D102
        self._load_if_needed("needs_dossier")
        return self._needs_dossier or False

    @property
    def notion_page_id(self) -> Optional[str]:  # noqa: D102
        self._load_if_needed("notion_page_id")
        return self._notion_page_id


class OptimizedCompanyIntelligence:
    """Complete intelligence profile with optimized memory usage."""

    __slots__ = (
        "_profile",
        "_movement",
        "_news_history",
        "_latest_intel",
        "_last_intel_date",
        "_cached_summary",
    )

    def __init__(
        self,
        profile: Union[LazyCompanyProfile, Any],
        movement: Optional[OptimizedMovement] = None,
        news_history: Optional[List[OptimizedNewsItem]] = None,
        latest_intel: Optional[str] = None,
        last_intel_date: Optional[datetime] = None,
    ):
        """Initialise optimized intelligence for a company."""
        self._profile = profile
        self._movement = movement
        self._news_history = news_history or []
        self._latest_intel = latest_intel
        self._last_intel_date = last_intel_date
        self._cached_summary = None

    @property
    def profile(self):  # noqa: D102
        return self._profile

    @property
    def movement(self):  # noqa: D102
        return self._movement

    @property
    def news_history(self):  # noqa: D102
        return self._news_history

    @cached_property
    def news_summary(self) -> str:
        """Generate and cache news summary."""
        if not self._news_history:
            return "No recent news"

        news_by_type = {}
        for item in self._news_history:
            if item.news_type not in news_by_type:
                news_by_type[item.news_type] = 0
            news_by_type[item.news_type] += 1

        summary_parts = []
        for news_type, count in news_by_type.items():
            summary_parts.append(f"{news_type.value}: {count}")

        return f"{len(self._news_history)} items - {', '.join(summary_parts)}"


# Conversion utilities for backward compatibility


def convert_to_optimized_news_item(old_item) -> OptimizedNewsItem:
    """Convert old NewsItem to optimized version."""
    return OptimizedNewsItem(
        title=old_item.title,
        url=old_item.url,
        source=old_item.source,
        published_at=old_item.published_at,
        summary=old_item.summary,
        news_type=old_item.news_type,
        relevance_score=old_item.relevance_score,
        sentiment=old_item.sentiment,
        company_mentions=old_item.company_mentions,
    )


def convert_to_optimized_movement(old_movement) -> OptimizedMovement:
    """Convert old Movement to optimized version."""
    return OptimizedMovement(
        callsign=old_movement.callsign,
        company_name=old_movement.company_name,
        current_balance=old_movement.current_balance,
        previous_balance=getattr(old_movement, "previous_balance", None),
        percentage_change=old_movement.percentage_change,
        movement_type=old_movement.movement_type,
        rank=old_movement.rank,
        is_new_account=old_movement.is_new_account,
        products=old_movement.products,
    )


# Memory usage comparison utility


def get_memory_usage(obj) -> int:
    """Get approximate memory usage of an object in bytes."""
    import sys

    if hasattr(obj, "__slots__"):
        # For slotted classes, sum up slot values
        size = sys.getsizeof(obj)
        for slot in obj.__slots__:
            if hasattr(obj, slot):
                attr = getattr(obj, slot)
                if attr is not None:
                    size += sys.getsizeof(attr)
        return size
    else:
        # For regular classes with __dict__
        return sys.getsizeof(obj) + sys.getsizeof(obj.__dict__)
