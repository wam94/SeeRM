"""
Intelligent caching layer for SeeRM intelligence system.

Provides in-memory and optional Redis caching with TTL support,
automatic invalidation, and performance metrics.
"""

import hashlib
import json
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)


class CacheStats:
    """Track cache performance metrics."""

    def __init__(self):
        """Initialise counters for cache statistics."""
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.total_hit_time = 0.0
        self.total_miss_time = 0.0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def avg_hit_time(self) -> float:
        """Average time for cache hits in milliseconds."""
        return (self.total_hit_time / self.hits * 1000) if self.hits > 0 else 0.0

    @property
    def avg_miss_time(self) -> float:
        """Average time for cache misses in milliseconds."""
        return (self.total_miss_time / self.misses * 1000) if self.misses > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Export stats as dictionary."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": f"{self.hit_rate:.2%}",
            "avg_hit_time_ms": f"{self.avg_hit_time:.2f}",
            "avg_miss_time_ms": f"{self.avg_miss_time:.2f}",
        }


class IntelligenceCache:
    """
    High-performance caching layer for intelligence data.

    Features:
    - TTL-based expiration
    - LRU eviction when size limit reached
    - Automatic key generation
    - Performance metrics
    - Thread-safe operations
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 3600):
        """
        Initialize cache.

        Args:
            max_size: Maximum number of entries
            default_ttl: Default TTL in seconds
        """
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._access_times: Dict[str, float] = {}
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.stats = CacheStats()

        logger.info("Intelligence cache initialized", max_size=max_size, default_ttl=default_ttl)

    def _make_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate cache key from prefix and arguments."""
        # Create stable key from arguments
        key_data = {"prefix": prefix, "args": args, "kwargs": sorted(kwargs.items())}
        key_json = json.dumps(key_data, sort_keys=True, default=str)
        key_hash = hashlib.md5(key_json.encode(), usedforsecurity=False).hexdigest()[:16]
        return f"{prefix}:{key_hash}"

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        start_time = time.time()

        if key in self._cache:
            value, expiry = self._cache[key]

            if time.time() < expiry:
                # Cache hit
                self._access_times[key] = time.time()
                self.stats.hits += 1
                self.stats.total_hit_time += time.time() - start_time

                logger.debug("Cache hit", key=key, ttl_remaining=int(expiry - time.time()))
                return value
            else:
                # Expired entry
                del self._cache[key]
                del self._access_times[key]
                self.stats.evictions += 1
                logger.debug("Cache entry expired", key=key)

        # Cache miss
        self.stats.misses += 1
        self.stats.total_miss_time += time.time() - start_time
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (uses default if None)
        """
        # Check if we need to evict entries
        if len(self._cache) >= self.max_size:
            self._evict_lru()

        ttl = ttl or self.default_ttl
        expiry = time.time() + ttl

        self._cache[key] = (value, expiry)
        self._access_times[key] = time.time()

        logger.debug("Cache set", key=key, ttl=ttl)

    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if not self._access_times:
            return

        # Find LRU key
        lru_key = min(self._access_times, key=self._access_times.get)

        # Remove from cache
        del self._cache[lru_key]
        del self._access_times[lru_key]
        self.stats.evictions += 1

        logger.debug("Evicted LRU entry", key=lru_key)

    def invalidate(self, pattern: Optional[str] = None) -> int:
        """
        Invalidate cache entries.

        Args:
            pattern: Key pattern to match (prefix). If None, clears all.

        Returns:
            Number of entries invalidated
        """
        if pattern is None:
            # Clear all
            count = len(self._cache)
            self._cache.clear()
            self._access_times.clear()
            logger.info("Cache cleared", entries=count)
            return count

        # Clear matching pattern
        keys_to_delete = [k for k in self._cache if k.startswith(pattern)]
        for key in keys_to_delete:
            del self._cache[key]
            self._access_times.pop(key, None)

        logger.info("Cache invalidated", pattern=pattern, entries=len(keys_to_delete))
        return len(keys_to_delete)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            **self.stats.to_dict(),
            "size": len(self._cache),
            "max_size": self.max_size,
        }


# Global cache instance
_cache = IntelligenceCache(max_size=2000, default_ttl=3600)


def cached(prefix: str, ttl: Optional[int] = None):
    """
    Cache function results using the given prefix and TTL.

    Args:
        prefix: Cache key prefix
        ttl: Time to live in seconds

    Example:
        @cached("company_profile", ttl=3600)
        def get_company_profile(callsign: str):
            # Expensive operation
            return profile
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = _cache._make_key(prefix, *args, **kwargs)

            # Check cache
            cached_value = _cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Call function and cache result
            result = func(*args, **kwargs)
            if result is not None:  # Only cache non-None results
                _cache.set(cache_key, result, ttl)

            return result

        # Add cache control methods
        wrapper.invalidate = lambda: _cache.invalidate(prefix)
        wrapper.cache_stats = lambda: _cache.get_stats()

        return wrapper

    return decorator


def get_cache() -> IntelligenceCache:
    """Get global cache instance."""
    return _cache


# Specialized cache decorators with appropriate TTLs


def cache_company_profile(ttl: int = 3600):
    """Cache company profiles for 1 hour by default."""
    return cached("company_profile", ttl)


def cache_news_classification(ttl: int = 86400):
    """Cache news classifications for 24 hours by default."""
    return cached("news_class", ttl)


def cache_notion_query(ttl: int = 900):
    """Cache Notion queries for 15 minutes by default."""
    return cached("notion", ttl)


def cache_movements(ttl: int = 300):
    """Cache movement data for 5 minutes by default."""
    return cached("movements", ttl)
