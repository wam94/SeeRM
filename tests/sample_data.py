"""Shared sample data paths for pytest."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SANITIZED_ACCOUNTS_CSV = str(
    REPO_ROOT / "files" / "SeeRM Accounts Demographics_2025-09-22T09_04_03.587130999Z.csv"
)
