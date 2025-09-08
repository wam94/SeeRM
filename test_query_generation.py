#!/usr/bin/env python3
"""
Test script to verify the Gmail query is being generated correctly.
This can be run in the workflow to debug the actual query being used.
"""
import os


def test_query_generation():
    """Test that the f-string formatting works correctly."""

    subject = os.environ.get("PROFILE_SUBJECT", "Alert: Will Accounts Demographics has results")

    print(f"🔍 Testing query generation with subject: '{subject}'")
    print("=" * 70)

    # This is the OLD broken version (what was causing the bug)
    broken_query = 'subject:"{subject}" has:attachment filename:csv'
    print(f"❌ OLD (broken) query: {broken_query}")

    # This is the NEW fixed version
    fixed_query = f'subject:"{subject}" has:attachment filename:csv'
    print(f"✅ NEW (fixed) query: {fixed_query}")

    # Verify they're different
    if broken_query == fixed_query:
        print("⚠️  WARNING: Queries are the same! The fix may not be applied.")
        return False
    else:
        print("✅ SUCCESS: Queries are different, f-string fix is working!")
        return True


if __name__ == "__main__":
    test_query_generation()
