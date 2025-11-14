"""Integration tests for Notion Intel Archive helper utilities."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.notion_client import update_intel_archive_for_company, upsert_company_page


@pytest.fixture
def notion_mocks():
    """Provide patched Notion helpers for intel archive tests."""
    with (
        patch("app.notion_client.notion_post") as post_mock,
        patch("app.notion_client.notion_patch") as patch_mock,
        patch("app.notion_client.notion_query_db") as query_mock,
        patch("app.notion_client.get_db_schema") as schema_mock,
        patch("app.notion_client.notion_get") as get_mock,
    ):
        schema_mock.return_value = {
            "properties": {
                "Company": {"type": "title"},
                "Callsign": {"type": "relation"},
                "Latest Intel": {"type": "rich_text"},
                "Last Intel At": {"type": "date"},
            }
        }
        query_mock.return_value = {"results": []}
        response = Mock()
        response.json.return_value = {"results": [], "has_more": False}
        get_mock.return_value = response
        yield post_mock, patch_mock


def test_upsert_company_page_creates_page(notion_mocks):
    """Ensure company page creation returns the Notion page ID."""
    post_mock, _ = notion_mocks
    post_mock.return_value.json.return_value = {"id": "page-123"}

    result = upsert_company_page(
        "companies-db",
        {"callsign": "TEST", "company": "Test Company", "needs_dossier": False},
    )

    assert result == "page-123"
    post_mock.assert_called_once()


def test_update_intel_archive_creates_toggle(notion_mocks):
    """Ensure intel archive update writes toggle blocks."""
    post_mock, patch_mock = notion_mocks
    post_mock.return_value.json.return_value = {"id": "intel-123"}

    update_intel_archive_for_company(
        intel_db_id="intel-db",
        companies_db_id="companies-db",
        company_page_id="company-123",
        callsign="TEST",
        date_iso="2025-09-08",
        summary_text="Weekly summary",
        items=[{"title": "Item", "source": "Source", "url": "https://example.com"}],
    )

    assert post_mock.called
    assert patch_mock.called
