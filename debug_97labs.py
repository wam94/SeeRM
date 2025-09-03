#!/usr/bin/env python3
"""
Debug script to test 97labs CSV parsing and domain resolution locally.
Run this to see exactly what's happening with the CSV data.
"""

import os
import sys
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))

from app.gmail_client import build_service
from app.news_job import fetch_csv_by_subject
from app.dossier_baseline import resolve_domain_for_org, _normalize_csv_text, _is_blank

# Set debug mode
os.environ["BASELINE_DEBUG"] = "true"

def debug_csv_parsing():
    """Debug CSV parsing for 97labs specifically."""
    print("=== DEBUGGING 97LABS CSV PARSING ===\n")
    
    # Gmail setup
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = os.environ["GMAIL_USER"]
    
    # Fetch CSV
    print("1. Fetching CSV from Gmail...")
    profile_subject = os.environ.get("PROFILE_SUBJECT") or "Org Profile — Will Mitchell"
    df = fetch_csv_by_subject(svc, user, profile_subject)
    
    if df is None:
        print("❌ No CSV found in Gmail!")
        return
        
    print(f"✅ CSV found with {len(df)} rows, {len(df.columns)} columns")
    print(f"Columns: {list(df.columns)}")
    print()
    
    # Look for 97labs specifically
    print("2. Searching for 97labs in CSV...")
    pcols = {c.lower(): c for c in df.columns}
    print(f"Column mapping: {pcols}")
    
    if "callsign" not in pcols:
        print("❌ No 'callsign' column found!")
        return
        
    # Find 97labs row
    found_97labs = False
    for idx, r in df.iterrows():
        cs = str(r[pcols.get("callsign")]).strip().lower()
        if cs == "97labs":
            found_97labs = True
            print(f"✅ Found 97labs at row {idx}")
            
            # Debug all the values we extract
            print("\n3. Raw CSV values for 97labs:")
            print(f"  callsign: '{r[pcols.get('callsign')]}'")
            if "dba" in pcols:
                print(f"  dba: '{r[pcols.get('dba')]}'")
            if "domain_root" in pcols:
                print(f"  domain_root: '{r[pcols.get('domain_root')]}' (type: {type(r[pcols.get('domain_root')])})")
            if "website" in pcols:
                print(f"  website: '{r[pcols.get('website')]}' (type: {type(r[pcols.get('website')])})")
            if "beneficial_owners" in pcols:
                print(f"  beneficial_owners: '{r[pcols.get('beneficial_owners')]}'")
                
            # Test our normalization functions
            print("\n4. After normalization:")
            domain_root_raw = r[pcols.get("domain_root")] if pcols.get("domain_root") in r else None
            website_raw = r[pcols.get("website")] if pcols.get("website") in r else None
            
            print(f"  _is_blank(domain_root): {_is_blank(domain_root_raw)}")
            print(f"  _is_blank(website): {_is_blank(website_raw)}")
            print(f"  _normalize_csv_text(domain_root): '{_normalize_csv_text(domain_root_raw)}'")
            print(f"  _normalize_csv_text(website): '{_normalize_csv_text(website_raw)}'")
            
            # Build the org dict like the real code does
            print("\n5. Building org dict...")
            owners_raw = r[pcols.get("beneficial_owners")] if pcols.get("beneficial_owners") in r else ""
            owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]
            
            org = {
                "callsign": r[pcols.get("callsign")],
                "dba": r[pcols.get("dba")] if pcols.get("dba") in r else None,
                "website": _normalize_csv_text(website_raw),
                "domain_root": _normalize_csv_text(domain_root_raw),
                "owners": owners,
            }
            
            print(f"  org dict: {org}")
            
            # Test domain resolution
            print("\n6. Testing domain resolution...")
            g_api_key = os.environ.get("GOOGLE_API_KEY")
            g_cse_id = os.environ.get("GOOGLE_CSE_ID")
            
            dr, url = resolve_domain_for_org(org, g_api_key, g_cse_id)
            print(f"  resolve_domain_for_org returned: dr='{dr}', url='{url}'")
            
            break
    
    if not found_97labs:
        print("❌ 97labs not found in CSV!")
        print("Available callsigns:")
        for idx, r in df.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower()
            if cs:
                print(f"  - '{cs}'")

if __name__ == "__main__":
    try:
        debug_csv_parsing()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()