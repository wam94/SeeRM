"""Utilities for packaging SeeRM into Mac-friendly onboarding flows."""

from __future__ import annotations

__all__ = [
    "DEFAULT_ENV_REFERENCE",
]

# Default location of the 1Password field that stores the shared .env payload.
DEFAULT_ENV_REFERENCE = "op://SeeRM Deployment/.env"
