"""Shared test fixtures and constants for sample data paths."""

from pathlib import Path

TEST_FILES_DIR = Path(__file__).resolve().parents[1] / "files"
SANITIZED_ACCOUNTS_CSV = str(
    TEST_FILES_DIR / "SeeRM Accounts Demographics_2025-09-22T09_04_03.587130999Z.csv"
)
