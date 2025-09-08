#!/usr/bin/env python
"""Test Intel Archive API access in GitHub Actions environment"""

import os
import sys
sys.path.insert(0, '.')

def test_intel_api():
    """Test Intel Archive API in environment where NOTION_API_KEY is available"""
    
    # Import our existing client functions
    from app.notion_client import notion_get, notion_post, notion_patch
    
    intel_db_id = "24e951a7f21580eeab37ce4f94b2a37f"
    companies_db_id = "247951a7f21580ffb496c6381c8e75fd"
    
    if not os.getenv("NOTION_API_KEY"):
        print("ERROR: NOTION_API_KEY not available")
        return False
    
    print("=== Testing Intel Archive API Access ===")
    print(f"Intel DB: {intel_db_id}")
    print(f"Companies DB: {companies_db_id}")
    
    try:
        # Test 1: Get database schema
        print("\n1. Fetching Intel Archive schema...")
        response = notion_get(f"/databases/{intel_db_id}")
        data = response.json()
        
        properties = data.get("properties", {})
        print("Found properties:")
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type")
            print(f"  - {prop_name}: {prop_type}")
            if prop_type == "relation":
                rel_db = prop_def.get("relation", {}).get("database_id")
                print(f"    -> Points to: {rel_db}")
                if rel_db == companies_db_id:
                    print(f"    ✅ Correctly points to SeeRM DB")
                else:
                    print(f"    ❌ Points to different DB than expected")
        
        # Test 2: Try minimal page creation
        print("\n2. Creating minimal test page...")
        test_props = {
            "Company": {"title": [{"text": {"content": "API Test Page"}}]}
        }
        
        response = notion_post(
            "/pages", 
            {"parent": {"database_id": intel_db_id}, "properties": test_props}
        )
        
        if response.status_code == 200:
            print("✅ SUCCESS! Created minimal page")
            page_data = response.json()
            page_id = page_data.get("id")
            print(f"Created page ID: {page_id}")
            
            # Clean up by archiving
            cleanup_response = notion_patch(f"/pages/{page_id}", {"archived": True})
            if cleanup_response.status_code == 200:
                print("✅ Cleaned up test page")
            
            return True
        else:
            print(f"❌ Failed to create page: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Exception during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_intel_api()
    if success:
        print("\n✅ Intel Archive API access is working!")
    else:
        print("\n❌ Intel Archive API access failed")
        sys.exit(1)