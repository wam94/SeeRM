#!/usr/bin/env python
"""Test writing to Intel Archive DB directly"""

import os
import sys
sys.path.insert(0, '.')

from app.notion_client import (
    update_intel_archive_for_company,
    upsert_company_page,
    get_db_schema,
    _intel_schema_hints,
)

def test_full_intel_archive_flow():
    """Test the complete Intel Archive flow"""
    
    # Set up environment variables if not set
    intel_db_id = os.getenv("NOTION_INTEL_DB_ID", "24e951a7f21580eeab37ce4f94b2a37f")
    companies_db_id = os.getenv("NOTION_COMPANIES_DB_ID", "247951a7f21580ffb496c6381c8e75fd")
    
    if not os.getenv("NOTION_API_KEY"):
        print("ERROR: NOTION_API_KEY not set")
        return False
        
    print(f"Intel DB ID: {intel_db_id}")
    print(f"Companies DB ID: {companies_db_id}")
    
    # Test callsign
    test_callsign = "TEST_CLAUDE_CODE"
    
    print(f"\n=== Testing full Intel Archive flow for {test_callsign} ===")
    
    # Step 1: Create/get company page in SeeRM DB
    try:
        print("Step 1: Creating/getting company page...")
        company_page_id = upsert_company_page(
            companies_db_id,
            {
                "callsign": test_callsign,
                "company": "Test Claude Code Company",
                "owners": ["Claude"],
                "needs_dossier": False,
            }
        )
        print(f"Company page ID: {company_page_id}")
    except Exception as e:
        print(f"Error creating company page: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 2: Test Intel Archive update
    try:
        print("Step 2: Updating Intel Archive...")
        test_items = [
            {
                "title": "Test News Item 1",
                "source": "Test Source",
                "url": "https://test.com/1",
                "published_at": "2025-09-08",
            },
            {
                "title": "Test News Item 2", 
                "source": "Another Test Source",
                "url": "https://test.com/2",
                "published_at": "2025-09-08",
            }
        ]
        
        intel_page_id = update_intel_archive_for_company(
            intel_db_id=intel_db_id,
            companies_db_id=companies_db_id,
            company_page_id=company_page_id,
            callsign=test_callsign,
            date_iso="2025-09-08",
            summary_text="Test summary for Claude Code testing",
            items=test_items,
            source_metadata={"rss_feeds": ["https://test.com/rss"], "search_queries": ["test query"]},
        )
        print(f"Intel page ID: {intel_page_id}")
        print("✅ SUCCESS! Intel Archive update completed.")
        return True
        
    except Exception as e:
        print(f"Error updating Intel Archive: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_full_intel_archive_flow()
    if success:
        print("\n🎉 Test completed successfully! Intel Archive functionality is working.")
    else:
        print("\n❌ Test failed. Intel Archive has issues.")
