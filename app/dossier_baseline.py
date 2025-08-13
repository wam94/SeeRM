from __future__ import annotations
import os, io, json
from datetime import datetime, timedelta
from typing import List, Dict, Any

import pandas as pd

from app.gmail_client import (
    build_service, search_messages, get_message,
    extract_csv_attachments, send_html_email
)
# Reuse helpers from the news job
from app.news_job import (
    fetch_csv_by_subject, build_queries, try_rss_feeds,
    google_cse_search, dedupe, within_days
)

# ---------------------- Env helpers ----------------------

def getenv(n: str, d: str | None = None) -> str | None:
    v = os.getenv(n)
    return d if v in (None, "") else v

def load_latest_weekly_csv(service, user, q, attachment_regex):
    msgs = search_messages(service, user, q, max_results=5)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if atts:
            name, data = atts[0]
            return pd.read_csv(io.BytesIO(data))
    return None

# ---------------------- Evidence collection ----------------------

def collect_recent_news(org: Dict[str, Any], lookback_days: int, g_api_key: str | None, g_cse_id: str | None,
                        max_items: int = 6, max_queries: int = 5) -> List[Dict[str, Any]]:
    """RSS + Google CSE (optional) for the last N days."""
    items: List[Dict[str, Any]] = []

    # RSS/blog (prefer explicit blog_url; else website)
    site_for_rss = org.get("blog_url") or org.get("website")
    if site_for_rss:
        items += try_rss_feeds(site_for_rss)

    # Google CSE (site + name queries + optional owners)
    if g_api_key and g_cse_id:
        queries = build_queries(
            org.get("dba"), org.get("website"), org.get("owners"),
            domain_root=org.get("domain_root"),
            aka_names=org.get("aka_names"),
            tags=org.get("industry_tags"),
        )
        for q in queries[:max_queries]:
            try:
                items += google_cse_search(g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=5)
            except Exception:
                continue

    # Clean and limit to window
    items = dedupe(items, key=lambda x: x["url"])
    items = [x for x in items if within_days(x.get("published_at", datetime.utcnow()), lookback_days)]
    # Normalize minimal fields
    for it in items:
        it["title"] = it.get("title") or ""
        it["url"] = it.get("url") or ""
        it["source"] = it.get("source") or ""
        if isinstance(it.get("published_at"), datetime):
            it["published_at"] = it["published_at"].strftime("%Y-%m-%d")
    return items[:max_items]

def collect_people_background(org: Dict[str, Any], lookback_days: int, g_api_key: str | None, g_cse_id: str | None,
                              max_people: int = 3) -> List[Dict[str, Any]]:
    """Light background on key contacts: prior roles/companies and profiles."""
    results: List[Dict[str, Any]] = []
    owners = [o for o in (org.get("owners") or []) if o]
    owners = owners[:max_people]
    if not (g_api_key and g_cse_id) or not owners:
        return results

    for person in owners:
        qs = [
            f'"{person}" "{org.get("dba") or org.get("domain_root") or ""}" (founder OR cofounder OR CFO OR COO OR CTO OR CEO OR head)',
            f'"{person}" (LinkedIn OR Crunchbase OR AngelList OR PitchBook)',
            f'"{person}" (previous OR formerly OR ex-)',
        ]
        items: List[Dict[str, Any]] = []
        for q in qs:
            try:
                items += google_cse_search(g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=3)
            except Exception:
                continue
        items = dedupe(items, key=lambda x: x["url"])[:5]
        # Normalize
        for it in items:
            it["title"] = it.get("title") or ""
            it["url"] = it.get("url") or ""
            it["source"] = it.get("source") or ""
        results.append({
            "name": person,
            "findings": items
        })
    return results

# ---------------------- Prompting ----------------------

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "resolved_company": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "website": {"type": "string"},
                "confidence": {"type": "number"}
            },
            "required": ["name", "confidence"]
        },
        "executive_summary": {"type": "string"},
        "company_overview": {"type": "string"},
        "products_services": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "use_cases": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"}
            }}
        },
        "recent_announcements": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "date": {"type": "string"},
                "title": {"type": "string"},
                "url": {"type": "string"},
                "summary": {"type": "string"}
            }}
        },
        "key_customers": {"type": "array", "items": {"type": "string"}},
        "people": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "role": {"type": "string"},
                "bio": {"type": "string"}
            }}
        },
        "risks_unknowns": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["resolved_company", "executive_summary"]
}

GUIDANCE = """You are an account manager at a banking technology company serving startups (often growth-stage, 5–100 employees).
Cross-reference the DBA (company name), website, and the provided contacts to decide which business is being examined.
Some companies change names/websites—do not treat either as gospel.

Then, using the evidence provided:
- Pull key facts about the company: what they do, products/services and use cases, any key customers mentioned publicly.
- Identify recent announcements (last 6 months): launches, funding, major partnerships; include 1–2 short summaries.
- Provide short background on key people (contacts and any clearly relevant leaders): prior roles/companies/startups.

Return a single VALID JSON object only (no markdown, no extra text), using this schema:
{schema}

Keep language crisp and factual, no hype. If data is missing or uncertain, include a short note in risks/unknowns.
"""

def openai_structured(prompt: str) -> Dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        # Ask for JSON only; we'll parse it
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            messages=[{"role":"user","content":prompt}],
            temperature=0.2,
        )
        content = resp.choices[0].message.content.strip()
        # try to extract JSON
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start:end+1])
    except Exception:
        return None
    return None

def make_dossier_json(org: Dict[str, Any], news_items: List[Dict[str, Any]], people_bg: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Prepare evidence text (simple, compact)
    news_str = "\n".join([f'- {n.get("title","")} — {n.get("source","")} ({n.get("date") or n.get("published_at","")}) {n.get("url","")}' for n in news_items[:8]])
    people_str = "\n".join([f'* {p["name"]}:\n  ' + "\n  ".join([f'- {it.get("title","")} ({it.get("source","")}) {it.get("url","")}' for it in p.get("findings", [])[:4]]) for p in people_bg])

    prompt = GUIDANCE.format(schema=json.dumps(JSON_SCHEMA, indent=2)) + "\n\n" + \
        f"Company (from internal profile):\n- Callsign: {org.get('callsign')}\n- DBA: {org.get('dba')}\n- Website: {org.get('website')}\n- Domain: {org.get('domain_root')}\n- AKA: {org.get('aka_names')}\n- Contacts: {', '.join(org.get('owners') or [])}\n- Tags: {org.get('industry_tags')}\n\n" + \
        f"Recent public items (last 6 months):\n{news_str or '(none found)'}\n\n" + \
        f"Background notes on people:\n{people_str or '(none)'}\n"

    data = openai_structured(prompt)
    if data is None:
        # graceful fallback: a minimal JSON with just basic info
        return {
            "resolved_company": {
                "name": org.get("dba") or (org.get("domain_root") or org.get("callsign") or ""),
                "website": org.get("website") or "",
                "confidence": 0.4,
            },
            "executive_summary": "LLM unavailable; baseline includes recent links below.",
            "company_overview": "",
            "products_services": [],
            "recent_announcements": [{"date": it.get("published_at",""), "title": it.get("title",""), "url": it.get("url","")} for it in news_items[:3]],
            "key_customers": [],
            "people": [{"name": p["name"], "role": "", "bio": ""} for p in people_bg],
            "risks_unknowns": ["Structured generation unavailable; review evidence links."],
            "next_actions": ["Skim the listed links; confirm identity via website/about page."]
        }
    return data

# ---------------------- Main ----------------------

def main():
    # Inputs
    callsigns = [c.strip().lower() for c in getenv("BASELINE_CALLSIGNS","").split(",") if c.strip()]
    if not callsigns:
        raise SystemExit("Set BASELINE_CALLSIGNS to a comma-separated list of callsigns.")

    lookback_days = int(getenv("BASELINE_LOOKBACK_DAYS", "180"))  # 6 months
    g_api_key = getenv("GOOGLE_API_KEY")
    g_cse_id  = getenv("GOOGLE_CSE_ID")

    # Gmail service
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = getenv("GMAIL_USER") or os.environ["GMAIL_USER"]

    # Load profile + weekly CSVs
    profile_subject = getenv("NEWS_PROFILE_SUBJECT") or "Org Profile — Will Mitchell"
    weekly_query = getenv("NEWS_GMAIL_QUERY") or 'from:metabase subject:"Weekly Diff — Will Mitchell" has:attachment filename:csv newer_than:30d'
    df_profile = fetch_csv_by_subject(svc, user, profile_subject)
    weekly = load_latest_weekly_csv(svc, user, weekly_query, getenv("ATTACHMENT_REGEX", r".*\.csv$"))

    if df_profile is None and weekly is None:
        raise SystemExit("Need at least one CSV (profile or weekly) to build a baseline.")

    # Build profile lookup
    prof: Dict[str, Dict[str, Any]] = {}
    def lower_cols(df): return {c.lower().strip(): c for c in df.columns}

    if df_profile is not None:
        pcols = lower_cols(df_profile)
        for _, r in df_profile.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower()
            if not cs: continue
            prof[cs] = {
                "callsign": r[pcols.get("callsign")],
                "dba": r[pcols.get("dba")] if pcols.get("dba") else None,
                "website": r[pcols.get("website")] if pcols.get("website") else None,
                "domain_root": r[pcols.get("domain_root")] if pcols.get("domain_root") else None,
                "aka_names": r[pcols.get("aka_names")] if pcols.get("aka_names") else None,
                "blog_url": r[pcols.get("blog_url")] if pcols.get("blog_url") else None,
                "rss_feeds": r[pcols.get("rss_feeds")] if pcols.get("rss_feeds") else None,
                "linkedin_url": r[pcols.get("linkedin_url")] if pcols.get("linkedin_url") else None,
                "twitter_handle": r[pcols.get("twitter_handle")] if pcols.get("twitter_handle") else None,
                "crunchbase_url": r[pcols.get("crunchbase_url")] if pcols.get("crunchbase_url") else None,
                "industry_tags": r[pcols.get("industry_tags")] if pcols.get("industry_tags") else None,
                "hq_city": r[pcols.get("hq_city")] if pcols.get("hq_city") else None,
                "hq_region": r[pcols.get("hq_region")] if pcols.get("hq_region") else None,
                "hq_country": r[pcols.get("hq_country")] if pcols.get("hq_country") else None,
                "owners": (r[pcols.get("beneficial_owners")] if pcols.get("beneficial_owners") else "") or ""
            }

    # Merge with weekly (fallback fields only if missing)
    if weekly is not None:
        wcols = lower_cols(weekly)
        for _, r in weekly.iterrows():
            cs = str(r[wcols.get("callsign")]).strip().lower()
            if not cs: continue
            base = prof.get(cs, {"callsign": r[wcols.get("callsign")]})
            for k in ["dba","website","beneficial_owners"]:
                if k == "beneficial_owners":
                    owners = base.get("owners") or (r[wcols.get("beneficial_owners")] if wcols.get("beneficial_owners") else "")
                    base["owners"] = [s.strip() for s in str(owners).split(",") if s.strip()]
                else:
                    if not base.get(k) and wcols.get(k):
                        base[k] = r[wcols.get(k)]
            prof[cs] = base

    # Build list to process (filtered by BASELINE_CALLSIGNS)
    targets = []
    for cs in callsigns:
        if cs in prof:
            targets.append(prof[cs])
        else:
            # minimal shell so you can still run
            targets.append({"callsign": cs, "dba": cs, "owners": []})

    # Create dossiers
    dossiers: List[Dict[str, Any]] = []
    for org in targets:
        news_items = collect_recent_news(org, lookback_days, g_api_key, g_cse_id)
        people_bg  = collect_people_background(org, lookback_days, g_api_key, g_cse_id)
        data = make_dossier_json(org, news_items, people_bg)
        dossiers.append({"callsign": org.get("callsign"), "data": data})

    # Output / email
    preview = getenv("PREVIEW_ONLY", "true").lower() in ("1","true","yes","y")
    if preview:
        # Print first ~2k chars of JSON for quick inspection
        js = json.dumps(dossiers, indent=2)
        print(js[:2000])
        return

    # Simple HTML wrapper for email
    body = ["<html><body><h2>Baselines</h2>"]
    for d in dossiers:
        body.append(f"<h3>{d.get('callsign')}</h3><pre>{json.dumps(d['data'], indent=2)}</pre><hr/>")
    body.append("</body></html>")
    html = "\n".join(body)

    to = getenv("DIGEST_TO") or getenv("GMAIL_USER") or ""
    send_html_email(build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    ), getenv("GMAIL_USER") or "", to, f"Baselines — {datetime.utcnow().date()}", html)
    print("Baselines emailed to", to)

if __name__ == "__main__":
    main()
