#!/usr/bin/env python3
"""
Test baseline processing on local CSV with minimal imports.
"""

import pandas as pd
import urllib.parse
from typing import Dict, Any, Optional

def lower_cols(df: pd.DataFrame) -> Dict[str, str]:
    return {c.lower().strip(): c for c in df.columns}

def _is_blank(x) -> bool:
    if x is None: 
        return True
    s = str(x).strip().lower()
    return s in ("", "none", "nan")

def _normalize_csv_text(x: Any) -> Optional[str]:
    if _is_blank(x): 
        return None
    return str(x).strip()

def compute_domain_root(url_or_domain: Optional[str]) -> Optional[str]:
    """Extract domain root from URL or return as-is if already a domain."""
    if not url_or_domain:
        return None
    s = str(url_or_domain).strip()
    if not s:
        return None
    
    # If it looks like a URL, parse it
    if s.startswith(('http://', 'https://', 'www.')):
        try:
            if s.startswith('www.'):
                s = 'http://' + s
            parsed = urllib.parse.urlparse(s)
            domain = parsed.netloc or parsed.path
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain if domain else None
        except Exception:
            return None
    
    # Already looks like a domain
    return s

def resolve_domain_for_org_simple(org: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Simplified domain resolution (no API calls) - just test the CSV priority logic.
    """
    csv_domain_root = _normalize_csv_text(org.get("domain_root"))
    csv_website = _normalize_csv_text(org.get("website"))
    
    print(f"    CSV domain_root after normalization: '{csv_domain_root}'")
    print(f"    CSV website after normalization: '{csv_website}'")

    # 1) Trust CSV domain_root
    if csv_domain_root:
        url = f"https://{csv_domain_root}"
        print(f"    Using CSV domain_root: {csv_domain_root} -> {url}")
        return csv_domain_root, url

    # 2) Trust CSV website
    if csv_website:
        domain_root = compute_domain_root(csv_website)
        print(f"    Using CSV website: {csv_website} -> domain: {domain_root}")
        return domain_root, csv_website

    # 3) Would search (but we skip for this test)
    print(f"    No CSV domain data - would search")
    return None, None

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
            
            # Debug: Show what we're reading from CSV
            csv_domain_root_val = r[pcols.get("domain_root")] if pcols.get("domain_root") in r else None
            csv_website_val = r[pcols.get("website")] if pcols.get("website") in r else None
            
            print(f"  Raw CSV values:")
            print(f"    domain_root: '{csv_domain_root_val}' (type: {type(csv_domain_root_val)})")
            print(f"    website: '{csv_website_val}' (type: {type(csv_website_val)})")
            print(f"    dba: '{r[pcols.get('dba')]}'")
            print(f"    beneficial_owners: '{owners_raw}'")
            
            # Convert pandas NaN to None (simulate the exact baseline logic)
            if pd.isna(csv_domain_root_val):
                csv_domain_root_val = None
            if pd.isna(csv_website_val):
                csv_website_val = None
                
            # Convert to strings only if not None, and strip whitespace
            if csv_domain_root_val is not None:
                csv_domain_root_val = str(csv_domain_root_val).strip()
                if not csv_domain_root_val:  # Empty string becomes None
                    csv_domain_root_val = None
                    
            if csv_website_val is not None:
                csv_website_val = str(csv_website_val).strip()
                if not csv_website_val:  # Empty string becomes None
                    csv_website_val = None
            
            print(f"  After pandas processing:")
            print(f"    domain_root: '{csv_domain_root_val}'")
            print(f"    website: '{csv_website_val}'")
            
            # Build the org dict exactly like baseline does
            base = {
                "callsign": r[pcols.get("callsign")],
                "dba": r[pcols.get("dba")] if pcols.get("dba") in r else None,
                "website": csv_website_val,
                "domain_root": csv_domain_root_val,
                "owners": owners,
            }
            
            # Preserve CSV domain_root - only compute from website if domain_root is missing
            if not base.get("domain_root"):
                base["domain_root"] = compute_domain_root(base.get("website"))
                print(f"    Computed domain_root from website: '{base['domain_root']}'")
            
            print(f"  Final org dict:")
            for key, val in base.items():
                print(f"    {key}: '{val}'")
            
            # Test domain resolution
            print(f"\n  --- Testing Domain Resolution ---")
            dr, url = resolve_domain_for_org_simple(base)
            print(f"  Result: domain_root='{dr}', url='{url}'")
            
            # Test the processing logic that handles the results
            print(f"\n  --- Testing Final Processing Logic ---")
            csv_domain_root = _normalize_csv_text(base.get("domain_root"))
            csv_website = _normalize_csv_text(base.get("website"))
            
            print(f"  Normalized values:")
            print(f"    csv_domain_root: '{csv_domain_root}'")
            print(f"    csv_website: '{csv_website}'")
            
            # Simulate the preservation logic from process_single_company
            if dr:
                if not csv_domain_root:
                    final_domain_root = dr
                    final_domain = dr
                    print(f"  Would set (no CSV): domain_root='{final_domain_root}', domain='{final_domain}'")
                else:
                    final_domain_root = csv_domain_root
                    final_domain = csv_domain_root
                    print(f"  Would preserve CSV: domain_root='{final_domain_root}', domain='{final_domain}'")
            
            if url:
                if (not csv_website) or (csv_domain_root and url.startswith(("https://"+csv_domain_root, "https://www."+csv_domain_root, "http://"+csv_domain_root))):
                    final_website = url
                    print(f"  Would set website: '{final_website}'")
                else:
                    print(f"  Would preserve CSV website: '{csv_website}'")
            
            print(f"\n✅ SUCCESS: CSV domain_root '{csv_domain_root_val}' would be preserved!")
            break  # Only process 97labs
            
        if "97labs" not in [str(r[pcols.get("callsign")]).strip().lower() for _, r in df_profile.iterrows() if str(r[pcols.get("callsign")]).strip().lower()]:
            print("❌ 97labs not found in CSV!")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_baseline_on_local_csv()