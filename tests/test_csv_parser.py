"""Validate CSV parser functionality and output integrity."""

from io import StringIO

import pandas as pd
import pytest

from app.data.csv_parser import CSVProcessor, parse_csv_data


class TestCSVProcessor:
    """Validate CSV processing functionality."""

    def setup_method(self):
        """Prepare fixtures for each test."""
        self.processor = CSVProcessor(strict_validation=False)

        # Sample CSV data matching your format
        self.sample_csv = """CALLSIGN,DBA,DOMAIN_ROOT,BENEFICIAL_OWNERS
97labs,97 Labs Inc.,outdoorly.com,"[""Theo Satloff""]"
aalo,Aalo Holdings Inc.,,"[""Matthew Loszak""]"
accio,Accio DBA Jump,jumpapp.com,"[""Parker Ence"",""Timothy Chaves""]"
testco,Test Company,test.com,"[""John Doe""]"
"""

    def test_column_normalization(self):
        """Ensure column names are normalized correctly."""
        df = pd.read_csv(StringIO("CALLSIGN,DBA,Domain_Root\ntest,Test Co,test.com"))

        normalized = self.processor.normalize_column_names(df)

        assert "callsign" in normalized
        assert "dba" in normalized
        assert "domain_root" in normalized
        assert normalized["callsign"] == "CALLSIGN"
        assert normalized["domain_root"] == "Domain_Root"

    def test_beneficial_owners_parsing(self):
        """Validate JSON parsing of beneficial owners."""
        df = pd.read_csv(StringIO(self.sample_csv))
        companies = self.processor.parse_companies_csv(df)

        # Test single owner
        aalo = next(c for c in companies if c.callsign == "aalo")
        assert aalo.beneficial_owners == ["Matthew Loszak"]

        # Test multiple owners
        accio = next(c for c in companies if c.callsign == "accio")
        assert len(accio.beneficial_owners) == 2
        assert "Parker Ence" in accio.beneficial_owners
        assert "Timothy Chaves" in accio.beneficial_owners

    def test_company_data_integrity(self):
        """Ensure company data is preserved correctly."""
        df = pd.read_csv(StringIO(self.sample_csv))
        companies = self.processor.parse_companies_csv(df)

        assert len(companies) == 4

        # Test specific company data
        test_co = next(c for c in companies if c.callsign == "testco")
        assert test_co.dba == "Test Company"
        assert test_co.domain_root == "test.com"
        assert test_co.beneficial_owners == ["John Doe"]

    def test_digest_calculation(self):
        """Validate digest statistics calculation."""
        df = pd.read_csv(StringIO(self.sample_csv))
        companies = self.processor.parse_companies_csv(df)

        digest_data = self.processor.calculate_digest_data(companies)

        assert digest_data["stats"]["total_accounts"] == 4
        assert "top_pct_gainers" in digest_data
        assert "top_pct_losers" in digest_data
        assert "product_starts" in digest_data
        assert "product_stops" in digest_data

    def test_validation_with_missing_columns(self):
        """Ensure missing columns are handled gracefully."""
        minimal_csv = "CALLSIGN\n97labs\naalo\n"
        df = pd.read_csv(StringIO(minimal_csv))

        companies = self.processor.parse_companies_csv(df)

        assert len(companies) == 2
        assert all(c.dba is None for c in companies)
        assert all(len(c.beneficial_owners) == 0 for c in companies)

    def test_error_handling_with_invalid_data(self):
        """Ensure malformed data is handled without raising errors."""
        invalid_csv = "CALLSIGN,BENEFICIAL_OWNERS\n97labs,invalid_json\n"
        df = pd.read_csv(StringIO(invalid_csv))

        # Should not raise exception in non-strict mode
        companies = self.processor.parse_companies_csv(df)
        assert len(companies) == 1
        assert companies[0].beneficial_owners == ["invalid_json"]  # Fallback parsing


@pytest.fixture
def real_csv_file():
    """Return sample path for real CSV file testing."""
    return "files/Will Accounts Demographics_2025-09-01T09_09_22.742205229Z.csv"


def test_real_csv_processing(real_csv_file):
    """Process an actual CSV file and validate structure."""
    try:
        companies, digest_data = parse_csv_data(open(real_csv_file, "rb").read())

        # Validate basic structure
        assert len(companies) > 0
        assert digest_data["stats"]["total_accounts"] == len(companies)

        # Validate company data integrity
        for company in companies[:10]:  # Check first 10
            assert company.callsign is not None
            assert len(company.callsign) > 0
            # Beneficial owners should be parsed as list
            assert isinstance(company.beneficial_owners, list)

        print(f"✅ Successfully processed {len(companies)} companies from real CSV")

    except FileNotFoundError:
        pytest.skip("Real CSV file not found - skipping integration test")
    except Exception as e:
        pytest.fail(f"Real CSV processing failed: {e}")


class TestCSVComparison:
    """Compare refactored CSV processing with original results."""

    def test_output_compatibility(self, real_csv_file):
        """Ensure refactored output matches original format."""
        try:
            # Process with new system
            companies, digest_data = parse_csv_data(open(real_csv_file, "rb").read())

            # Validate digest format matches expected structure
            required_keys = [
                "stats",
                "top_pct_gainers",
                "top_pct_losers",
                "product_starts",
                "product_stops",
            ]
            for key in required_keys:
                assert key in digest_data, f"Missing key: {key}"

            # Validate stats structure
            stats = digest_data["stats"]
            required_stats = [
                "total_accounts",
                "changed_accounts",
                "new_accounts",
                "removed_accounts",
            ]
            for stat in required_stats:
                assert stat in stats, f"Missing stat: {stat}"
                assert isinstance(stats[stat], int), f"Stat {stat} should be integer"

            print("✅ Output format validation passed")

        except FileNotFoundError:
            pytest.skip("Real CSV file not found")
