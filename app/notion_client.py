# app/notion_client.py
from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")


# -------------------- HTTP --------------------


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_get(path: str, params: Dict[str, Any] | None = None) -> requests.Response:
    r = requests.get(f"{NOTION_API}{path}", headers=_headers(), params=params, timeout=30)
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


def notion_delete(path: str) -> requests.Response:
    r = requests.delete(f"{NOTION_API}{path}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r


def notion_query_db(db_id: str, filter_json: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(
        f"{NOTION_API}/databases/{db_id}/query", headers=_headers(), json=filter_json, timeout=30
    )
    r.raise_for_status()
    return r.json()


# -------------------- Schema/utils --------------------


def _rt_segments(text: str, chunk: int = 1800) -> Dict[str, Any]:
    # Break into <= ~2k chars per segment (rich_text item limit)
    parts = [text[i : i + chunk] for i in range(0, len(text), chunk)] or [""]
    return {"rich_text": [{"type": "text", "text": {"content": p}} for p in parts]}


def _title(text: str) -> Dict[str, Any]:
    parts = [text[i : i + 1800] for i in range(0, len(text), 1800)] or [""]
    return {"title": [{"type": "text", "text": {"content": p}} for p in parts]}


def _date_iso(dt: Optional[str]) -> Dict[str, Any]:
    return {"date": {"start": dt or datetime.date.today().isoformat()}}


def get_db_schema(db_id: str) -> Dict[str, Any]:
    return notion_get(f"/databases/{db_id}").json()


def get_title_prop_name(schema: Dict[str, Any]) -> str:
    for name, meta in (schema.get("properties") or {}).items():
        if meta.get("type") == "title":
            return name
    return "Name"


def prop_exists(schema: Dict[str, Any], name: str, typ: str) -> bool:
    meta = (schema.get("properties") or {}).get(name)
    return bool(meta and meta.get("type") == typ)


def _first_prop_of_type(
    schema: Dict[str, Any], typ: str, preferred: str | None = None
) -> Optional[str]:
    if preferred and prop_exists(schema, preferred, typ):
        return preferred
    for name, meta in (schema.get("properties") or {}).items():
        if meta.get("type") == typ:
            return name
    return None


def _get_rich_text_plain(props: Dict[str, Any], prop: str) -> str:
    node = (props.get(prop) or {}).get("rich_text") or []
    return "".join(x.get("plain_text", "") for x in node)


def _bytes(s: str) -> int:
    try:
        return len(s.encode("utf-8"))
    except Exception:
        return len(s)


# -------------------- Companies DB helpers (existing) --------------------


def find_company_page(
    companies_db_id: str, callsign: str, title_prop: Optional[str] = None
) -> Optional[str]:
    schema = get_db_schema(companies_db_id)
    title_prop = title_prop or get_title_prop_name(schema)
    data = notion_query_db(
        companies_db_id, {"filter": {"property": title_prop, "title": {"equals": callsign}}}
    )
    res = data.get("results", [])
    return res[0]["id"] if res else None


def upsert_company_page(companies_db_id: str, payload: Dict[str, Any]) -> str:
    """
    payload keys: callsign (required), company, website, domain, owners (list[str]), tags (list[str]), needs_dossier (bool)
    Writes whatever props exist in the DB schema.
    """
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    cs = payload["callsign"]
    # Find existing
    data = notion_query_db(
        companies_db_id, {"filter": {"property": title_prop, "title": {"equals": cs}}}
    )
    pid = data.get("results", [{}])
    pid = pid[0]["id"] if pid else None

    props: Dict[str, Any] = {title_prop: _title(cs)}
    if prop_exists(schema, "Company", "rich_text") and payload.get("company"):
        props["Company"] = _rt_segments(payload["company"])
    if prop_exists(schema, "Website", "url") and payload.get("website"):
        props["Website"] = {"url": payload["website"]}
    if prop_exists(schema, "Domain", "url") and payload.get("domain"):
        props["Domain"] = {
            "url": (
                f"https://{payload['domain']}"
                if not payload["domain"].startswith("http")
                else payload["domain"]
            )
        }
    elif prop_exists(schema, "Domain", "rich_text") and payload.get("domain"):
        props["Domain"] = _rt_segments(payload["domain"])
    if prop_exists(schema, "Owners", "rich_text") and payload.get("owners"):
        props["Owners"] = _rt_segments(", ".join(payload["owners"]))
    if prop_exists(schema, "Tags", "multi_select") and payload.get("tags"):
        props["Tags"] = {"multi_select": [{"name": t[:90]} for t in payload["tags"] if t]}
    if (
        prop_exists(schema, "Needs Dossier", "checkbox")
        and payload.get("needs_dossier") is not None
    ):
        props["Needs Dossier"] = {"checkbox": bool(payload["needs_dossier"])}

    if pid:
        notion_patch(f"/pages/{pid}", {"properties": props})
        return pid
    res = notion_post(
        "/pages", {"parent": {"database_id": companies_db_id}, "properties": props}
    ).json()
    return res["id"]


def set_latest_intel(
    companies_page_id: str,
    summary_text: str,
    date_iso: Optional[str] = None,
    companies_db_id: Optional[str] = None,
):
    """
    Safely set Latest Intel and Last Intel At (if those props exist).
    If companies_db_id provided, we verify property existence/types first.
    """
    props: Dict[str, Any] = {}
    if companies_db_id:
        schema = get_db_schema(companies_db_id)
        if prop_exists(schema, "Latest Intel", "rich_text"):
            props["Latest Intel"] = _rt_segments(summary_text or "")
        if prop_exists(schema, "Last Intel At", "date"):
            props["Last Intel At"] = _date_iso(date_iso)
        if not props:
            return
    else:
        props = {
            "Latest Intel": _rt_segments(summary_text or ""),
            "Last Intel At": _date_iso(date_iso),
        }
    notion_patch(f"/pages/{companies_page_id}", {"properties": props})


def set_needs_dossier(companies_page_id: str, needs: bool = True):
    notion_patch(
        f"/pages/{companies_page_id}", {"properties": {"Needs Dossier": {"checkbox": bool(needs)}}}
    )


def get_company_domain_data(companies_db_id: str, callsign: str) -> Dict[str, Optional[str]]:
    """Fetch domain/website data from Notion for a company by callsign."""
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    data = notion_query_db(
        companies_db_id, {"filter": {"property": title_prop, "title": {"equals": callsign}}}
    )

    results = data.get("results", [])
    if not results:
        return {"domain": None, "website": None}

    page = results[0]
    properties = page.get("properties", {})

    # Extract domain
    domain = None
    domain_prop = properties.get("Domain")
    if domain_prop:
        if domain_prop.get("type") == "url":
            url = domain_prop.get("url")
            if url:
                # Clean domain from URL format
                domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        elif domain_prop.get("type") == "rich_text":
            rich_text = domain_prop.get("rich_text", [])
            if rich_text and rich_text[0].get("text"):
                domain = rich_text[0]["text"].get("content", "").strip()

    # Extract website
    website = None
    website_prop = properties.get("Website")
    if website_prop and website_prop.get("type") == "url":
        website = website_prop.get("url")

    return {"domain": domain, "website": website}


def get_all_companies_domain_data(
    companies_db_id: str, callsigns: List[str]
) -> Dict[str, Dict[str, Optional[str]]]:
    """Efficiently fetch domain/website data for multiple companies from Notion."""
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    # Fetch all company pages (paginated)
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        query_params = {"page_size": 100}  # Max page size
        if start_cursor:
            query_params["start_cursor"] = start_cursor

        data = notion_query_db(companies_db_id, query_params)
        results = data.get("results", [])
        all_results.extend(results)

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    # Extract domain data for requested callsigns
    domain_data = {}
    callsigns_set = set(cs.lower() for cs in callsigns)

    for page in all_results:
        properties = page.get("properties", {})

        # Get callsign from title
        title_prop_data = properties.get(title_prop, {})
        title_array = title_prop_data.get("title", [])
        if not title_array:
            continue

        callsign = title_array[0].get("text", {}).get("content", "").strip().lower()
        if callsign not in callsigns_set:
            continue

        # Extract domain
        domain = None
        domain_prop = properties.get("Domain")
        if domain_prop:
            if domain_prop.get("type") == "url":
                url = domain_prop.get("url")
                if url:
                    domain = url.replace("https://", "").replace("http://", "").split("/")[0]
            elif domain_prop.get("type") == "rich_text":
                rich_text = domain_prop.get("rich_text", [])
                if rich_text and rich_text[0].get("text"):
                    domain = rich_text[0]["text"].get("content", "").strip()

        # Extract website
        website = None
        website_prop = properties.get("Website")
        if website_prop and website_prop.get("type") == "url":
            website = website_prop.get("url")

        domain_data[callsign] = {"domain": domain, "website": website}

    # Fill in missing callsigns with None values
    for cs in callsigns:
        if cs.lower() not in domain_data:
            domain_data[cs.lower()] = {"domain": None, "website": None}

    return domain_data


def get_companies_needing_dossiers(companies_db_id: str) -> List[Tuple[str, str]]:
    """
    Query Notion for companies that have 'Needs Dossier' = true.
    Returns list of (callsign, page_id) tuples.
    """
    schema = get_db_schema(companies_db_id)
    title_prop = get_title_prop_name(schema)

    # Only proceed if the database has a "Needs Dossier" property
    if not prop_exists(schema, "Needs Dossier", "checkbox"):
        return []

    # Query for companies with Needs Dossier = true
    filter_query = {"filter": {"property": "Needs Dossier", "checkbox": {"equals": True}}}

    companies_needing_dossiers = []
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            filter_query["start_cursor"] = start_cursor

        data = notion_query_db(companies_db_id, filter_query)
        results = data.get("results", [])

        for page in results:
            page_id = page["id"]
            properties = page.get("properties", {})

            # Get callsign from title
            title_prop_data = properties.get(title_prop, {})
            title_array = title_prop_data.get("title", [])
            if title_array:
                callsign = title_array[0].get("text", {}).get("content", "").strip()
                if callsign:
                    companies_needing_dossiers.append((callsign, page_id))

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return companies_needing_dossiers


def page_has_dossier(page_id: str) -> bool:
    """
    Check if a Notion page already has a dossier by looking for a "Dossier" heading.
    Returns True if dossier exists, False otherwise.
    """
    try:
        blocks = _list_block_children(page_id)
        for block in blocks:
            block_type = block.get("type")
            if block_type == "heading_2":
                heading_text = ""
                rich_text = block.get("heading_2", {}).get("rich_text", [])
                for text_obj in rich_text:
                    heading_text += text_obj.get("plain_text", "")
                if "Dossier" in heading_text:
                    return True
        return False
    except Exception:
        # If we can't check, assume no dossier to be safe
        return False


# -------------------- Intel Archive (timeline-only) --------------------


def _intel_schema_hints(intel_db_id: str) -> Dict[str, Optional[str]]:
    s = get_db_schema(intel_db_id)
    
    # Debug schema detection
    print(f"[DEBUG SCHEMA] Intel DB schema properties:")
    for prop_name, prop_def in s.get("properties", {}).items():
        prop_type = prop_def.get("type")
        print(f"[DEBUG SCHEMA]   {prop_name}: {prop_type}")
        if prop_type == "relation":
            rel_db = prop_def.get("relation", {}).get("database_id")
            print(f"[DEBUG SCHEMA]     -> Points to DB: {rel_db}")
    
    hints = {
        "title": get_title_prop_name(s),  # This should be "Company" (title column)
        "callsign_rel": _first_prop_of_type(
            s, "relation", preferred="Callsign"
        ),  # Callsign = relation
        "company_rel": None,  # No separate company relation - company name is the title
        "callsign_prop": None,  # No separate callsign text property - callsign is the relation
        "date_prop": "Date" if prop_exists(s, "Date", "date") else _first_prop_of_type(s, "date"),
        "summary_prop": (
            "Summary"
            if prop_exists(s, "Summary", "rich_text")
            else _first_prop_of_type(s, "rich_text")
        ),
        "last_updated_prop": (
            "Last Updated"
            if prop_exists(s, "Last Updated", "date")
            else _first_prop_of_type(s, "date")
        ),
    }
    
    print(f"[DEBUG SCHEMA] Detected hints: {hints}")
    return hints


def ensure_intel_page(
    intel_db_id: str, companies_db_id: Optional[str], company_page_id: Optional[str], callsign: str
) -> str:
    hints = _intel_schema_hints(intel_db_id)
    title_prop = hints["title"]  # "Company" column (title)
    callsign_rel = hints["callsign_rel"]  # "Callsign" column (relation to Companies DB)

    # 1) Try query by callsign relation (if we have both callsign relation and company_page_id)
    if callsign_rel and company_page_id:
        try:
            data = notion_query_db(
                intel_db_id,
                {"filter": {"property": callsign_rel, "relation": {"contains": company_page_id}}},
            )
            res = data.get("results", [])
            if res:
                return res[0]["id"]
        except requests.HTTPError:
            pass

    # 2) Get company name for the title from SeeRM's "Company" rich_text property
    company_name = f"Intel — {callsign}"  # Fallback
    if company_page_id:
        try:
            company_props = _get_page_properties(company_page_id)
            # In SeeRM: Callsign=title, Company=rich_text
            # We want the Company rich_text value for Intel Archive title
            if "Company" in company_props:
                prop_data = company_props["Company"]
                if prop_data.get("type") == "rich_text":
                    text_parts = prop_data.get("rich_text", [])
                    if text_parts:
                        company_name = "".join(
                            part.get("text", {}).get("content", "") for part in text_parts
                        ).strip()
                        if company_name:  # Only use if non-empty
                            pass
                        else:
                            company_name = f"Intel — {callsign}"  # Fallback for empty company
        except Exception:
            # If we can't get company name, use fallback
            pass

    # 3) Create new Intel page
    props: Dict[str, Any] = {}
    
    print(f"[DEBUG INTEL] Creating Intel page for callsign: {callsign}")
    print(f"[DEBUG INTEL] Schema hints: title_prop='{title_prop}', callsign_rel='{callsign_rel}'")
    print(f"[DEBUG INTEL] company_name='{company_name}', company_page_id='{company_page_id}'")
    
    if title_prop:
        props[title_prop] = _title(company_name)
        print(f"[DEBUG INTEL] Added title property: {title_prop} = '{company_name}'")
    else:
        print(f"[DEBUG INTEL] WARNING: No title property found!")
        
    if callsign_rel and company_page_id:
        props[callsign_rel] = {"relation": [{"id": company_page_id}]}
        print(f"[DEBUG INTEL] Added relation property: {callsign_rel} -> {company_page_id}")
    else:
        print(f"[DEBUG INTEL] WARNING: Missing relation data - callsign_rel='{callsign_rel}', company_page_id='{company_page_id}'")
    
    print(f"[DEBUG INTEL] Final properties being sent: {props}")
    print(f"[DEBUG INTEL] Target database: {intel_db_id}")
    
    try:
        res = notion_post(
            "/pages", {"parent": {"database_id": intel_db_id}, "properties": props}
        )
        print(f"[DEBUG INTEL] Notion API response status: {res.status_code}")
        
        if res.status_code != 200:
            print(f"[DEBUG INTEL] ERROR Response body: {res.text}")
            raise Exception(f"Failed to create Intel page: {res.status_code} - {res.text}")
        
        result = res.json()
        page_id = result["id"]
        print(f"[DEBUG INTEL] SUCCESS: Created Intel page {page_id}")
        return page_id
        
    except Exception as e:
        print(f"[DEBUG INTEL] Exception creating Intel page: {e}")
        raise


def _get_page_properties(page_id: str) -> Dict[str, Any]:
    return notion_get(f"/pages/{page_id}").json().get("properties", {})


def _set_page_props(page_id: str, props: Dict[str, Any]):
    if props:
        notion_patch(f"/pages/{page_id}", {"properties": props})


def _set_summary_latest(page_id: str, summary_prop: str, text: str, max_bytes: int = 250_000):
    """
    Overwrite Summary with ONLY the latest text (no history).
    """
    text = (text or "").strip()
    enc = text.encode("utf-8")
    if len(enc) > max_bytes:
        enc = enc[:max_bytes]
        # ensure valid utf-8 boundary
        while True:
            try:
                text = enc.decode("utf-8")
                break
            except UnicodeDecodeError:
                enc = enc[:-1]
    _set_page_props(page_id, {summary_prop: _rt_segments(text)})


def _append_timeline_group(
    page_id: str, date_iso: str, summary_text: str, items: List[Dict[str, Any]]
):
    """Append a toggle group: heading line + summary paragraph + bullets."""
    toggle_title = f"{date_iso} — Weekly intel"
    bullets: List[Dict[str, Any]] = []
    for it in items or []:
        ttl = (it.get("title") or "").strip()[:180]
        src = (it.get("source") or "").strip()
        url = (it.get("url") or "").strip()
        line = f"{ttl} — {src}".strip(" —")
        bullets.append(
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": line, "link": {"url": url} if url else None},
                        }
                    ]
                },
            }
        )
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": summary_text[:1800]}}]
            },
        }
    ] + bullets

    # First check if we can access the page
    try:
        page_check = notion_get(f"/pages/{page_id}")
        if page_check.status_code != 200:
            print(f"[WARN] Cannot access page {page_id}: {page_check.status_code}")
            raise Exception(f"Cannot access page: {page_check.status_code}")
    except Exception as e:
        print(f"[WARN] Failed to verify page access for {page_id}: {e}")
        raise
    
    # Try to append blocks
    try:
        print(f"[DEBUG BLOCKS] Attempting to append toggle to page {page_id}")
        print(f"[DEBUG BLOCKS] Toggle title: {toggle_title}")
        print(f"[DEBUG BLOCKS] Number of items: {len(items or [])}")
        
        # According to Notion API docs, use POST to append blocks, not PATCH
        r = notion_post(
            f"/blocks/{page_id}/children",
            {
                "children": [
                    {
                        "object": "block",
                        "type": "toggle",
                        "toggle": {
                            "rich_text": [{"type": "text", "text": {"content": toggle_title}}],
                            "children": children,
                        },
                    }
                ]
            },
        )
        
        print(f"[DEBUG BLOCKS] Response status: {r.status_code}")
        
        if r.status_code == 401:
            print(f"[ERROR] Unauthorized to append blocks to {page_id}")
            print(f"[ERROR] Response: {r.text}")
            # Try to get more info about the page
            page_info = notion_get(f"/pages/{page_id}").json()
            parent = page_info.get("parent", {})
            print(f"[ERROR] Page parent type: {parent.get('type')}, parent ID: {parent.get('database_id') or parent.get('page_id')}")
            raise Exception(f"Unauthorized to modify page {page_id} - API integration may need edit permissions")
        elif r.status_code == 400:
            print(f"[ERROR] Bad request appending blocks to {page_id}")
            print(f"[ERROR] Response: {r.text}")
            raise Exception(f"Bad request: {r.text}")
        
        r.raise_for_status()
        print(f"[DEBUG BLOCKS] Successfully appended toggle to page {page_id}")
        
    except Exception as e:
        print(f"[ERROR] Failed to append timeline group: {e}")
        raise


def _estimate_block_text_bytes(block: Dict[str, Any]) -> int:
    t = block.get("type")
    node = block.get(t, {})
    txt = ""
    if "rich_text" in node:
        txt += "".join(x.get("plain_text", "") for x in node.get("rich_text") or [])
    if node.get("children"):
        for ch in node["children"]:
            t2 = ch.get("type")
            nd2 = ch.get(t2, {})
            if "rich_text" in nd2:
                txt += "".join(x.get("plain_text", "") for x in nd2.get("rich_text") or [])
    return _bytes(txt)


def _list_block_children(page_id: str, page_size: int = 100) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start = None
    while True:
        params = {"page_size": page_size}
        if start:
            params["start_cursor"] = start
        r = notion_get(f"/blocks/{page_id}/children", params=params).json()
        out.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        start = r.get("next_cursor")
    return out


def _append_sources_summary(page_id: str, date_iso: str, source_metadata: Dict[str, List[str]]):
    """
    Append or update a 'News Sources' section at the bottom of the Intel Archive page.
    This provides clickable URLs for RSS feeds and readable search queries used this week.
    """
    rss_feeds = source_metadata.get("rss_feeds", [])
    search_queries = source_metadata.get("search_queries", [])

    if not rss_feeds and not search_queries:
        return  # No sources to add

    # Build sources content
    sources_content = []

    if rss_feeds:
        sources_content.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": "📡 RSS Feeds:", "link": None},
                            "annotations": {"bold": True},
                        }
                    ]
                },
            }
        )

        # Add each RSS feed as a bulleted list item with clickable link
        for feed_url in rss_feeds:
            sources_content.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": feed_url, "link": {"url": feed_url}},
                            }
                        ]
                    },
                }
            )

    if search_queries:
        if sources_content:  # Add spacing if we had RSS feeds above
            sources_content.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": ""}}]},
                }
            )

        sources_content.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": "🔍 Search Queries:", "link": None},
                            "annotations": {"bold": True},
                        }
                    ]
                },
            }
        )

        # Add each search query as a bulleted list item
        for query in search_queries:
            # Make Google search URL clickable
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            sources_content.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": '"{query}"', "link": {"url": search_url}},
                            }
                        ]
                    },
                }
            )

    # Create the main sources toggle block
    sources_toggle = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [
                {"type": "text", "text": {"content": f"📋 News Sources Used - {date_iso}"}}
            ],
            "children": sources_content,
        },
    }

    # Append to page
    notion_post(f"/blocks/{page_id}/children", json={"children": [sources_toggle]})


def _prune_oldest_toggles_by_budget(page_id: str, approx_max_bytes: int = 800_000):
    """
    Keep the page's top-level toggle groups within an approximate byte budget.
    We estimate text bytes; if over budget, delete oldest toggles.
    """
    blocks = _list_block_children(page_id)
    toggles = [b for b in blocks if b.get("type") == "toggle"]
    est_bytes = 0
    keep_ids: List[str] = []
    for b in reversed(toggles):  # newest first
        sz = _estimate_block_text_bytes(b)
        if est_bytes + sz <= approx_max_bytes:
            est_bytes += sz
            keep_ids.append(b["id"])
        else:
            break
    keep_set = set(keep_ids)
    for b in toggles:
        if b["id"] not in keep_set:
            try:
                notion_delete(f"/blocks/{b['id']}")
            except Exception:
                pass


def _set_last_updated(
    intel_page_id: str,
    companies_page_id: Optional[str],
    date_iso: str,
    intel_db_id: str,
    companies_db_id: Optional[str],
):
    s = get_db_schema(intel_db_id)
    if prop_exists(s, "Last Updated", "date"):
        _set_page_props(intel_page_id, {"Last Updated": _date_iso(date_iso)})
    if companies_page_id and companies_db_id:
        sc = get_db_schema(companies_db_id)
        if prop_exists(sc, "Last Intel At", "date"):
            notion_patch(
                f"/pages/{companies_page_id}",
                {"properties": {"Last Intel At": _date_iso(date_iso)}},
            )


def update_intel_archive_for_company(
    intel_db_id: str,
    companies_db_id: Optional[str],
    company_page_id: Optional[str],
    callsign: str,
    date_iso: str,
    summary_text: str,
    items: List[Dict[str, Any]],
    summary_max_bytes: int = 250_000,
    timeline_max_bytes: int = 800_000,
    overwrite_summary_only: bool = True,
    source_metadata: Optional[Dict[str, List[str]]] = None,
) -> str:
    """
    Upsert a single Intel page per company, overwrite Summary with latest (no history),
    append a dated toggle group of news items, and enforce a coarse timeline budget.
    """
    callsign = (callsign or "").strip()
    summary_text = (summary_text or "").strip()
    date_iso = date_iso or datetime.date.today().isoformat()

    page_id = ensure_intel_page(intel_db_id, companies_db_id, company_page_id, callsign)

    # 1) Summary: overwrite only (no archive)
    hints = _intel_schema_hints(intel_db_id)
    summary_prop = hints["summary_prop"]
    if summary_prop and overwrite_summary_only:
        # Include date prefix in the visible text so the DB view shows freshness
        latest_line = f"[{date_iso}] {summary_text}" if summary_text else ""
        _set_summary_latest(page_id, summary_prop, latest_line, max_bytes=summary_max_bytes)

    # 2) Append timeline (toggle with bullets)
    _append_timeline_group(page_id, date_iso, summary_text, items)

    # 2.5) Append sources summary if provided
    if source_metadata:
        _append_sources_summary(page_id, date_iso, source_metadata)

    # 3) Prune oldest groups to keep under budget
    _prune_oldest_toggles_by_budget(page_id, approx_max_bytes=timeline_max_bytes)

    # 4) Last Updated stamps
    _set_last_updated(page_id, company_page_id, date_iso, intel_db_id, companies_db_id)

    return page_id
