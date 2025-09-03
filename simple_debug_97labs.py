#!/usr/bin/env python3
"""
Simple debug script to test 97labs CSV parsing locally.
"""

import os
import sys
import pandas as pd

# Add the app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def _is_blank(x) -> bool:
    if x is None: 
        return True
    s = str(x).strip().lower()
    return s in ("", "none", "nan")

def _normalize_csv_text(x):
    if _is_blank(x): 
        return None
    return str(x).strip()

def debug_csv_parsing():
    """Debug CSV parsing for 97labs specifically."""
    print("=== DEBUGGING 97LABS CSV PARSING ===\n")
    
    try:
        from gmail_client import build_service
        from news_job import fetch_csv_by_subject
    except ImportError as e:
        print(f"Import error: {e}")
        print("Make sure you have all the required environment variables set.")
        return
    
    # Gmail setup
    try:
        svc = build_service(
            client_id=os.environ["GMAIL_CLIENT_ID"],
            client_secret=os.environ["GMAIL_CLIENT_SECRET"],
            refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        )
        user = os.environ["GMAIL_USER"]
    except KeyError as e:
        print(f"Missing environment variable: {e}")
        return
    
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
    print(f"Column mapping (first 10): {dict(list(pcols.items())[:10])}")
    
    if "callsign" not in pcols:
        print("❌ No 'callsign' column found!")
        print(f"Available columns: {list(pcols.keys())}")
        return
        
    # Find 97labs row
    found_97labs = False
    for idx, r in df.iterrows():
        cs = str(r[pcols.get("callsign", "")]).strip().lower()
        if cs == "97labs":
            found_97labs = True
            print(f"✅ Found 97labs at row {idx}")
            
            # Debug the critical fields
            print("\n3. Raw CSV values for 97labs:")
            print(f"  callsign: '{r.get(pcols.get('callsign', ''), 'N/A')}'")
            
            if "dba" in pcols:
                dba_val = r.get(pcols.get('dba'), None)
                print(f"  dba: '{dba_val}' (type: {type(dba_val)})")
                
            if "domain_root" in pcols:
                domain_val = r.get(pcols.get('domain_root'), None)
                print(f"  domain_root: '{domain_val}' (type: {type(domain_val)})")
                print(f"  domain_root _is_blank: {_is_blank(domain_val)}")
                print(f"  domain_root normalized: '{_normalize_csv_text(domain_val)}'")
            else:
                print("  ❌ domain_root column not found!")
                
            if "website" in pcols:
                website_val = r.get(pcols.get('website'), None)
                print(f"  website: '{website_val}' (type: {type(website_val)})")
                print(f"  website _is_blank: {_is_blank(website_val)}")
                print(f"  website normalized: '{_normalize_csv_text(website_val)}'")
            else:
                print("  ❌ website column not found!")
                
            break
    
    if not found_97labs:
        print("❌ 97labs not found in CSV!")
        print("\nFirst 10 callsigns found:")
        count = 0
        for idx, r in df.iterrows():
            if count >= 10:
                break
            cs = str(r.get(pcols.get("callsign", ""), "")).strip().lower()
            if cs:
                print(f"  - '{cs}'")
                count += 1

if __name__ == "__main__":
    debug_csv_parsing()