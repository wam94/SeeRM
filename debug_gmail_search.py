#!/usr/bin/env python3
"""
Debug Gmail search to find why CSV isn't being found.
Run this as a GitHub Action to test different search patterns.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from gmail_client import build_service, search_messages, get_message

def test_gmail_search():
    print("=== DEBUGGING GMAIL CSV SEARCH ===\n")
    
    # Gmail setup
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = os.environ["GMAIL_USER"]
    
    # Test different search patterns
    search_patterns = [
        # Original pattern
        'subject:"Alert: Will Accounts Demographics has results" has:attachment filename:csv',
        
        # Without quotes around subject
        'subject:Alert: Will Accounts Demographics has results has:attachment filename:csv',
        
        # Partial subject match
        'subject:"Will Accounts Demographics" has:attachment filename:csv',
        
        # Just from metabase
        'from:metabase@mercury.com has:attachment filename:csv',
        
        # From metabase with subject keywords
        'from:metabase@mercury.com subject:Demographics has:attachment filename:csv',
        
        # From metabase with Alert keyword
        'from:metabase@mercury.com subject:Alert has:attachment filename:csv newer_than:10d',
        
        # The actual pattern used in NEWS_GMAIL_QUERY
        'from:metabase@mercury.com subject:"Will Accounts Demographics" has:attachment filename:csv newer_than:10d'
    ]
    
    for i, pattern in enumerate(search_patterns, 1):
        print(f"{i}. Testing search: {pattern}")
        try:
            msgs = search_messages(svc, user, pattern, max_results=5)
            print(f"   Found {len(msgs)} messages")
            
            if msgs:
                # Get details of first message
                msg = get_message(svc, user, msgs[0]["id"])
                subject = ""
                for header in msg.get("payload", {}).get("headers", []):
                    if header["name"].lower() == "subject":
                        subject = header["value"]
                        break
                print(f"   First message subject: '{subject}'")
                
                # Check for attachments
                payload = msg.get("payload", {})
                parts = payload.get("parts", []) or []
                csv_attachments = []
                for part in parts:
                    filename = part.get("filename", "")
                    if filename.endswith(".csv"):
                        csv_attachments.append(filename)
                
                if csv_attachments:
                    print(f"   CSV attachments: {csv_attachments}")
                else:
                    print("   No CSV attachments found")
            else:
                print("   No messages found")
                
        except Exception as e:
            print(f"   Error: {e}")
        
        print()

if __name__ == "__main__":
    test_gmail_search()