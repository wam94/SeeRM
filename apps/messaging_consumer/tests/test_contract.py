from pathlib import Path

from messaging_consumer.contracts import load_weekly_digest


def test_sample_fixture_validates():
    payload = load_weekly_digest(
        Path(__file__).resolve().parents[1] / "fixtures" / "sample_report.json"
    )
    assert payload.summary_stats.total_items == 42
