"""Tests for `_parse_amount` helpers."""

import math

import pytest

from app.intelligence.llm_enrichment import _parse_amount


@pytest.mark.parametrize(
    "value,expected",
    [
        (150000000, 150000000.0),
        ("$50M", 50_000_000.0),
        ("US$12.5 million", 12_500_000.0),
        ("€1.2B", 1_200_000_000.0),
        ("approx $500k", 500_000.0),
        ("USD 750,000,000", 750_000_000.0),
        ("$3.5bn", 3_500_000_000.0),
        ("250mn", 250_000_000.0),
    ],
)
def test_parse_amount_succeeds(value, expected):
    """Return numeric amounts when the value includes supported unit markers."""
    result = _parse_amount(value)
    assert result is not None
    assert math.isclose(result, expected, rel_tol=1e-9)


@pytest.mark.parametrize(
    "value",
    [None, "", "unknown", "Undisclosed", "N/A", "not available", "none"],
)
def test_parse_amount_returns_none_for_unknown(value):
    """Yield `None` for missing or undisclosed amount text."""
    assert _parse_amount(value) is None


def test_parse_amount_handles_plain_numeric_string():
    """Handle numbers without unit suffixes."""
    assert _parse_amount("40000000") == 40_000_000.0
