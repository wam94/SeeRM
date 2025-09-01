# app/notion_client.py
from __future__ import annotations
import os
import requests
import datetime
from typing import Dict, Any, Optional, List

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

# ---------- Low-level helpers ----------

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
    return {"multi_select": [{"name": t[:90]} for t in (tags or []) if t]}

# HTTP

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
    Adapts to the DB's title property name and only sets properties that exist.
    Handles 'Domain' whether it's a URL or rich_text property.
    """
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    cs = payload["callsign"]
    pid = find_company_page(companies_db_id, cs, title_prop)

    company_name = payload.get("company") or payload.get("dba") or ""
    props: Dict[str, Any] = {title_prop: _title(cs)}

    if prop_exists(schema, "Company", "rich_text"):
        props["Company"] = _rt(company_name)

    if prop_exists(schema, "Website", "url"):
        props["Website"] = _url(payload.get("website"))

    # Domain can be url or rich_text depending on schema
    domain_raw = payload.get("domain")
    domain_val = (str(domain_raw) if domain_raw is not None and not (isinstance(domain_raw, float) and str(domain_raw) == 'nan') else "").strip()
    if domain_val:
        if prop_exists(schema, "Domain", "url"):
            props["Domain"] = _url(f"https://{domain_val}" if not domain_val.startswith(("http://","https://")) else domain_val)
        elif prop_exists(schema, "Domain", "rich_text"):
            props["Domain"] = _rt(domain_val)

    if prop_exists(schema, "Owners", "rich_text"):
        props["Owners"] = _rt(", ".join(payload.get("owners") or []))

    if prop_exists(schema, "Tags", "multi_select") and payload.get("tags"):
        props["Tags"] = _multi_select(payload["tags"])

    if prop_exists(schema, "Needs Dossier", "checkbox") and payload.get("needs_dossier") is not None:
        props["Needs Dossier"] = _checkbox(bool(payload["needs_dossier"]))

    if pid:
        notion_patch(f"/pages/{pid}", {"properties": props})
        return pid
    else:
        res = notion_post("/pages", {
            "parent": {"database_id": companies_db_id},
            "properties": props
        }).json()
        return res["id"]

# ---------- Company updates ----------

def set_latest_intel(companies_page_id: str, summary_text: str, date_iso: Optional[str] = None, companies_db_id: Optional[str] = None):
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
    notion_patch(f"/pages/{companies_page_id}", {"properties": props})

def set_needs_dossier(companies_page_id: str, needs: bool = True):
    notion_patch(f"/pages/{companies_page_id}", {"properties": {"Needs Dossier": _checkbox(needs)}})

# ---------- Intel archive helpers ----------

def append_intel_log(intel_db_id: str, company_page_id: str, callsign: str,
                     date_iso: str, summary_text: str, items: List[Dict[str, Any]], company_name: str = ""):
    """
    Create one Intel log page and append bulleted items:
    'YYYY-MM-DD — <linked title> — source' (title hyperlinked when URL present).
    """
    try:
        res = notion_post("/pages", {
            "parent": {"database_id": intel_db_id},
            "properties": {
                "Company": {"title": [{"type": "text", "text": {"content": (company_name or callsign)[:200]}}]},
                "Callsign": {"relation": [{"id": company_page_id}]},
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
        dt    = (it.get("published_at") or "").strip()
        title = (it.get("title") or it.get("url") or "News item").strip()[:180]
        url   = (it.get("url") or "").strip()
        src   = (it.get("source") or "").strip()

        rich: List[Dict[str, Any]] = []
        if dt:
            rich.append({"type": "text", "text": {"content": f"{dt} — "}})
        rich.append({
            "type": "text",
            "text": {"content": title, "link": {"url": url} if url else None}
        })
        if src:
            rich.append({"type": "text", "text": {"content": f" — {src}"}})

        bullets.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rich}
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

def append_structured_items(intel_db_id: str, company_page_id: str, callsign: str,
                            items: List[Dict[str, Any]], company_name: str = ""):
    """
    (Optional) Also create one DB row per item if suitable columns exist:
      - Headline (title), Item Date (date), Item Source (rich_text), Item URL (url)
    Safe to call even if these columns are not present.
    """
    schema = get_db_schema(intel_db_id)
    for it in items:
        title = (it.get("title") or it.get("url") or "News item")[:180]
        props: Dict[str, Any] = {}
        if prop_exists(schema, "Headline", "title"):
            props["Headline"] = {"title": [{"type": "text", "text": {"content": title}}]}
        if prop_exists(schema, "Company", "title"):
            props["Company"] = {"title": [{"type": "text", "text": {"content": (company_name or callsign)[:200]}}]}
        if prop_exists(schema, "Callsign", "relation"):
            props["Callsign"] = {"relation": [{"id": company_page_id}]}
        if prop_exists(schema, "Item Date", "date"):
            props["Item Date"] = {"date": {"start": (it.get("published_at") or datetime.date.today().isoformat())}}
        if prop_exists(schema, "Item Source", "rich_text"):
            props["Item Source"] = _rt(it.get("source") or "")
        if prop_exists(schema, "Item URL", "url"):
            props["Item URL"] = _url(it.get("url"))
        if props:
            try:
                notion_post("/pages", {
                    "parent": {"database_id": intel_db_id},
                    "properties": props
                })
            except requests.HTTPError as e:
                try:
                    print("INTEL_ITEM create error body:", e.response.text[:500])
                except Exception:
                    pass
