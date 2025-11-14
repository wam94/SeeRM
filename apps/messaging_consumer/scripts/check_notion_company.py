import os
from notion_client import Client

notion_api_key = os.getenv("NOTION_API_KEY")
companies_db_id = os.getenv("NOTION_COMPANIES_DB_ID")

print("NOTION_API_KEY:", notion_api_key)
print("NOTION_COMPANIES_DB_ID:", companies_db_id)

if not notion_api_key or not companies_db_id:
    raise SystemExit("Missing NOTION_API_KEY or NOTION_COMPANIES_DB_ID")

client = Client(auth=notion_api_key)
resp = client.databases.query(
    database_id=companies_db_id,
    filter={"property": "Callsign", "rich_text": {"equals": "aalo"}},
    page_size=1,
)

print("Result count:", len(resp.get("results", [])))
if resp.get("results"):
    print("First page id:", resp["results"][0]["id"])
