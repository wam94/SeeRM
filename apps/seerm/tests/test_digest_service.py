"""Validate digest service functionality end-to-end."""

from unittest.mock import Mock, patch

import pytest

from app.core.config import DigestConfig
from app.core.models import Company, DigestData, DigestStats
from app.services.digest_service import DigestService
from app.services.render_service import DigestRenderer
from tests.sample_data import SANITIZED_ACCOUNTS_CSV


class TestDigestService:
    """Validate digest service functionality."""

    def setup_method(self):
        """Prepare common mocks for each test."""
        self.mock_gmail_client = Mock()
        self.renderer = DigestRenderer()
        self.config = DigestConfig(top_movers=15)
        self.service = DigestService(self.mock_gmail_client, self.renderer, self.config)
        self.mock_notion_client = Mock()
        self.service_with_notion = DigestService(
            self.mock_gmail_client,
            self.renderer,
            self.config,
            notion_client=self.mock_notion_client,
            companies_db_id="test_db",
        )

    def test_digest_data_generation(self):
        """Generate digest data from company fixtures."""
        # Create test companies
        companies = [
            Company(
                callsign="test1",
                dba="Test Company 1",
                curr_balance=100000,
                prev_balance=90000,
                balance_pct_delta_pct=11.11,
                any_change=True,
            ),
            Company(
                callsign="test2",
                dba="Test Company 2",
                curr_balance=80000,
                prev_balance=100000,
                balance_pct_delta_pct=-20.0,
                any_change=True,
            ),
            Company(
                callsign="test3",
                dba="Test Company 3",
                is_new_account=True,
                any_change=True,
            ),
        ]

        digest_data = self.service.generate_digest_data(companies, top_n=5)

        # Validate basic structure
        assert isinstance(digest_data, DigestData)
        assert digest_data.stats.total_accounts == 3
        assert digest_data.stats.changed_accounts == 3
        assert digest_data.stats.new_accounts == 1

        # Validate top movers
        assert len(digest_data.top_pct_gainers) == 1
        assert len(digest_data.top_pct_losers) == 1

        assert digest_data.top_pct_gainers[0].callsign == "test1"
        assert digest_data.top_pct_gainers[0].percentage_change == 11.11

        assert digest_data.top_pct_losers[0].callsign == "test2"
        assert digest_data.top_pct_losers[0].percentage_change == -20.0

    def test_new_callsigns_extraction(self):
        """Test extraction of new account callsigns."""
        companies = [
            Company(callsign="existing1", any_change=True),
            Company(callsign="new1", is_new_account=True, any_change=True),
            Company(callsign="new2", is_new_account=True, any_change=True),
            Company(callsign="existing2", any_change=False),
        ]

        self.mock_notion_client.get_all_companies_domain_data.return_value = {
            "existing1": {"page_id": "page_existing1"},
            "new1": {"page_id": None},
            "new2": {"page_id": None},
            "existing2": {"page_id": "page_existing2"},
        }

        new_callsigns = self.service_with_notion.extract_new_account_callsigns(companies)

        self.mock_notion_client.get_all_companies_domain_data.assert_called_once_with(
            "test_db", [c.callsign for c in companies]
        )

        assert len(new_callsigns) == 2
        assert "new1" in new_callsigns
        assert "new2" in new_callsigns
        assert "existing1" not in new_callsigns
        assert "existing2" not in new_callsigns

    @patch("builtins.open", create=True)
    def test_trigger_file_writing(self, mock_open):
        """Test writing of new callsigns trigger file."""
        mock_file = Mock()
        mock_open.return_value.__enter__.return_value = mock_file

        callsigns = ["new1", "new2", "new3"]
        self.service.write_new_callsigns_trigger(callsigns)

        mock_open.assert_called_once_with("/tmp/new_callsigns.txt", "w")
        mock_file.write.assert_called_once_with("new1,new2,new3")


class TestDigestRenderer:
    """Test digest HTML rendering."""

    def setup_method(self):
        """Prepare common mocks for each test."""
        self.renderer = DigestRenderer()

    def test_digest_rendering(self):
        """Test HTML digest rendering."""
        # Create test digest data
        digest_data = DigestData(
            subject="Test Weekly Digest",
            stats=DigestStats(
                total_accounts=100,
                changed_accounts=25,
                new_accounts=5,
                removed_accounts=2,
            ),
            top_pct_gainers=[],
            top_pct_losers=[],
        )

        html = self.renderer.render_digest(digest_data)

        # Validate HTML structure
        assert "<!doctype html>" in html
        assert "Test Weekly Digest" in html
        assert "Accounts: 100" in html
        assert "Changed: 25" in html
        assert "New: 5" in html
        assert "Removed: 2" in html

    def test_movers_rendering(self):
        """Test rendering of top movers."""
        from app.core.models import AccountMovement

        digest_data = DigestData(
            stats=DigestStats(total_accounts=10),
            top_pct_gainers=[
                AccountMovement(callsign="gainer1", percentage_change=15.5, balance_delta=50000)
            ],
            top_pct_losers=[
                AccountMovement(callsign="loser1", percentage_change=-10.2, balance_delta=-25000)
            ],
        )

        html = self.renderer.render_digest(digest_data)

        # Check gainer formatting
        assert "gainer1" in html
        assert "+15.50%" in html
        assert "50,000" in html

        # Check loser formatting
        assert "loser1" in html
        assert "-10.20%" in html
        assert "-25,000" in html


@pytest.fixture
def benchmark_data():
    """Fixture providing benchmark data for performance testing."""
    return {
        "csv_parse_time_ms": 50,  # Target: under 50ms for 200+ companies
        "digest_gen_time_ms": 100,  # Target: under 100ms
        "html_render_time_ms": 20,  # Target: under 20ms
        "total_workflow_time_s": 2.0,  # Target: under 2 seconds end-to-end
    }


class TestPerformanceBenchmarks:
    """Performance benchmarking tests."""

    def test_csv_parsing_performance(self, benchmark_data):
        """Test CSV parsing performance meets benchmarks."""
        import time

        from app.data.csv_parser import parse_csv_file

        try:
            start_time = time.time()
            companies, digest_data = parse_csv_file(SANITIZED_ACCOUNTS_CSV)
            parse_time = (time.time() - start_time) * 1000  # Convert to ms

            # Performance assertions
            assert (
                parse_time < benchmark_data["csv_parse_time_ms"] * 2
            ), f"CSV parsing too slow: {parse_time:.2f}ms"
            assert len(companies) > 0, "No companies parsed"

            print(f"✅ CSV parsing performance: {parse_time:.2f}ms for {len(companies)} companies")

        except FileNotFoundError:
            pytest.skip("CSV file not found for performance test")

    def test_digest_generation_performance(self, benchmark_data):
        """Test digest generation performance."""
        import time

        from app.core.models import Company

        # Generate test data
        companies = [
            Company(
                callsign=f"test{i}",
                dba=f"Test Company {i}",
                curr_balance=100000 + i * 1000,
                prev_balance=95000 + i * 1000,
                balance_pct_delta_pct=5.0 + (i % 10),
                any_change=True,
            )
            for i in range(200)  # Test with 200 companies
        ]

        service = DigestService(Mock(), DigestRenderer(), DigestConfig())

        start_time = time.time()
        digest_data = service.generate_digest_data(companies)
        gen_time = (time.time() - start_time) * 1000

        assert (
            gen_time < benchmark_data["digest_gen_time_ms"] * 2
        ), f"Digest generation too slow: {gen_time:.2f}ms"
        assert digest_data.stats.total_accounts == 200

        print(f"✅ Digest generation performance: {gen_time:.2f}ms for 200 companies")


class TestOriginalCompatibility:
    """Test compatibility with original system behavior."""

    def test_csv_output_format_compatibility(self):
        """Ensure CSV processing output format matches original."""
        from app.data.csv_parser import parse_csv_file

        try:
            companies, digest_data = parse_csv_file(SANITIZED_ACCOUNTS_CSV)

            # Test output structure matches original parse_csv_to_context format
            assert "stats" in digest_data
            assert "top_pct_gainers" in digest_data
            assert "top_pct_losers" in digest_data

            # Test each gainer/loser has required fields
            for gainer in digest_data["top_pct_gainers"]:
                assert "callsign" in gainer
                assert "percentage_change" in gainer
                # Note: called 'percentage_change' in new system vs 'pct' in original

            print("✅ Output format compatibility verified")

        except FileNotFoundError:
            pytest.skip("CSV file not available")

    def test_html_output_structure(self):
        """Test HTML output contains expected elements from original."""
        digest_data = DigestData(
            stats=DigestStats(total_accounts=221, changed_accounts=0, new_accounts=0)
        )

        renderer = DigestRenderer()
        html = renderer.render_digest(digest_data)

        # Check for key elements that exist in original
        expected_elements = [
            "<!doctype html>",
            "Client Weekly Digest",
            "At a glance",
            "Accounts: 221",
            "Generated automatically",
            "font-family: -apple-system",  # CSS style preservation
        ]

        for element in expected_elements:
            assert element in html, f"Missing expected element: {element}"

        print("✅ HTML structure compatibility verified")
