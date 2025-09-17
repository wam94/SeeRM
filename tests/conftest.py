"""Configure pytest fixtures and environment for SeeRM tests."""

import pytest
from dotenv import load_dotenv


def pytest_sessionstart(session):
    """Load environment variables when the pytest session starts."""
    # Load environment variables from .env file
    load_dotenv()
    print("✅ Environment variables loaded from .env file")


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Prepare the test environment for each session."""
    yield
