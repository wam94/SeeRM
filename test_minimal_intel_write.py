#!/usr/bin/env python
"""Minimal test to check if we can write ANYTHING to Intel Archive DB"""

import os
import json
import requests

def test_minimal_write():
    """Try the absolute simplest write to Intel Archive"""
    
    intel_db_id = "24e951a7f21580eeab37ce4f94b2a37f"
    companies_db_id = "247951a7f21580ffb496c6381c8e75fd"
    
    token = os.getenv("NOTION_API_KEY")
    if not token:
        print("ERROR: Set NOTION_API_KEY environment variable")
        return False
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    print("=== Testing Minimal Intel Archive Write ===")
    print(f"Intel DB: {intel_db_id}")
    print(f"Companies DB: {companies_db_id}")
    
    # First, get the database schema to understand what's expected
    print("\n1. Fetching Intel Archive schema...")
    try:
        response = requests.get(
            f"https://api.notion.com/v1/databases/{intel_db_id}",
            headers=headers
        )
        if response.status_code != 200:
            print(f"❌ Failed to get DB schema: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
        data = response.json()
        properties = data.get("properties", {})
        
        print("Properties found:")
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type")
            print(f"  - {prop_name}: {prop_type}")
            if prop_type == "relation":
                rel_db = prop_def.get("relation", {}).get("database_id")
                print(f"    -> Points to DB: {rel_db}")
                
    except Exception as e:
        print(f"❌ Error fetching schema: {e}")
        return False
    
    # Test 1: Try with just a title (Company)
    print("\n2. Test 1: Creating page with just Company title...")
    test_props = {
        "Company": {"title": [{"text": {"content": "Test Company Minimal"}}]}
    }
    
    try:
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={
                "parent": {"database_id": intel_db_id},
                "properties": test_props
            }
        )
        
        if response.status_code == 200:
            print("✅ SUCCESS! Created page with just title")
            page_id = response.json().get("id")
            print(f"Page ID: {page_id}")
            
            # Try to delete it to clean up
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=headers,
                    json={"archived": True}
                )
                print("Cleaned up test page")
            except:
                pass
                
            return True
        else:
            print(f"❌ Failed: {response.status_code}")
            error_data = response.json()
            print(f"Error: {json.dumps(error_data, indent=2)}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    # Test 2: Try to get a valid company page ID from SeeRM
    print("\n3. Test 2: Getting a valid company page from SeeRM...")
    try:
        # Query for any company in SeeRM
        response = requests.post(
            f"https://api.notion.com/v1/databases/{companies_db_id}/query",
            headers=headers,
            json={"page_size": 1}
        )
        
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                test_company_id = results[0]["id"]
                test_callsign = "Unknown"
                
                # Try to get the callsign
                props = results[0].get("properties", {})
                if "Callsign" in props:
                    callsign_prop = props["Callsign"]
                    if callsign_prop.get("type") == "title":
                        title_parts = callsign_prop.get("title", [])
                        if title_parts:
                            test_callsign = title_parts[0].get("text", {}).get("content", "Unknown")
                
                print(f"Found company: {test_callsign} (ID: {test_company_id})")
                
                # Now try to create Intel page with relation
                print("\n4. Creating Intel page with Company title and Callsign relation...")
                test_props = {
                    "Company": {"title": [{"text": {"content": f"Test Intel for {test_callsign}"}}]},
                    "Callsign": {"relation": [{"id": test_company_id}]}
                }
                
                response = requests.post(
                    "https://api.notion.com/v1/pages",
                    headers=headers,
                    json={
                        "parent": {"database_id": intel_db_id},
                        "properties": test_props
                    }
                )
                
                if response.status_code == 200:
                    print("✅ SUCCESS! Created Intel page with relation")
                    page_id = response.json().get("id")
                    print(f"Page ID: {page_id}")
                    
                    # Clean up
                    try:
                        requests.patch(
                            f"https://api.notion.com/v1/pages/{page_id}",
                            headers=headers,
                            json={"archived": True}
                        )
                        print("Cleaned up test page")
                    except:
                        pass
                    
                    return True
                else:
                    print(f"❌ Failed: {response.status_code}")
                    error_data = response.json()
                    print(f"Error: {json.dumps(error_data, indent=2)}")
                    
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    return False

if __name__ == "__main__":
    success = test_minimal_write()
    if success:
        print("\n✅ Intel Archive is writable!")
    else:
        print("\n❌ Cannot write to Intel Archive - check API key permissions or database settings")