"""Configure pytest fixtures and environment for SeeRM tests."""

import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv


def _ensure_app_on_path() -> None:
    """Ensure the canonical apps/seerm package is importable as ``app``."""
    root = Path(__file__).resolve().parents[1]
    app_root = root / "apps" / "seerm"
    if app_root.exists():
        sys.path.insert(0, str(app_root))


def pytest_sessionstart(session):
    """Load environment variables and configure import path."""
    _ensure_app_on_path()
    load_dotenv()
    print("✅ Environment variables loaded from .env file")


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Prepare the test environment for each session."""
    yield
