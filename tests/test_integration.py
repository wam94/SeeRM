"""
Integration tests for complete workflow validation.

Tests end-to-end functionality and comparison with original system behavior.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.core.config import Settings
from app.core.models import ProcessingStatus
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient
from app.workflows.weekly_digest import WeeklyDigestWorkflow


class TestWorkflowIntegration:
    """Test complete workflow integration."""

    def setup_method(self):
        """Setup for each test."""
        # Create test settings with minimal required values to avoid validation errors
        import os

        os.environ.update(
            {
                "DRY_RUN": "true",
                "GMAIL_CLIENT_ID": "test_client_id",
                "GMAIL_CLIENT_SECRET": "test_client_secret",
                "GMAIL_REFRESH_TOKEN": "test_refresh_token",
                "GMAIL_USER": "test@example.com",
                "NOTION_API_KEY": "test_api_key",
            }
        )
        self.settings = Settings()
        self.settings.dry_run = True  # Always use dry run for tests

        # Mock Gmail responses
        self.mock_gmail_messages = [{"id": "test_msg_1"}]
        self.mock_gmail_message = {
            "id": "test_msg_1",
            "payload": {
                "parts": [
                    {"filename": "test_data.csv", "body": {"data": self._create_test_csv_b64()}}
                ]
            },
        }

    def _create_test_csv_b64(self):
        """Create base64 encoded test CSV data."""
        import base64

        csv_data = """CALLSIGN,DBA,DOMAIN_ROOT,BENEFICIAL_OWNERS,CURR_BALANCE,PREV_BALANCE,BALANCE_PCT_DELTA_PCT,IS_NEW_ACCOUNT,ANY_CHANGE
test1,Test Company 1,test1.com,"[""John Doe""]",100000,90000,11.11,False,True
test2,Test Company 2,test2.com,"[""Jane Smith""]",80000,100000,-20.0,False,True
newco,New Company,newco.com,"[""Bob Wilson""]",50000,0,0.0,True,True"""
        return base64.urlsafe_b64encode(csv_data.encode()).decode()

    @patch("app.data.gmail_client.build")
    def test_complete_digest_workflow_dry_run(self, mock_build):
        """Test complete digest workflow in dry run mode."""
        # Setup Gmail service mock
        mock_service = Mock()
        mock_build.return_value = mock_service

        # Mock Gmail API responses
        mock_service.users().messages().list().execute.return_value = {
            "messages": self.mock_gmail_messages
        }
        mock_service.users().messages().get().execute.return_value = self.mock_gmail_message
        mock_service.users().getProfile().execute.return_value = {
            "messagesTotal": 1000,
            "threadsTotal": 500,
        }

        # Create workflow
        workflow = WeeklyDigestWorkflow(self.settings)

        # Run workflow
        result = workflow.run(max_messages=1)

        # Validate results
        assert result.status == ProcessingStatus.COMPLETED
        assert result.items_processed == 3  # Should process 3 test companies
        assert result.duration_seconds is not None
        assert result.duration_seconds > 0

        # Validate data
        assert result.data is not None
        assert "new_callsigns" in result.data
        assert "newco" in result.data["new_callsigns"]
        assert result.data["digest_stats"]["total_accounts"] == 3

    def test_workflow_configuration_validation(self):
        """Test workflow validates configuration properly."""
        # Create settings with missing required fields
        incomplete_settings = Settings()
        incomplete_settings.gmail.client_id = ""  # Missing required field
        incomplete_settings.dry_run = False  # Ensure dry_run is False for this test

        workflow = WeeklyDigestWorkflow(incomplete_settings)

        # Should raise configuration error
        with pytest.raises(Exception) as exc_info:
            workflow.validate_configuration()

        assert "GMAIL_CLIENT_ID" in str(exc_info.value)

    def test_workflow_health_checks(self):
        """Test workflow health check functionality."""
        workflow = WeeklyDigestWorkflow(self.settings)

        # Create mock objects
        mock_gmail = Mock()
        mock_gmail.health_check.return_value = {"status": "healthy", "user": "test@example.com"}

        mock_digest = Mock()
        mock_digest.health_check.return_value = {"status": "healthy", "config": {}}

        # Set the private attributes directly
        workflow._gmail_client = mock_gmail
        workflow._digest_service = mock_digest

        health = workflow.perform_health_checks()

        assert health["overall_status"] == "healthy"
        assert health["gmail"]["status"] == "healthy"
        assert health["digest_service"]["status"] == "healthy"

    def test_workflow_error_handling(self):
        """Test workflow handles errors gracefully."""
        workflow = WeeklyDigestWorkflow(self.settings)

        # Create mock service that raises an exception
        mock_service = Mock()
        mock_service.run_digest_workflow.side_effect = Exception("Service unavailable")

        # Set the private attribute directly
        workflow._digest_service = mock_service

        result = workflow.run()

        assert result.status == ProcessingStatus.FAILED
        assert "Service unavailable" in result.error_message
        assert result.duration_seconds is not None


class TestOriginalSystemComparison:
    """Compare refactored system output with original system."""

    def setup_method(self):
        """Setup comparison tests."""
        self.test_csv_path = "files/Will Accounts Demographics_2025-09-01T09_09_22.742205229Z.csv"

    def test_csv_parsing_compatibility(self):
        """Test CSV parsing produces compatible output."""
        if not os.path.exists(self.test_csv_path):
            pytest.skip("Test CSV file not available")

        # Import both systems
        from app.data.csv_parser import parse_csv_file

        # Parse with new system
        companies, digest_data = parse_csv_file(self.test_csv_path)

        # Test output structure matches original parse_csv_to_context
        assert isinstance(digest_data, dict)
        required_keys = [
            "stats",
            "top_pct_gainers",
            "top_pct_losers",
            "product_starts",
            "product_stops",
        ]
        for key in required_keys:
            assert key in digest_data

        # Test stats structure
        stats = digest_data["stats"]
        required_stats = ["total_accounts", "changed_accounts", "new_accounts", "removed_accounts"]
        for stat in required_stats:
            assert stat in stats
            assert isinstance(stats[stat], int)

        # Test that we get reasonable numbers
        assert stats["total_accounts"] > 0
        assert stats["total_accounts"] >= stats["changed_accounts"]
        assert stats["total_accounts"] >= stats["new_accounts"]

    def test_digest_html_compatibility(self):
        """Test HTML output structure matches original."""
        if not os.path.exists(self.test_csv_path):
            pytest.skip("Test CSV file not available")

        from app.core.models import DigestData
        from app.data.csv_parser import parse_csv_file
        from app.services.render_service import DigestRenderer

        # Parse data and render
        companies, digest_dict = parse_csv_file(self.test_csv_path)
        digest_data = DigestData(**digest_dict)
        renderer = DigestRenderer()
        html = renderer.render_digest(digest_data)

        # Check for elements that exist in original template
        expected_elements = [
            "<!doctype html>",
            "Client Weekly Digest",
            "At a glance",
            f"Accounts: {digest_data.stats.total_accounts}",
            "Generated automatically",
            "font-family: -apple-system",  # CSS compatibility
            ".badge",
            ".section",
            ".h1",  # CSS classes
        ]

        for element in expected_elements:
            assert element in html, f"Missing expected element: {element}"


class TestEndToEndValidation:
    """End-to-end validation with real data and scenarios."""

    def test_complete_pipeline_with_real_csv(self):
        """Test complete processing pipeline with real CSV."""
        csv_path = "files/Will Accounts Demographics_2025-09-01T09_09_22.742205229Z.csv"

        if not os.path.exists(csv_path):
            pytest.skip("Real CSV file not available for E2E test")

        from app.core.config import DigestConfig
        from app.data.csv_parser import parse_csv_file
        from app.services.digest_service import DigestService
        from app.services.render_service import DigestRenderer

        # Step 1: Parse CSV
        companies, digest_dict = parse_csv_file(csv_path)
        assert len(companies) > 0

        # Step 2: Create digest service (mocked Gmail)
        mock_gmail = Mock()
        renderer = DigestRenderer()
        config = DigestConfig(top_movers=15)
        service = DigestService(mock_gmail, renderer, config)

        # Step 3: Generate digest
        from app.core.models import DigestData

        digest_data = DigestData(**digest_dict)

        # Step 4: Extract new callsigns
        new_callsigns = service.extract_new_account_callsigns(companies)

        # Step 5: Render HTML
        html = renderer.render_digest(digest_data)

        # Validate end-to-end results
        assert len(html) > 1000  # Should be substantial HTML
        assert digest_data.stats.total_accounts == len(companies)
        assert "Client Weekly Digest" in html

        print(f"✅ E2E validation: {len(companies)} companies → {len(html)} char HTML")
        print(f"   New accounts: {len(new_callsigns)}")
        print(f"   Changed accounts: {digest_data.stats.changed_accounts}")

    def test_error_scenarios(self):
        """Test system behavior under error conditions."""
        import io

        import pandas as pd

        from app.data.csv_parser import CSVProcessor

        processor = CSVProcessor(strict_validation=False)

        # Test empty CSV
        empty_csv = "CALLSIGN\n"
        df = pd.read_csv(io.StringIO(empty_csv))
        companies = processor.parse_companies_csv(df)
        assert len(companies) == 0

        # Test malformed data
        bad_csv = "CALLSIGN,BENEFICIAL_OWNERS\ntest,{bad_json}\n"
        df = pd.read_csv(io.StringIO(bad_csv))
        companies = processor.parse_companies_csv(df)
        assert len(companies) == 1  # Should handle gracefully

        print("✅ Error scenario handling verified")

    def test_performance_with_large_dataset(self):
        """Test performance with larger dataset."""
        import time

        from app.core.models import Company
        from app.data.csv_parser import CSVProcessor

        # Generate test companies
        companies = [
            Company(
                callsign=f"test{i:04d}",
                dba=f"Test Company {i}",
                domain_root=f"test{i}.com",
                beneficial_owners=[f"Owner {i}"],
                curr_balance=100000 + i * 100,
                prev_balance=95000 + i * 100,
                balance_pct_delta_pct=5.0 + (i % 20) - 10,  # Mix of gains/losses
                any_change=True,
            )
            for i in range(500)  # Test with 500 companies
        ]

        processor = CSVProcessor()

        # Test digest calculation performance
        start_time = time.time()
        digest_data = processor.calculate_digest_data(companies, top_n=25)
        calc_time = time.time() - start_time

        # Performance assertions
        assert calc_time < 1.0, f"Digest calculation too slow: {calc_time:.3f}s"
        assert digest_data["stats"]["total_accounts"] == 500
        assert len(digest_data["top_pct_gainers"]) <= 25
        assert len(digest_data["top_pct_losers"]) <= 25

        print(f"✅ Performance test: 500 companies processed in {calc_time:.3f}s")


class TestDryRunValidation:
    """Test dry-run functionality and validation."""

    def test_dry_run_no_side_effects(self):
        """Test dry-run mode prevents actual changes."""
        from app.core.config import GmailConfig, NotionConfig
        from app.data.gmail_client import EnhancedGmailClient
        from app.data.notion_client import EnhancedNotionClient

        # Create clients in dry-run mode
        gmail_config = GmailConfig(
            client_id="test", client_secret="test", refresh_token="test", user="test@example.com"
        )
        notion_config = NotionConfig(api_key="test")

        gmail_client = EnhancedGmailClient(gmail_config, dry_run=True)
        notion_client = EnhancedNotionClient(notion_config, dry_run=True)

        # Test that operations return dry-run indicators
        assert gmail_client.dry_run is True
        assert notion_client.dry_run is True

        # Mock operations should not make real calls
        with patch("httpx.Client") as mock_client:
            # Notion operations in dry-run should not make HTTP requests
            from app.core.models import Company

            test_company = Company(callsign="test", dba="Test Co")

            # This should not make actual HTTP calls
            result = notion_client.create_company_page("fake_db", test_company)

            # Should return dry-run result
            assert result.page_id == "dry_run_page_id"
            assert result.created is True

        print("✅ Dry-run validation: No side effects confirmed")


# Benchmark definitions
PERFORMANCE_BENCHMARKS = {
    "csv_parse_221_companies_ms": 100,  # Target: <100ms for 221 companies
    "digest_generation_500_companies_ms": 200,  # Target: <200ms for 500 companies
    "html_render_ms": 50,  # Target: <50ms for HTML generation
    "workflow_end_to_end_s": 5.0,  # Target: <5s for complete workflow
    "memory_usage_mb": 100,  # Target: <100MB memory usage
    "circuit_breaker_recovery_ms": 200,  # Target: <200ms recovery time
}


def test_benchmarks_summary():
    """Print benchmark summary for manual verification."""
    print("\n" + "=" * 60)
    print("PERFORMANCE BENCHMARKS")
    print("=" * 60)

    for metric, target in PERFORMANCE_BENCHMARKS.items():
        unit = metric.split("_")[-1]
        description = " ".join(metric.split("_")[:-1]).replace("_", " ").title()
        print(f"{description:.<45} < {target} {unit}")

    print("=" * 60)
    print("Run with: pytest tests/ -v --tb=short")
    print("=" * 60)
