# app/notion_client.py
from __future__ import annotations
import os
import datetime
from typing import Dict, Any, Optional, List

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

# -------------------------
# Headers & small helpers
# -------------------------

def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def _rt(text: str) -> Dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": (text or "")[:2000]}}]}

def _title(text: str) -> Dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": (text or '')[:2000]}}]}

def _url(u: Optional[str]) -> Dict[str, Any]:
    return {"url": (u if u else None)}

def _date_iso(dt: Optional[str]) -> Dict[str, Any]:
    return {"date": {"start": dt or datetime.date.today().isoformat()}}

def _checkbox(v: bool) -> Dict[str, Any]:
    return {"checkbox": bool(v)}

def _multi_select(tags: Optional[List[str]]) -> Dict[str, Any]:
    return {"multi_select": [{"name": (t or "")[:90]} for t in (tags or []) if t]}

def _ensure_http(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    s = u.strip()
    if not s:
        return None
    if not s.startswith(("http://","https://")):
        s = "https://" + s
    return s

# -------------------------
# Basic HTTP wrappers
# -------------------------

def notion_get(path: str) -> requests.Response:
    r = requests.get(f"{NOTION_API}{path}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r

def notion_post(path: str, json: Dict[str, Any]) -> requests.Response:
    r = requests.post(f"{NOTION_API}{path}", headers=_headers(), json=json, timeout=30)
    r.raise_for_status()
    return r

def notion_patch(path: str, json: Dict[str, Any]) -> requests.Response:
    r = requests.patch(f"{NOTION_API}{path}", headers=_headers(), json=json, timeout=30)
    r.raise_for_status()
    return r

# -------------------------
# Schema helpers
# -------------------------

def get_db_schema(db_id: str) -> Dict[str, Any]:
    return notion_get(f"/databases/{db_id}").json()

def get_title_prop_name(schema: Dict[str, Any]) -> str:
    props = schema.get("properties", {})
    for name, meta in props.items():
        if meta.get("type") == "title":
            return name
    # Fallback – Notion DBs must have a title prop, but just in case:
    return "Name"

def prop_exists(schema: Dict[str, Any], name: str, typ: Optional[str] = None) -> bool:
    meta = schema.get("properties", {}).get(name)
    if not meta:
        return False
    if typ is None:
        return True
    return meta.get("type") == typ

def get_prop_type(schema: Dict[str, Any], name: str) -> Optional[str]:
    meta = schema.get("properties", {}).get(name)
    return meta.get("type") if meta else None

# -------------------------
# Page lookup / upsert
# -------------------------

def _find_page_by_title(db_id: str, title_prop: str, title_value: str) -> Optional[str]:
    data = notion_post(f"/databases/{db_id}/query", {
        "filter": {"property": title_prop, "title": {"equals": title_value}}
    }).json()
    results = data.get("results", [])
    return results[0]["id"] if results else None

def upsert_company_page(companies_db_id: str, payload: Dict[str, Any]) -> str:
    """
    Create or update a row in the Companies DB, adapting to its schema.

    payload MUST include:
      - callsign (used as the DB title)

    Optional payload keys we honor (if columns exist in the DB schema):
      - company (string)         -> 'Company' (rich_text)
      - website (url string)     -> 'Website' (url)
      - domain (bare root)       -> 'Domain' (url preferred; or rich_text fallback)
      - owners (list[str])       -> 'Owners' (rich_text as comma-separated list)
      - tags (list[str])         -> 'Tags' (multi_select)
      - needs_dossier (bool)     -> 'Needs Dossier' (checkbox)
    """
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    callsign = payload["callsign"]
    page_id = _find_page_by_title(companies_db_id, title_prop, callsign)

    # Build properties map safely against schema
    props: Dict[str, Any] = {title_prop: _title(callsign)}

    if prop_exists(schema, "Company", "rich_text") and payload.get("company"):
        props["Company"] = _rt(payload["company"])

    # WEBSITE
    if prop_exists(schema, "Website", "url"):
        props["Website"] = _url(_ensure_http(payload.get("website")))

    # DOMAIN – prefer url typed column if available, else fall back to rich_text
    domain_val = (payload.get("domain") or "").strip() or None
    if domain_val:
        dom_type = get_prop_type(schema, "Domain")
        if dom_type == "url":
            props["Domain"] = _url(_ensure_http(domain_val))
        elif dom_type == "rich_text":
            props["Domain"] = _rt(domain_val)
        # If no 'Domain' column, try a reasonable alternative name some templates use
        elif prop_exists(schema, "Domain (Text)", "rich_text"):
            props["Domain (Text)"] = _rt(domain_val)

    if prop_exists(schema, "Owners", "rich_text") and payload.get("owners"):
        owners_csv = ", ".join([o for o in (payload.get("owners") or []) if o])
        props["Owners"] = _rt(owners_csv)

    if prop_exists(schema, "Tags", "multi_select") and payload.get("tags"):
        props["Tags"] = _multi_select(payload["tags"])

    if prop_exists(schema, "Needs Dossier", "checkbox") and (payload.get("needs_dossier") is not None):
        props["Needs Dossier"] = _checkbox(bool(payload["needs_dossier"]))

    if page_id:
        notion_patch(f"/pages/{page_id}", {"properties": props})
        return page_id
    else:
        res = notion_post("/pages", {
            "parent": {"database_id": companies_db_id},
            "properties": props
        }).json()
        return res["id"]

# -------------------------
# Convenience property patch
# -------------------------

def patch_company_properties(page_id: str, companies_db_id: str, payload: Dict[str, Any]):
    """Patch a subset of properties reliably (useful when schema changed recently)."""
    schema = get_db_schema(companies_db_id)
    props: Dict[str, Any] = {}

    if payload.get("company") and prop_exists(schema, "Company", "rich_text"):
        props["Company"] = _rt(payload["company"])

    if prop_exists(schema, "Website", "url"):
        props["Website"] = _url(_ensure_http(payload.get("website")))

    domain_val = (payload.get("domain") or "").strip() or None
    if domain_val:
        dom_type = get_prop_type(schema, "Domain")
        if dom_type == "url":
            props["Domain"] = _url(_ensure_http(domain_val))
        elif dom_type == "rich_text":
            props["Domain"] = _rt(domain_val)
        elif prop_exists(schema, "Domain (Text)", "rich_text"):
            props["Domain (Text)"] = _rt(domain_val)

    if payload.get("owners") and prop_exists(schema, "Owners", "rich_text"):
        props["Owners"] = _rt(", ".join([o for o in payload["owners"] if o]))

    if payload.get("tags") and prop_exists(schema, "Tags", "multi_select"):
        props["Tags"] = _multi_select(payload["tags"])

    if payload.get("needs_dossier") is not None and prop_exists(schema, "Needs Dossier", "checkbox"):
        props["Needs Dossier"] = _checkbox(bool(payload["needs_dossier"]))

    if props:
        notion_patch(f"/pages/{page_id}", {"properties": props})

# -------------------------
# Content append helpers
# -------------------------

def append_blocks(block_id: str, blocks: List[Dict[str, Any]]):
    """Append child blocks to a page or block."""
    if not blocks:
        return
    notion_patch(f"/blocks/{block_id}/children", {"children": blocks})

def append_dossier_blocks(page_id: str, markdown_body: str):
    """Append a 'Dossier' heading and the markdown body as paragraphs (chunked)."""
    chunks = [markdown_body[i:i+1800] for i in range(0, len(markdown_body), 1800)] or [markdown_body]
    blocks: List[Dict[str, Any]] = []

    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Dossier"}}]}
    })

    for ch in chunks:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": ch}}]}
        })

    append_blocks(page_id, blocks)

# -------------------------
# Other convenience setters
# -------------------------

def set_latest_intel(companies_page_id: str, summary_text: str, date_iso: Optional[str] = None,
                     companies_db_id: Optional[str] = None):
    """
    Safely set 'Latest Intel' and 'Last Intel At' if those columns exist.
    If companies_db_id is provided, we read the schema to guard types.
    """
    props: Dict[str, Any] = {}
    if companies_db_id:
        schema = get_db_schema(companies_db_id)
        if prop_exists(schema, "Latest Intel", "rich_text"):
            props["Latest Intel"] = _rt(summary_text or "")
        if prop_exists(schema, "Last Intel At", "date"):
            props["Last Intel At"] = _date_iso(date_iso)
        if not props:
            return
    else:
        props = {
            "Latest Intel": _rt(summary_text or ""),
            "Last Intel At": _date_iso(date_iso),
        }
    notion_patch(f"/pages/{companies_page_id}", {"properties": props})

def append_intel_log(intel_db_id: str, company_page_id: str, callsign: str,
                     date_iso: str, summary_text: str, items: List[Dict[str, Any]]):
    """Create a new row in an 'Intel Log' DB and append bulleted links for items."""
    try:
        res = notion_post("/pages", {
            "parent": {"database_id": intel_db_id},
            "properties": {
                "Company": {"relation": [{"id": company_page_id}]},
                "Callsign": _rt(callsign),
                "Date": {"date": {"start": date_iso}},
                "Summary": _rt(summary_text or ""),
            }
        }).json()
    except requests.HTTPError as e:
        try:
            print("INTEL_LOG create error body:", e.response.text[:800])
        except Exception:
            pass
        raise

    log_page_id = res["id"]

    bullets: List[Dict[str, Any]] = []
    for it in items:
        title = (it.get("title") or "")[:180]
        url = it.get("url") or ""
        src = it.get("source") or ""
        line = f"{title} — {src}".strip(" —")
        bullets.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": line, "link": {"url": url} if url else None}
                }]
            }
        })
    if bullets:
        try:
            append_blocks(log_page_id, bullets)
        except requests.HTTPError as e:
            try:
                print("INTEL_LOG children error body:", e.response.text[:800])
            except Exception:
                pass
            raise

def set_needs_dossier(companies_page_id: str, needs: bool = True):
    try:
        notion_patch(f"/pages/{companies_page_id}", {"properties": {"Needs Dossier": _checkbox(needs)}})
    except requests.HTTPError as e:
        try:
            print("SET_NEEDS_DOSSIER error body:", e.response.text[:800])
        except Exception:
            pass
        raise
