#!/usr/bin/env python3
"""
Debug CSV parsing specifically for domain_root column mapping.
"""

import pandas as pd
import sys
import os

def debug_csv_domain_parsing():
    print("=== DEBUGGING CSV DOMAIN PARSING ===\n")
    
    # Load the sample CSV directly
    csv_path = "files/Will Accounts Demographics_2025-09-01T09_09_22.742205229Z.csv"
    
    if not os.path.exists(csv_path):
        print(f"❌ CSV file not found at {csv_path}")
        return
    
    try:
        df = pd.read_csv(csv_path)
        print(f"✅ CSV loaded with {len(df)} rows, {len(df.columns)} columns")
        print(f"Raw columns: {list(df.columns)}")
        
        # Test lower_cols mapping
        def lower_cols(df):
            return {c.lower().strip(): c for c in df.columns}
        
        pcols = lower_cols(df)
        print(f"Column mapping: {pcols}")
        
        # Find 97labs row
        for idx, r in df.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower() if pcols.get("callsign") in r else ""
            if cs == "97labs":
                print(f"\n✅ Found 97labs at row {idx}")
                
                # Test each column access
                print("\nColumn access test:")
                print(f"  callsign column exists: {'callsign' in pcols}")
                print(f"  callsign raw value: '{r[pcols.get('callsign')]}'")
                
                print(f"  dba column exists: {'dba' in pcols}")
                if 'dba' in pcols:
                    print(f"  dba raw value: '{r[pcols.get('dba')]}'")
                
                print(f"  domain_root column exists: {'domain_root' in pcols}")
                if 'domain_root' in pcols:
                    domain_raw = r[pcols.get("domain_root")]
                    print(f"  domain_root raw value: '{domain_raw}' (type: {type(domain_raw)})")
                    print(f"  is pd.isna: {pd.isna(domain_raw)}")
                    print(f"  str conversion: '{str(domain_raw)}'")
                else:
                    print("  ❌ domain_root column not found in mapping!")
                    print(f"  Available keys: {list(pcols.keys())}")
                
                print(f"  beneficial_owners column exists: {'beneficial_owners' in pcols}")
                if 'beneficial_owners' in pcols:
                    owners_raw = r[pcols.get("beneficial_owners")]
                    print(f"  beneficial_owners raw value: '{owners_raw}'")
                
                # Test the exact logic from the code
                print("\nTesting exact parsing logic:")
                csv_domain_root_val = r[pcols.get("domain_root")] if pcols.get("domain_root") in r else None
                print(f"  Step 1 - raw extraction: '{csv_domain_root_val}' (type: {type(csv_domain_root_val)})")
                
                if pd.isna(csv_domain_root_val):
                    csv_domain_root_val = None
                    print(f"  Step 2 - after NaN check: None")
                else:
                    print(f"  Step 2 - not NaN, keeping: '{csv_domain_root_val}'")
                
                if csv_domain_root_val is not None:
                    csv_domain_root_val = str(csv_domain_root_val).strip()
                    print(f"  Step 3 - after str/strip: '{csv_domain_root_val}'")
                    if not csv_domain_root_val:
                        csv_domain_root_val = None
                        print(f"  Step 4 - empty string, set to None")
                    else:
                        print(f"  Step 4 - final value: '{csv_domain_root_val}'")
                else:
                    print(f"  Step 3-4 - was None, staying None")
                
                break
        else:
            print("❌ 97labs not found in CSV!")
            print("Available callsigns:")
            for idx, r in df.iterrows():
                cs = str(r[pcols.get("callsign")]).strip().lower() if pcols.get("callsign") in r else ""
                if cs:
                    print(f"  - '{cs}'")
                if idx >= 10:  # Show first 10 only
                    print("  ... (truncated)")
                    break
                    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_csv_domain_parsing()