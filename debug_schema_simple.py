#!/usr/bin/env python
"""Simple diagnostic to check Intel Archive schema"""

import os
import json
import requests

def debug_schema_simple():
    """Check Intel Archive schema directly"""
    
    intel_db_id = "24e951a7f21580eeab37ce4f94b2a37f" 
    
    token = os.getenv("NOTION_API_KEY")
    if not token:
        print("Set NOTION_API_KEY to run this test")
        return
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    # Get database schema
    try:
        response = requests.get(
            f"https://api.notion.com/v1/databases/{intel_db_id}",
            headers=headers
        )
        response.raise_for_status()
        data = response.json()
        
        print("=== Intel Archive Database Properties ===")
        properties = data.get("properties", {})
        
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type")
            print(f"Property: '{prop_name}' | Type: {prop_type}")
            
            if prop_type == "relation":
                rel_db_id = prop_def.get("relation", {}).get("database_id")
                print(f"  -> Relation points to DB: {rel_db_id}")
                
        print(f"\n=== Title Property ===")
        title_prop = data.get("title", [{}])[0].get("plain_text", "No title")
        print(f"Database title: {title_prop}")
        
        # Try to create a test page to see what fails
        test_props = {
            "Company": {"title": [{"text": {"content": "TEST COMPANY"}}]},
            "Callsign": {"relation": [{"id": "247951a7f21580ffb496c6381c8e75fd"}]}  # Point to SeeRM DB
        }
        
        print(f"\n=== Test Page Creation ===")
        print(f"Attempting to create with properties: {json.dumps(test_props, indent=2)}")
        
        create_response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={
                "parent": {"database_id": intel_db_id},
                "properties": test_props
            }
        )
        
        if create_response.status_code == 400:
            print("❌ 400 Bad Request - Schema mismatch!")
            print(f"Error details: {create_response.text}")
        else:
            print("✅ Test page creation succeeded!")
            print(f"Response: {create_response.status_code}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_schema_simple()