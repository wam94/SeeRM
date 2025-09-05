"""
Connection pooling and session management for API clients.

Provides reusable HTTP sessions with connection pooling, retry logic,
and automatic connection pre-warming for improved reliability.
"""

import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
import structlog
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

logger = structlog.get_logger(__name__)


class ConnectionPool:
    """
    Manages pooled HTTP connections for API clients.

    Features:
    - Connection pooling per host
    - Automatic retry with exponential backoff
    - Session reuse across requests
    - Connection pre-warming
    - SSL/TLS optimization
    """

    _instance: Optional["ConnectionPool"] = None
    _sessions: Dict[str, requests.Session] = {}

    def __new__(cls):
        """Singleton pattern for global connection pool."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize connection pool if not already done."""
        if not self._initialized:
            self._sessions = {}
            self._initialized = True
            logger.info("Connection pool initialized")

    def get_session(
        self,
        base_url: str,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        pool_connections: int = 10,
        pool_maxsize: int = 20,
        timeout: tuple = (10, 30),
    ) -> requests.Session:
        """
        Get or create a session for a specific API endpoint.

        Args:
            base_url: Base URL for the API
            max_retries: Maximum number of retries
            backoff_factor: Backoff factor for retries
            pool_connections: Number of connection pools
            pool_maxsize: Maximum size of connection pool
            timeout: (connect_timeout, read_timeout) in seconds

        Returns:
            Configured requests.Session
        """
        # Extract host from URL
        parsed = urlparse(base_url)
        host = f"{parsed.scheme}://{parsed.netloc}"

        # Check if session exists
        if host not in self._sessions:
            session = self._create_session(
                max_retries=max_retries,
                backoff_factor=backoff_factor,
                pool_connections=pool_connections,
                pool_maxsize=pool_maxsize,
            )

            # Set default timeout
            session.timeout = timeout

            # Store session
            self._sessions[host] = session

            logger.info(
                "Created new session", host=host, pool_size=pool_maxsize, max_retries=max_retries
            )

        return self._sessions[host]

    def _create_session(
        self, max_retries: int, backoff_factor: float, pool_connections: int, pool_maxsize: int
    ) -> requests.Session:
        """Create a new configured session."""
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"],
            respect_retry_after_header=True,
        )

        # Create adapter with connection pooling
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            pool_block=False,
        )

        # Mount adapter for both HTTP and HTTPS
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set headers for better compatibility
        session.headers.update(
            {
                "User-Agent": "SeeRM-Intelligence/2.0",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )

        return session

    def prewarm_connections(self, urls: list) -> Dict[str, bool]:
        """
        Pre-warm connections to reduce SSL handshake time.

        Args:
            urls: List of URLs to pre-warm

        Returns:
            Dictionary mapping URL to success status
        """
        results = {}

        logger.info(f"Pre-warming {len(urls)} connections")

        for url in urls:
            try:
                # Get or create session
                session = self.get_session(url)

                # Make a lightweight HEAD request
                start_time = time.time()
                response = session.head(url, timeout=5, allow_redirects=True)
                elapsed = time.time() - start_time

                # Check if successful
                success = response.status_code < 400
                results[url] = success

                logger.debug(
                    "Connection pre-warmed",
                    url=url,
                    status=response.status_code,
                    elapsed_ms=int(elapsed * 1000),
                    success=success,
                )

            except Exception as e:
                logger.warning(f"Failed to pre-warm {url}", error=str(e))
                results[url] = False

        # Log summary
        successful = sum(1 for s in results.values() if s)
        logger.info(
            "Connection pre-warming complete",
            successful=successful,
            failed=len(results) - successful,
            total=len(results),
        )

        return results

    def close_all(self):
        """Close all sessions and clear pool."""
        for host, session in self._sessions.items():
            try:
                session.close()
                logger.debug(f"Closed session for {host}")
            except Exception as e:
                logger.warning(f"Error closing session for {host}", error=str(e))

        self._sessions.clear()
        logger.info("All sessions closed")

    def get_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics."""
        stats = {
            "active_sessions": len(self._sessions),
            "hosts": list(self._sessions.keys()),
        }

        # Get adapter stats for each session
        for host, session in self._sessions.items():
            for prefix, adapter in session.adapters.items():
                if isinstance(adapter, HTTPAdapter):
                    # Get pool manager stats
                    if hasattr(adapter, "poolmanager") and adapter.poolmanager:
                        pool_stats = {"num_pools": len(adapter.poolmanager.pools), "pools": {}}

                        for key, pool in adapter.poolmanager.pools.items():
                            pool_stats["pools"][str(key)] = {
                                "num_connections": pool.num_connections,
                                "num_requests": pool.num_requests,
                            }

                        stats[f"{host}_{prefix}"] = pool_stats

        return stats


# Global connection pool instance
_connection_pool: Optional[ConnectionPool] = None


def get_connection_pool() -> ConnectionPool:
    """Get or create global connection pool."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = ConnectionPool()
    return _connection_pool


class PooledGmailClient:
    """Gmail client wrapper with connection pooling."""

    def __init__(self, base_client):
        self.base_client = base_client
        self.pool = get_connection_pool()
        self.session = self.pool.get_session(
            "https://gmail.googleapis.com", max_retries=3, backoff_factor=0.5
        )

    def __getattr__(self, name):
        """Proxy all other attributes to base client."""
        return getattr(self.base_client, name)


class PooledNotionClient:
    """Notion client wrapper with connection pooling."""

    def __init__(self, base_client):
        self.base_client = base_client
        self.pool = get_connection_pool()
        self.session = self.pool.get_session(
            "https://api.notion.com",
            max_retries=3,
            backoff_factor=1.0,
            pool_maxsize=30,  # Notion can handle more concurrent connections
        )

        # Override base client's session if possible
        if hasattr(self.base_client, "_session"):
            self.base_client._session = self.session

    def __getattr__(self, name):
        """Proxy all other attributes to base client."""
        return getattr(self.base_client, name)


def prewarm_api_connections():
    """
    Pre-warm common API connections.

    Call this at the start of workflows to reduce SSL errors.
    """
    pool = get_connection_pool()

    urls = [
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        "https://api.notion.com/v1/users/me",
        "https://api.openai.com/v1/models",
    ]

    results = pool.prewarm_connections(urls)

    # Log results
    if all(results.values()):
        logger.info("All API connections pre-warmed successfully")
    else:
        failed = [url for url, success in results.items() if not success]
        logger.warning("Some connections failed to pre-warm", failed_urls=failed)

    return results
