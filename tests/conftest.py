"""
Pytest configuration for SeeRM tests.

Automatically loads environment variables from .env file for all tests.
"""

import pytest
from dotenv import load_dotenv


def pytest_sessionstart(session):
    """Called after the Session object has been created."""
    # Load environment variables from .env file
    load_dotenv()
    print("✅ Environment variables loaded from .env file")


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment for all tests."""
    # This runs automatically for all tests
    yield
