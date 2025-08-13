# app/dossier_baseline.py
from __future__ import annotations
import os, io, json, requests
from datetime import datetime
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

# Notion helpers
from app.notion_client import upsert_company_page, set_needs_dossier

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

# ---------- Narrative prompt (executive summary style) ----------

DOSSIER_GUIDANCE = """
You are an account manager at a banking technology company serving startups (often growth-stage, 5–100 employees).
Produce a concise, executive-style client profile based ONLY on the evidence provided below and the internal fields.
Do not invent facts; if identity or facts are uncertain, say so briefly.

Process to follow:
1) Cross-reference the DBA/company name, website, and the named contacts to decide which business we’re examining.
   Some companies change names or websites—do not treat either as gospel; resolve the most likely identity.
2) Pull key company info: what they do, products/services and use cases, any publicly mentioned key customers.
3) Find recent announcements (last 6 months): launches, funding, partnerships. Summarize the 1–2 most relevant.
4) Add short background notes on key people (the provided contacts and any clearly relevant leaders): prior roles/companies/startups.

Output format: Markdown. Keep it tight (~400–600 words), crisp, factual, and skimmable. Avoid hype.
Use the following sections and headings exactly:

🔍 Company & Identity
- Who we’re talking about; how you resolved the identity (DBA vs brand vs domain); HQ if available.

🏢 Company Overview
- One short paragraph on stage, size (if inferable), compliance posture if clearly public (e.g., SOC 2 / ISO).

🚀 Product & Use Cases
- 3–6 bullets: product, core capabilities, typical users, high-level use cases.

📰 Recent Announcements (last ~6 months)
- 1–2 bullets with a date, 1–2 sentence summary, and source name. Prefer company sources; include one credible external if useful.

👥 Your Contacts & Key Team
- 2–4 bullets: each person, role, most relevant prior roles/companies/startups.

(Optional) Risks/Unknowns
- 1–3 bullets for uncertainty, gaps, or identity ambiguities that need confirmation.
"""

def _build_evidence_block(org: dict, news_items: list[dict], people_bg: list[dict]) -> str:
    news_lines = []
    for n in news_items[:8]:
        date = n.get("date") or n.get("published_at", "")
        src  = n.get("source", "")
        title = n.get("title", "")
        url = n.get("url", "")
        news_lines.append(f"- {date} — {title} — {src} {url}")

    ppl_lines = []
    for p in people_bg:
        name = p.get("name") or ""
        finds = p.get("findings", [])[:4]
        inner = "\n  ".join([f"- {it.get('title','')} — {it.get('source','')} {it.get('url','')}" for it in finds])
        ppl_lines.append(f"* {name}:\n  {inner}" if inner else f"* {name}")

    evidence = []
    evidence.append("Internal fields:")
    evidence.append(f"- Callsign: {org.get('callsign')}")
    evidence.append(f"- DBA: {org.get('dba')}")
    evidence.append(f"- Website: {org.get('website')}")
    evidence.append(f"- Domain root: {org.get('domain_root')}")
    evidence.append(f"- AKA: {org.get('aka_names')}")
    evidence.append(f"- Contacts: {', '.join(org.get('owners') or [])}")
    evidence.append(f"- Tags: {org.get('industry_tags')}")
    evidence.append("")
    evidence.append("Recent items (last 6 months):")
    evidence.append("\n".join(news_lines) if news_lines else "(none)")
    evidence.append("")
    evidence.append("People background findings:")
    evidence.append("\n".join(ppl_lines) if ppl_lines else "(none)")
    return "\n".join(evidence)

def _openai_write_narrative(prompt: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_CHAT_MODEL","gpt-4o-mini"),
            messages=[{"role":"user","content":prompt}],
            temperature=0.25,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None

def generate_narrative(org: dict, news_items: list[dict], people_bg: list[dict]) -> str:
    evidence = _build_evidence_block(org, news_items, people_bg)
    prompt = f"{DOSSIER_GUIDANCE}\n\nEVIDENCE START\n{evidence}\nEVIDENCE END\n\nWrite the profile now."
    text = _openai_write_narrative(prompt)
    if text:
        return text

    # Fallback if no OpenAI lib/key: a compact template from evidence.
    lines = []
    lines.append("🔍 Company & Identity")
    lines.append(f"- DBA: {org.get('dba') or org.get('domain_root') or org.get('callsign')}")
    lines.append(f"- Website: {org.get('website') or '—'}")
    lines.append("")
    lines.append("🏢 Company Overview")
    lines.append("- (LLM not available) See recent items and people notes below.")
    lines.append("")
    lines.append("🚀 Product & Use Cases")
    lines.append("- (summarize after LLM is enabled)")
    lines.append("")
    lines.append("📰 Recent Announcements (last ~6 months)")
    if news_items:
        for n in news_items[:4]:
            date = n.get("date") or n.get("published_at","")
            src  = n.get("source","")
            lines.append(f"- {date} — {n.get('title','')} — {src} {n.get('url','')}")
    else:
        lines.append("- None found")
    lines.append("")
    lines.append("👥 Your Contacts & Key Team")
    if people_bg:
        for p in people_bg:
            lines.append(f"- {p.get('name')}")
    else:
        lines.append("- (no background findings)")
    return "\n".join(lines)

# ---------------------- Notion push ----------------------

def push_dossier_to_notion(callsign: str, org: dict, markdown_body: str):
    token = os.getenv("NOTION_API_KEY")
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    if not (token and companies_db):
        return
    # Upsert company page (ensure the "Company" prop is filled)
    page_id = upsert_company_page(companies_db, {
        "callsign": callsign,
        "company":  org.get("dba") or "",
        "dba":      org.get("dba") or "",  # send both for backward-compat
        "website":  org.get("website") or "",
        "domain":   org.get("domain_root") or "",
        "owners":   org.get("owners") or [],
        "needs_dossier": False,
    })
    # Append a 'Dossier' section (heading + paragraphs)
    hdr = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Dossier"}}]}
    }
    # Chunk text for Notion API size limits
    chunks = [markdown_body[i:i+1800] for i in range(0, len(markdown_body), 1800)] or [markdown_body]
    paras = [{
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": ch}}]}
    } for ch in chunks]
    requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": os.getenv("NOTION_VERSION","2022-06-28"),
            "Content-Type": "application/json",
        },
        json={"children": [hdr] + paras},
        timeout=30
    ).raise_for_status()
    # Clear the “Needs Dossier” flag
    try:
        set_needs_dossier(page_id, False)
    except Exception:
        pass

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
    profile_subject = getenv("NEWS_PROFILE_SUBJECT")
    weekly_query = getenv("NEWS_GMAIL_QUERY")
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
                    if (not base.get(k)) and wcols.get(k):
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
        narr = generate_narrative(org, news_items, people_bg)
        dossiers.append({"callsign": org.get("callsign"), "body_md": narr})

        # Push to Notion (if configured)
        try:
            push_dossier_to_notion((org.get("callsign") or "").strip(), org, narr)
        except Exception as e:
            print("Notion dossier push error:", e)

    # Output / email
    preview = getenv("PREVIEW_ONLY", "true").lower() in ("1","true","yes","y")
    if preview:
        for d in dossiers:
            print(f"\n=== {d.get('callsign')} ===\n")
            print(d["body_md"][:2500])
            print("\n----------------------------")
        return

    # Simple HTML wrapper for email
    body = ["<html><body><h2>Baselines</h2>"]
    for d in dossiers:
        body.append(f"<h3>{d.get('callsign')}</h3><pre style='white-space:pre-wrap'>{d['body_md']}</pre><hr/>")
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
