#!/usr/bin/env python3
"""
Test script to verify Gmail CSV fetch is working properly.
This will test the fixed fetch_csv_by_subject function.
"""
import os
import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from gmail_client import build_service
from news_job import fetch_csv_by_subject


def test_csv_fetch():
    """Test the Gmail CSV fetch functionality."""

    print("🔍 Testing Gmail CSV fetch...")
    print("=" * 50)

    # Get credentials from environment
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    user = os.environ.get("GMAIL_USER")
    subject = os.environ.get("PROFILE_SUBJECT", "Alert: Will Accounts Demographics has results")

    if not all([client_id, client_secret, refresh_token, user]):
        print("❌ Missing Gmail credentials in environment variables:")
        print(f"   GMAIL_CLIENT_ID: {'✓' if client_id else '✗'}")
        print(f"   GMAIL_CLIENT_SECRET: {'✓' if client_secret else '✗'}")
        print(f"   GMAIL_REFRESH_TOKEN: {'✓' if refresh_token else '✗'}")
        print(f"   GMAIL_USER: {'✓' if user else '✗'}")
        return False

    print(f"📧 Gmail User: {user}")
    print(f"🔍 Subject to search: '{subject}'")

    try:
        # Build Gmail service
        print("\n🔗 Building Gmail service...")
        service = build_service(client_id, client_secret, refresh_token)
        print("✅ Gmail service built successfully")

        # Test the CSV fetch
        print(f"\n📥 Fetching CSV with subject containing: '{subject}'...")
        df = fetch_csv_by_subject(service, user, subject)

        if df is None:
            print("❌ No CSV found!")
            print("   This could mean:")
            print("   1. No email found with that subject")
            print("   2. Email found but no CSV attachment")
            print("   3. CSV attachment found but failed to parse")
            return False

        print(f"✅ CSV fetched successfully!")
        print(f"📊 DataFrame shape: {df.shape} (rows: {df.shape[0]}, columns: {df.shape[1]})")
        print(f"📋 Columns: {list(df.columns)}")

        # Check for callsign column
        callsign_cols = [c for c in df.columns if "callsign" in c.lower()]
        if callsign_cols:
            print(f"🎯 Found callsign column(s): {callsign_cols}")

            # Show sample of callsign data
            callsign_col = callsign_cols[0]
            non_empty = df[df[callsign_col].notna() & (df[callsign_col] != "")][callsign_col]
            print(f"📈 Non-empty callsigns found: {len(non_empty)}")

            if len(non_empty) > 0:
                print(f"🔍 Sample callsigns: {list(non_empty.head(5))}")
                return True
            else:
                print("⚠️ Callsign column exists but has no data")
                return False
        else:
            print("⚠️ No 'callsign' column found in CSV")
            print(f"   Available columns: {list(df.columns)}")
            return False

    except Exception as e:
        print(f"❌ Error occurred: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_csv_fetch()
    sys.exit(0 if success else 1)
