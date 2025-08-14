# app/notion_client.py
from __future__ import annotations
import os, requests, datetime
from typing import Dict, Any, Optional, List

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

# ---------- Headers / helpers ----------

def _headers():
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
    return {"multi_select": [{"name": str(t)[:90]} for t in (tags or []) if t]}

def _ensure_http(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    s = u.strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s

# ---------- Basic HTTP ----------

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

def notion_query_db(db_id: str, filter_json: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(f"{NOTION_API}/databases/{db_id}/query", headers=_headers(), json=filter_json, timeout=30)
    r.raise_for_status()
    return r.json()

# ---------- Schema helpers ----------

def get_db_schema(db_id: str) -> Dict[str, Any]:
    return notion_get(f"/databases/{db_id}").json()

def get_title_prop_name(schema: Dict[str, Any]) -> str:
    props = schema.get("properties", {})
    for name, meta in props.items():
        if meta.get("type") == "title":
            return name
    return "Name"

def prop_exists(schema: Dict[str, Any], name: str, typ: str) -> bool:
    meta = schema.get("properties", {}).get(name)
    return bool(meta and meta.get("type") == typ)

def prop_type(schema: Dict[str, Any], name: str) -> Optional[str]:
    meta = schema.get("properties", {}).get(name)
    return meta.get("type") if meta else None

# ---------- Company page lookup / upsert ----------

def find_company_page(companies_db_id: str, callsign: str, title_prop: str) -> Optional[str]:
    data = notion_query_db(companies_db_id, {
        "filter": {"property": title_prop, "title": {"equals": callsign}}
    })
    results = data.get("results", [])
    return results[0]["id"] if results else None

def upsert_company_page(companies_db_id: str, payload: Dict[str, Any]) -> str:
    """
    payload must include: callsign;
    optional keys: company/dba, website, domain, owners, tags, needs_dossier (bool)

    Writes Domain as URL if the Notion property 'Domain' is typed 'url',
    otherwise falls back to rich_text. Website is optional.
    """
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    cs = payload["callsign"]
    pid = find_company_page(companies_db_id, cs, title_prop)

    company_name = payload.get("company") or payload.get("dba") or ""
    website = payload.get("website") or None
    domain = payload.get("domain") or None
    owners = payload.get("owners") or []
    tags = payload.get("tags")
    needs_dossier = payload.get("needs_dossier")

    props: Dict[str, Any] = {title_prop: _title(cs)}

    # Company
    if prop_exists(schema, "Company", "rich_text"):
        props["Company"] = _rt(company_name)

    # Website (optional, only if DB has it)
    if prop_exists(schema, "Website", "url") and website:
        props["Website"] = _url(_ensure_http(website))

    # Domain (prefer URL type, but fall back to rich_text if that's how the DB is configured)
    dom_typ = prop_type(schema, "Domain")
    if domain and dom_typ == "url":
        props["Domain"] = _url(_ensure_http(domain))
    elif domain and dom_typ == "rich_text":
        props["Domain"] = _rt(domain)

    # Owners
    if prop_exists(schema, "Owners", "rich_text"):
        props["Owners"] = _rt(", ".join(owners) if isinstance(owners, list) else str(owners))

    # Tags
    if prop_exists(schema, "Tags", "multi_select") and tags:
        props["Tags"] = _multi_select(tags)

    # Needs Dossier
    if prop_exists(schema, "Needs Dossier", "checkbox") and needs_dossier is not None:
        props["Needs Dossier"] = _checkbox(bool(needs_dossier))

    try:
        if pid:
            notion_patch(f"/pages/{pid}", {"properties": props})
            return pid
        else:
            res = notion_post("/pages", {
                "parent": {"database_id": companies_db_id},
                "properties": props
            }).json()
            return res["id"]
    except requests.HTTPError as e:
        try:
            print("UPSERT error body:", e.response.text[:800])
        except Exception:
            pass
        raise

# ---------- Updates on company page ----------

def set_latest_intel(companies_page_id: str, summary_text: str, date_iso: Optional[str] = None,
                     companies_db_id: Optional[str] = None):
    """
    Safely set Latest Intel and Last Intel At (if those props exist).
    If companies_db_id provided, we verify property existence/types first.
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
    try:
        notion_patch(f"/pages/{companies_page_id}", {"properties": props})
    except requests.HTTPError as e:
        try:
            print("LATEST_INTEL error body:", e.response.text[:800])
        except Exception:
            pass
        raise

def append_intel_log(intel_db_id: str, company_page_id: str, callsign: str,
                     date_iso: str, summary_text: str, items: List[Dict[str, Any]]):
    """
    Create a row in the Intel Archive DB, relate to the company, and append bulleted links.
    Expects Intel DB to have: Company (relation), Callsign (rich_text), Date (date), Summary (rich_text)
    """
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
    bullets = []
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
            notion_patch(f"/blocks/{log_page_id}/children", {"children": bullets})
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
