#!/usr/bin/env python
"""Debug Intel Archive schema"""

import os
import sys
sys.path.insert(0, '.')

from app.notion_client import get_db_schema, _intel_schema_hints

def debug_intel_schema():
    intel_db_id = "24e951a7f21580eeab37ce4f94b2a37f"
    
    if not os.getenv("NOTION_API_KEY"):
        print("ERROR: NOTION_API_KEY not set")
        return False
    
    print(f"=== Intel Archive Schema Debug ===")
    print(f"Intel DB ID: {intel_db_id}")
    
    try:
        # Get raw schema
        schema = get_db_schema(intel_db_id)
        print(f"\n=== Raw Schema ===")
        for prop_name, prop_def in schema.get("properties", {}).items():
            prop_type = prop_def.get("type")
            print(f"  {prop_name}: {prop_type}")
            if prop_type == "relation":
                database_id = prop_def.get("relation", {}).get("database_id")
                print(f"    -> Points to DB: {database_id}")
        
        # Get schema hints (what the code expects)
        hints = _intel_schema_hints(intel_db_id)
        print(f"\n=== Schema Hints (Code Expectations) ===")
        for key, value in hints.items():
            print(f"  {key}: {value}")
            
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    debug_intel_schema()