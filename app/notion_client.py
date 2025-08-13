from __future__ import annotations
import os, requests, datetime
from typing import Dict, Any, Optional, List

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

def _headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def _rt(text: str) -> Dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}  # soft cap

def _title(text: str) -> Dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": text[:2000]}}]}

def _url(u: Optional[str]) -> Dict[str, Any]:
    return {"url": (u if u else None)}

def _date_iso(dt: Optional[str]) -> Dict[str, Any]:
    return {"date": {"start": dt or datetime.date.today().isoformat()}}

def _checkbox(v: bool) -> Dict[str, Any]:
    return {"checkbox": bool(v)}

def _multi_select(tags: Optional[List[str]]) -> Dict[str, Any]:
    return {"multi_select": [{"name": t[:90]} for t in (tags or []) if t]}

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

def find_company_page(companies_db_id: str, callsign: str) -> Optional[str]:
    data = notion_query_db(companies_db_id, {
        "filter": {"property": "Callsign", "title": {"equals": callsign}}
    })
    results = data.get("results", [])
    return results[0]["id"] if results else None

def upsert_company_page(companies_db_id: str, payload: Dict[str, Any]) -> str:
    """payload must include: callsign; optional: dba/company, website, domain, owners, tags, needs_dossier (bool)"""
    cs = payload["callsign"]
    pid = find_company_page(companies_db_id, cs)

    # allow either 'company' or 'dba' coming in
    company_name = payload.get("company") or payload.get("dba") or ""

    props = {
        "Callsign": _title(cs),
        "Company": _rt(company_name),           # <- was "DBA"
        "Website": _url(payload.get("website")),
        "Domain": _rt(payload.get("domain") or ""),
        "Owners": _rt(", ".join(payload.get("owners") or [])),
    }
    if payload.get("tags"):
        props["Tags"] = _multi_select(payload["tags"])
    if payload.get("needs_dossier") is not None:
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

def set_latest_intel(companies_page_id: str, summary_text: str, date_iso: Optional[str] = None):
    props = {
        "Latest Intel": _rt(summary_text or ""),
        "Last Intel At": _date_iso(date_iso),
    }
    notion_patch(f"/pages/{companies_page_id}", {"properties": props})

def append_intel_log(intel_db_id: str, company_page_id: str, callsign: str,
                     date_iso: str, summary_text: str, items: List[Dict[str, Any]]):
    # 1) Create a log row
    res = notion_post("/pages", {
        "parent": {"database_id": intel_db_id},
        "properties": {
            "Company": {"relation": [{"id": company_page_id}]},
            "Callsign": _rt(callsign),
            "Date": {"date": {"start": date_iso}},
            "Summary": _rt(summary_text or ""),
        }
    }).json()
    log_page_id = res["id"]

    # 2) Append bulleted links as children
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
                "rich_text": [{"type": "text", "text": {"content": line, "link": {"url": url} if url else None}}]
            }
        })
    if bullets:
        notion_patch(f"/blocks/{log_page_id}/children", {"children": bullets})

def set_needs_dossier(companies_page_id: str, needs: bool = True):
    notion_patch(f"/pages/{companies_page_id}", {"properties": {"Needs Dossier": _checkbox(needs)}})
