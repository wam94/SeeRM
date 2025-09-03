#!/usr/bin/env python3
"""
Test baseline processing directly on the local CSV file to verify domain resolution.
"""

import os
import sys
import pandas as pd
from typing import Dict, Any

# Add the app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

# Import the functions we need
from dossier_baseline import (
    lower_cols, _normalize_csv_text, _is_blank, 
    resolve_domain_for_org, compute_domain_root
)

def test_baseline_on_local_csv():
    print("=== TESTING BASELINE ON LOCAL CSV ===\n")
    
    # Load the CSV directly
    csv_path = "files/Will Accounts Demographics_2025-09-01T09_09_22.742205229Z.csv"
    
    try:
        df_profile = pd.read_csv(csv_path)
        print(f"✅ CSV loaded: {len(df_profile)} rows, {len(df_profile.columns)} columns")
        print(f"Columns: {list(df_profile.columns)}")
        
        # Replicate the exact baseline logic
        prof: Dict[str, Dict[str, Any]] = {}
        pcols = lower_cols(df_profile)
        print(f"Column mapping: {pcols}\n")
        
        for _, r in df_profile.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower() if pcols.get("callsign") in r else ""
            if not cs:
                continue
            
            # Only process 97labs for this test
            if cs != "97labs":
                continue
                
            print(f"Processing {cs}...")
            
            # Replicate exact CSV parsing logic
            owners_raw = r[pcols.get("beneficial_owners")] if pcols.get("beneficial_owners") in r else ""
            owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]
            
            # Debug: Show what we're reading from CSV - handle pandas NaN values properly
            csv_domain_root_val = r[pcols.get("domain_root")] if pcols.get("domain_root") in r else None
            csv_website_val = r[pcols.get("website")] if pcols.get("website") in r else None
            
            print(f"Raw CSV values:")
            print(f"  domain_root: '{csv_domain_root_val}' (type: {type(csv_domain_root_val)})")
            print(f"  website: '{csv_website_val}' (type: {type(csv_website_val)})")
            
            # Convert pandas NaN to None
            if pd.isna(csv_domain_root_val):
                csv_domain_root_val = None
                print(f"  domain_root after NaN check: None")
            if pd.isna(csv_website_val):
                csv_website_val = None
                print(f"  website after NaN check: None")
                
            # Convert to strings only if not None, and strip whitespace
            if csv_domain_root_val is not None:
                csv_domain_root_val = str(csv_domain_root_val).strip()
                if not csv_domain_root_val:  # Empty string becomes None
                    csv_domain_root_val = None
                    
            if csv_website_val is not None:
                csv_website_val = str(csv_website_val).strip()
                if not csv_website_val:  # Empty string becomes None
                    csv_website_val = None
            
            print(f"After processing:")
            print(f"  domain_root: '{csv_domain_root_val}'")
            print(f"  website: '{csv_website_val}'")
            
            # Build the org dict exactly like baseline does
            base = {
                "callsign": r[pcols.get("callsign")],
                "dba": r[pcols.get("dba")] if pcols.get("dba") in r else None,
                "website": csv_website_val,
                "domain_root": csv_domain_root_val,
                "aka_names": r[pcols.get("aka_names")] if pcols.get("aka_names") in r else None,
                "blog_url": r[pcols.get("blog_url")] if pcols.get("blog_url") in r else None,
                "rss_feeds": r[pcols.get("rss_feeds")] if pcols.get("rss_feeds") in r else None,
                "linkedin_url": r[pcols.get("linkedin_url")] if pcols.get("linkedin_url") in r else None,
                "twitter_handle": r[pcols.get("twitter_handle")] if pcols.get("twitter_handle") in r else None,
                "crunchbase_url": r[pcols.get("crunchbase_url")] if pcols.get("crunchbase_url") in r else None,
                "industry_tags": r[pcols.get("industry_tags")] if pcols.get("industry_tags") in r else None,
                "hq_city": r[pcols.get("hq_city")] if pcols.get("hq_city") in r else None,
                "hq_region": r[pcols.get("hq_region")] if pcols.get("hq_region") in r else None,
                "hq_country": r[pcols.get("hq_country")] if pcols.get("hq_country") in r else None,
                "owners": owners,
            }
            
            # Preserve CSV domain_root - only compute from website if domain_root is missing
            if not base.get("domain_root"):
                base["domain_root"] = compute_domain_root(base.get("website"))
                print(f"  Computed domain_root from website: '{base['domain_root']}'")
            
            prof[cs] = base
            
            print(f"\nOrg dict created:")
            for key, val in base.items():
                print(f"  {key}: '{val}'")
            
            # Test domain resolution
            print(f"\n--- Testing Domain Resolution ---")
            dr, url = resolve_domain_for_org(base, None, None)  # No API keys for this test
            print(f"resolve_domain_for_org returned:")
            print(f"  domain_root: '{dr}'")
            print(f"  url: '{url}'")
            
            # Test the processing logic from process_single_company
            print(f"\n--- Testing Process Single Company Logic ---")
            csv_domain_root = _normalize_csv_text(base.get("domain_root"))
            csv_website = _normalize_csv_text(base.get("website"))
            
            print(f"After _normalize_csv_text:")
            print(f"  csv_domain_root: '{csv_domain_root}'")
            print(f"  csv_website: '{csv_website}'")
            
            # Simulate the preservation logic
            if dr:
                if not csv_domain_root:
                    print(f"Would set: domain_root = '{dr}', domain = '{dr}'")
                else:
                    print(f"Would preserve CSV: domain_root = '{csv_domain_root}', domain = '{csv_domain_root}'")
            
            if url:
                if (not csv_website) or (csv_domain_root and url.startswith(("https://"+csv_domain_root, "https://www."+csv_domain_root, "http://"+csv_domain_root))):
                    print(f"Would set: website = '{url}'")
                else:
                    print(f"Would preserve CSV website")
            
            break  # Only process 97labs
            
        if "97labs" not in prof:
            print("❌ 97labs not found in CSV!")
            # Show available callsigns
            available = []
            pcols = lower_cols(df_profile)
            for _, r in df_profile.iterrows():
                cs = str(r[pcols.get("callsign")]).strip().lower() if pcols.get("callsign") in r else ""
                if cs:
                    available.append(cs)
            print(f"Available callsigns: {available[:10]}{'...' if len(available) > 10 else ''}")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_baseline_on_local_csv()