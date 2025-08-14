# scripts/probe_domain.py
import os
from typing import Dict, Any, Optional
import tldextract

from app.news_job import google_cse_search
from app.dossier_baseline import (
    compute_domain_root, validate_domain_to_url,
    discover_domain_smart
)

def probe(name: str, owners_csv: str = ""):
    owners = [s.strip() for s in owners_csv.split(",") if s.strip()]
    g_key = os.getenv("GOOGLE_API_KEY")
    g_cse = os.getenv("GOOGLE_CSE_ID")

    # smart discovery
    root = discover_domain_smart(name, owners, g_key, g_cse)
    url  = validate_domain_to_url(root) if root else None

    print(f"Name: {name}")
    print(f"Owners: {owners}")
    print(f"Chosen domain: {root}")
    print(f"Validated URL: {url}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.probe_domain \"Company Name\" [\"Owner One,Owner Two\"]")
        raise SystemExit(2)
    name = sys.argv[1]
    owners = sys.argv[2] if len(sys.argv) > 2 else ""
    probe(name, owners)
