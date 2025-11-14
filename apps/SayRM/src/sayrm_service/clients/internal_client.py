"""Placeholder internal usage client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import SayRMSettings


@dataclass
class InternalUsageSnapshot:
    """Structured internal usage data."""

    status: str
    owners: list[str]
    products: list[str]
    notes: Optional[str] = None
    raw: dict | None = None


class InternalUsageClient:
    """
    Fetches internal usage details from an internal API when configured.

    Until the endpoint is wired up we return an informative placeholder so the
    rest of the workflow continues to operate.
    """

    def __init__(self, settings: SayRMSettings) -> None:
        self._base_url = settings.internal_usage_base_url
        self._api_key = settings.internal_usage_api_key
        self._http = httpx.Client(timeout=15.0) if self._base_url else None

    def fetch(self, callsign: str) -> InternalUsageSnapshot:
        """Return structured internal usage data or a placeholder snapshot."""
        if not self._http or not self._base_url:
            return InternalUsageSnapshot(
                status="unconfigured",
                owners=[],
                products=[],
                notes="Internal usage API not configured yet.",
                raw=None,
            )

        try:
            resp = self._http.get(
                f"{self._base_url.rstrip('/')}/companies/{callsign}/usage",
                headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else None,
            )
            resp.raise_for_status()
            data = resp.json()
            return InternalUsageSnapshot(
                status=data.get("status", "ok"),
                owners=data.get("stakeholders", []),
                products=data.get("products", []),
                notes=data.get("notes"),
                raw=data,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return InternalUsageSnapshot(
                status="error",
                owners=[],
                products=[],
                notes=f"Failed to pull internal usage: {exc}",
                raw=None,
            )

