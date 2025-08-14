# app/dossier_baseline.py
from __future__ import annotations

import io
import math
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import tldextract

from trafilatura import fetch_url as _t_fetch, extract as _t_extract

# -- Gmail / search utilities from your project
from app.gmail_client import (
    build_service,
    search_messages,
    get_message,
    extract_csv_attachments,
    send_html_email,
)
from app.news_job import (
    fetch_csv_by_subject,
    build_queries,
    try_rss_feeds,
    google_cse_search,
    dedupe,
    within_days,
)

# -- Notion helpers (refreshed to handle Domain url/rich_text)
from app.notion_client import upsert_company_page, set_needs_dossier

# -- Optional funding enrichment (skip if module not present)
try:
    from app.enrich_funding import best_funding
except Exception:
    def best_funding(org: Dict[str, Any], fetched_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {}

# =========================
# Environment / small utils
# =========================

def getenv(n: str, d: Optional[str] = None) -> Optional[str]:
    v = os.getenv(n)
    return d if v in (None, "") else v

DEBUG = (os.getenv("BASELINE_DEBUG", "").lower() in ("1", "true", "yes", "y"))

def logd(msg: str) -> None:
    if DEBUG:
        print(msg)

def ensure_http(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    s = u.strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s

def compute_domain_root(website_or_domain: Optional[str]) -> Optional[str]:
    """
    Normalize any website or hostname to a registered domain (example.com).
    """
    if not website_or_domain:
        return None
    w = str(website_or_domain).strip().lower()
    if not w:
        return None
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    host = w.split("/")[0]
    ext = tldextract.extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    # Fallback to 'host' if we couldn't parse a registered domain
    return host or None

# ---------------
# CSV domain utils
# ---------------

CSV_DOMAIN_CANDIDATES = [
    "domain",
    "domain_root",
    "root_domain",
    "company_domain",
    "primary_domain",
    "website_domain",
    "domainroot",
]

def extract_domain_from_row(row: pd.Series, colmap: Dict[str, str]) -> Optional[str]:
    """
    Return a normalized registered domain from any domain-ish column, or derive from website.
    """
    # Try explicit domain columns first
    for k in CSV_DOMAIN_CANDIDATES:
        c = colmap.get(k)
        if c and c in row and str(row[c]).strip():
            d = compute_domain_root(str(row[c]))
            if d:
                return d
    # Fallback: derive from website
    c = colmap.get("website")
    if c and c in row and str(row[c]).strip():
        return compute_domain_root(str(row[c]))
    return None

def lower_cols(df: pd.DataFrame) -> Dict[str, str]:
    return {c.lower().strip(): c for c in df.columns}

# --------------------
# Domain discovery shim
# --------------------

_BLOCKED_SITES = {
    # content/social we should never treat as "official" company domains
    "linkedin.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "github.com",
    "medium.com",
    "substack.com",
    "notion.so",
    "notion.site",
    "docs.google.com",
    "wikipedia.org",
    "angel.co",
}

def _head_ok(url: str) -> bool:
    try:
        r = requests.head(url, timeout=4, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

def validate_domain_to_url(domain_root: str) -> Optional[str]:
    """
    Try a few URL forms for a domain and return the first that resolves; otherwise first candidate.
    """
    candidates = [
        f"https://{domain_root}",
        f"https://www.{domain_root}",
        f"http://{domain_root}",
    ]
    for u in candidates:
        if _head_ok(u):
            return u
    return candidates[0] if candidates else None

def discover_domain_by_search(name: Optional[str],
                              g_api_key: Optional[str],
                              g_cse_id: Optional[str]) -> Optional[str]:
    """
    Use Google CSE to guess an 'official' site; returns registered_domain. Avoids social/content hosts.
    """
    if not (name and g_api_key and g_cse_id):
        return None
    try:
        q = f'{name} (official site OR homepage) -site:linkedin.com -site:twitter.com -site:x.com'
        items = google_cse_search(g_api_key, g_cse_id, q, num=5)
        for it in items:
            url = (it.get("url") or "").strip()
            if not url:
                continue
            ext = tldextract.extract(url)
            host = (ext.registered_domain or "").lower()
            if not host:
                continue
            if any(host.endswith(b) for b in _BLOCKED_SITES):
                continue
            return host
    except Exception as e:
        logd(f"[discover_domain_by_search] error: {e}")
    return None

def _safe_text_from_url(url: str, timeout: int = 5) -> str:
    """
    Best-effort page text with short timeouts.
    """
    try:
        html = _t_fetch(url, timeout=timeout)
        if not html:
            # fallback if fetch_url returns None
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code < 400:
                html = r.text
        if not html:
            return ""
        txt = _t_extract(html, include_comments=False, include_links=False, favor_recall=True) or ""
        return txt[:25000]
    except Exception:
        return ""

def _score_candidate_domain(domain_root: str, name: str, owners: list[str]) -> int:
    """
    Heuristic: score a candidate domain by checking its homepage text for company/owner signals.
    """
    score = 0
    if not domain_root:
        return -999

    # quick url guess and retrieval
    url = validate_domain_to_url(domain_root) or f"https://{domain_root}"
    text = _safe_text_from_url(url) or ""
    t = text.lower()

    # content signals
    name_l = (name or "").lower().strip()
    if name_l and name_l in t:
        score += 3
    # owner signals
    for o in owners or []:
        oo = (o or "").lower().strip()
        if oo and oo in t:
            score += 3
            break
    # page structure hints
    for kw in ("about", "team", "careers", "jobs", "contact"):
        if kw in t:
            score += 1

    # tiny bonus if homepage actually responds
    if _head_ok(url):
        score += 1

    return score

def discover_domain_smart(name: Optional[str],
                          owners: Optional[list[str]],
                          g_api_key: Optional[str],
                          g_cse_id: Optional[str]) -> Optional[str]:
    """
    Broader discovery: try several queries, collect candidate roots, score by on-page content.
    """
    if not (name and g_api_key and g_cse_id):
        return None

    queries = [
        f'{name} "About Us"',
        f'{name} website',
        f'{name} homepage',
        f'{name} official site',
    ]
    # owners combo query can help when the brand name is generic
    if owners:
        lead = owners[0]
        queries.append(f'{name} {lead} website')
        queries.append(f'{name} {lead} "About"')

    candidates: list[str] = []
    seen = set()

    try:
        for q in queries:
            items = google_cse_search(g_api_key, g_cse_id, q, num=5)
            for it in items or []:
                url = (it.get("url") or "").strip()
                if not url:
                    continue
                ext = tldextract.extract(url)
                root = (ext.registered_domain or "").lower()
                if not root:
                    continue
                if any(root.endswith(b) for b in _BLOCKED_SITES):
                    continue
                if root in seen:
                    continue
                seen.add(root)
                candidates.append(root)
    except Exception as e:
        logd(f"[discover_domain_smart] CSE error: {e}")

    if not candidates:
        return None

    # score and pick best
    owners_list = [o for o in (owners or []) if o]
    scored = [(root, _score_candidate_domain(root, name or "", owners_list)) for root in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_root, best_score = scored[0]

    # require a minimal score so we don't pick random domains
    return best_root if best_score >= 3 else None

def resolve_domain_for_org(org: Dict[str, Any],
                           g_api_key: Optional[str],
                           g_cse_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    strategy = (os.getenv("BASELINE_DOMAIN_STRATEGY", "prefer_csv") or "prefer_csv").lower()

    csv_domain = (org.get("domain") or org.get("domain_root") or "").strip() or None
    if csv_domain:
        csv_domain = compute_domain_root(csv_domain)

    def _finalize(d: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if not d:
            return None, None
        url = validate_domain_to_url(d)
        if DEBUG:
            print(f"[DOMAIN] cs={org.get('callsign')} strategy={strategy} chosen={d} url={url} (csv={csv_domain})")
        return d, url

    # CSV-only path
    if strategy == "csv_only":
        return _finalize(csv_domain)

    # Prefer CSV when present
    if strategy == "prefer_csv" and csv_domain:
        return _finalize(csv_domain)

    # Discovery
    discovered = discover_domain_smart(
        org.get("dba") or org.get("callsign"),
        org.get("owners") or [],
        g_api_key, g_cse_id
    )

    if strategy in ("prefer_discovery", "discovery_only"):
        return _finalize(discovered or (None if strategy == "discovery_only" else csv_domain))

    # prefer_csv but CSV missing
    return _finalize(discovered or csv_domain)

# ================
# Gmail CSV loaders
# ================

def load_latest_weekly_csv(service, user: str, q: Optional[str], attachment_regex: str) -> Optional[pd.DataFrame]:
    if not q:
        return None
    msgs = search_messages(service, user, q, max_results=5)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if atts:
            name, data = atts[0]
            try:
                return pd.read_csv(io.BytesIO(data))
            except Exception:
                pass
    return None

# ======================
# Intel & background pull
# ======================

def collect_recent_news(org: Dict[str, Any],
                        lookback_days: int,
                        g_api_key: Optional[str],
                        g_cse_id: Optional[str],
                        max_items: int = 6,
                        max_queries: int = 5) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    # RSS/blog (prefer explicit blog_url; else website)
    site_for_rss = ensure_http(org.get("blog_url") or org.get("website"))
    if site_for_rss:
        try:
            items += try_rss_feeds(site_for_rss)
        except Exception:
            pass

    # Google CSE for news (respect BASELINE_DISABLE_CSE)
    if (g_api_key and g_cse_id) and str(getenv("BASELINE_DISABLE_CSE", "false")).lower() not in ("1","true","yes","y"):
        queries = build_queries(
            org.get("dba"),
            org.get("website"),
            org.get("owners"),
            domain_root=org.get("domain_root"),
            aka_names=org.get("aka_names"),
            tags=org.get("industry_tags"),
        )
        limit = int(getenv("BASELINE_CSE_MAX_QUERIES", str(max_queries)) or max_queries)
        for q in queries[:limit]:
            try:
                items += google_cse_search(g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=5)
            except Exception:
                continue

    # Clean & window
    items = dedupe(items, key=lambda x: x.get("url"))
    items = [x for x in items if within_days(x.get("published_at", datetime.utcnow()), lookback_days)]
    # Normalize
    for it in items:
        it["title"] = it.get("title") or ""
        it["url"] = it.get("url") or ""
        it["source"] = it.get("source") or (org.get("domain_root") or "")
        if isinstance(it.get("published_at"), datetime):
            it["published_at"] = it["published_at"].strftime("%Y-%m-%d")
    return items[:max_items]

def collect_people_background(org: Dict[str, Any],
                              lookback_days: int,
                              g_api_key: Optional[str],
                              g_cse_id: Optional[str],
                              max_people: int = 3) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    owners = [o for o in (org.get("owners") or []) if o][:max_people]
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
        items = dedupe(items, key=lambda x: x.get("url"))[:5]
        for it in items:
            it["title"] = it.get("title") or ""
            it["url"] = it.get("url") or ""
            it["source"] = it.get("source") or ""
        results.append({"name": person, "findings": items})
    return results

# =====================
# LLM (narrative writer)
# =====================

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
- One short paragraph on stage of funding and most recent fundraise date, who their investors are, number of employees

🚀 Product & Use Cases
- 3–6 bullets: product, core capabilities, typical users, high-level use cases.

📰 Recent Announcements (last ~6 months)
- 1–2 bullets with a date, 1–2 sentence summary, and source name. Prefer company sources; include one credible external if useful.

👥 Your Contacts & Key Team
- 2–4 bullets: each person, role, most relevant prior roles/companies/startups.

(Optional) Risks/Unknowns
- 1–3 bullets for uncertainty, gaps, or identity ambiguities that need confirmation.
"""

def _build_evidence_block(org: Dict[str, Any],
                          news_items: List[Dict[str, Any]],
                          people_bg: List[Dict[str, Any]],
                          funding: Optional[Dict[str, Any]]) -> str:
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
        inner = "\n  ".join([
            f"- {it.get('title','')} — {it.get('source','')} {it.get('url','')}"
            for it in finds
        ])
        ppl_lines.append(f"* {name}:\n  {inner}" if inner else f"* {name}")

    evidence: List[str] = []
    evidence.append("Internal fields:")
    evidence.append(f"- Callsign: {org.get('callsign')}")
    evidence.append(f"- DBA: {org.get('dba')}")
    evidence.append(f"- Website: {org.get('website')}")
    evidence.append(f"- Domain root: {org.get('domain_root')}")
    evidence.append(f"- AKA: {org.get('aka_names')}")
    evidence.append(f"- Contacts: {', '.join(org.get('owners') or [])}")
    evidence.append(f"- Tags: {org.get('industry_tags')}")
    evidence.append("")
    if funding and funding.get("funding_present"):
        evidence.append("Funding snapshot (pre-normalized):")
        if funding.get("total_funding_usd") or funding.get("last_round_amount_usd"):
            evidence.append(f"- Total funding: {funding.get('total_funding_usd')}")
            evidence.append(f"- Last round: {funding.get('last_round_type')} on {funding.get('last_round_date')}, amount={funding.get('last_round_amount_usd')}")
        if funding.get("investors"):
            evidence.append(f"- Investors: {', '.join(funding['investors'])}")
        if funding.get("funding_sources"):
            for s in funding["funding_sources"][:3]:
                evidence.append(f"- Source: {s}")
        evidence.append("")
    evidence.append("Recent items (last 6 months):")
    evidence.append("\n".join(news_lines) if news_lines else "(none)")
    evidence.append("")
    evidence.append("People background findings:")
    evidence.append("\n".join(ppl_lines) if ppl_lines else "(none)")
    return "\n".join(evidence)

def _openai_write_narrative(prompt: str) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        model = (
            os.getenv("OPENAI_CHAT_MODEL_DOSSIER")
            or os.getenv("OPENAI_CHAT_MODEL")
            or "gpt-5-mini"
        ).strip()
        temp_env = (
            os.getenv("OPENAI_TEMPERATURE_DOSSIER")
            or os.getenv("OPENAI_TEMPERATURE")
            or ""
        ).strip()
        temperature = None if temp_env in ("", "auto", "none") else float(temp_env)

        def try_call(send_temperature: bool) -> str:
            if model.startswith("gpt-5"):
                kwargs: Dict[str, Any] = {"model": model, "input": prompt}
                if send_temperature and temperature is not None:
                    kwargs["temperature"] = temperature
                r = client.responses.create(**kwargs)
                return (r.output_text or "").strip()
            else:
                kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}]}
                if send_temperature and temperature is not None:
                    kwargs["temperature"] = temperature
                r = client.chat.completions.create(**kwargs)
                return (r.choices[0].message.content or "").strip()

        try:
            return try_call(send_temperature=True)
        except Exception as e1:
            if "temperature" in repr(e1).lower() or "unrecognized request argument" in repr(e1).lower():
                return try_call(send_temperature=False)
            raise
    except Exception:
        return None

def generate_narrative(org: Dict[str, Any],
                       news_items: List[Dict[str, Any]],
                       people_bg: List[Dict[str, Any]],
                       funding: Optional[Dict[str, Any]]) -> str:
    evidence = _build_evidence_block(org, news_items, people_bg, funding)
    prompt = f"{DOSSIER_GUIDANCE}\n\nEVIDENCE START\n{evidence}\nEVIDENCE END\n\nWrite the profile now."
    text = _openai_write_narrative(prompt)
    if text:
        return text

    # Fallback if no OpenAI lib/key: compact evidence-based
    lines: List[str] = []
    lines.append("🔍 Company & Identity")
    lines.append(f"- DBA: {org.get('dba') or org.get('domain_root') or org.get('callsign')}")
    lines.append(f"- Website: {org.get('website') or '—'}\n")
    lines.append("🏢 Company Overview")
    if funding and funding.get("funding_present"):
        lines.append(f"- Last round: {funding.get('last_round_type')} on {funding.get('last_round_date')} amount={funding.get('last_round_amount_usd')}")
        if funding.get("investors"):
            lines.append(f"- Investors: {', '.join(funding.get('investors') or [])}")
    else:
        lines.append("- (LLM not available) See recent items and people notes below.\n")
    lines.append("🚀 Product & Use Cases")
    lines.append("- (summarize after LLM is enabled)\n")
    lines.append("📰 Recent Announcements (last ~6 months)")
    if news_items:
        for n in news_items[:4]:
            date = n.get("date") or n.get("published_at", "")
            src  = n.get("source", "")
            lines.append(f"- {date} — {n.get('title','')} — {src} {n.get('url','')}")
    else:
        lines.append("- None found")
    lines.append("\n👥 Your Contacts & Key Team")
    if people_bg:
        for p in people_bg:
            lines.append(f"- {p.get('name')}")
    else:
        lines.append("- (no background findings)")
    return "\n".join(lines)

# =========================
# Notion: write dossier body
# =========================

def _notion_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": os.getenv("NOTION_VERSION", "2022-06-28"),
        "Content-Type": "application/json",
    }

def append_dossier_blocks(page_id: str, markdown_body: str) -> None:
    """
    Minimal block append: Heading + paragraphs (chunked) to avoid size limits.
    """
    chunks = [markdown_body[i:i+1800] for i in range(0, len(markdown_body), 1800)] or [markdown_body]
    hdr = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Dossier"}}]},
    }
    paras = [{
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": ch}}]},
    } for ch in chunks]
    r = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=_notion_headers(),
        json={"children": [hdr] + paras},
        timeout=30,
    )
    r.raise_for_status()

def push_dossier_to_notion(callsign: str, org: Dict[str, Any], markdown_body: str, throttle_sec: float = 0.35) -> None:
    token = os.getenv("NOTION_API_KEY")
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    if not (token and companies_db):
        return

    # Prefer unified 'domain' key, fallback to legacy 'domain_root'
    domain_root = (org.get("domain") or org.get("domain_root") or "").strip() or None

    # If Website empty but we have a domain, synthesize an https URL (helps Notion URL props)
    website = (org.get("website") or "").strip() or None
    if not website and domain_root:
        website = ensure_http(domain_root)

    payload = {
        "callsign": callsign,
        "company":  (org.get("dba") or "").strip(),
        "website":  website,
        "domain":   domain_root,     # bare root, e.g., "example.com"
        "owners":   org.get("owners") or [],
        "needs_dossier": False,
    }

    page_id = upsert_company_page(companies_db, payload)

    try:
        append_dossier_blocks(page_id, markdown_body)
    except Exception as e:
        print("[Notion] append blocks warning:", repr(e))

    try:
        set_needs_dossier(page_id, False)
    except Exception:
        pass

    if throttle_sec and throttle_sec > 0:
        time.sleep(throttle_sec)

# ===============
# Batching helpers
# ===============

def slice_batch(keys: List[str], batch_size: Optional[int], batch_index: Optional[int]) -> List[str]:
    if not keys:
        return []
    if not batch_size:
        return keys
    n = max(1, int(batch_size))
    i = max(0, int(batch_index or 0))
    start = i * n
    end = start + n
    return keys[start:end]

# =====
# Main
# =====

def main() -> None:
    # Gmail service (for CSVs and optional email)
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = getenv("GMAIL_USER") or os.environ["GMAIL_USER"]

    # Load profile CSV (preferred) and weekly CSV (fallback merge)
    profile_subject = getenv("PROFILE_SUBJECT") or getenv("NEWS_PROFILE_SUBJECT")
    weekly_query    = getenv("WEEKLY_GMAIL_QUERY") or getenv("NEWS_GMAIL_QUERY")
    attach_rx       = getenv("ATTACHMENT_REGEX", r".*\.csv$")

    df_profile = fetch_csv_by_subject(svc, user, profile_subject) if profile_subject else None
    weekly     = load_latest_weekly_csv(svc, user, weekly_query, attach_rx) if weekly_query else None

    if df_profile is None and weekly is None:
        raise SystemExit("Need at least one CSV (profile or weekly) to build a baseline.")

    # Build profile roster
    prof: Dict[str, Dict[str, Any]] = {}
    if df_profile is not None:
        pcols = lower_cols(df_profile)
        for _, r in df_profile.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower() if pcols.get("callsign") in r else ""
            if not cs:
                continue
            dba = r[pcols.get("dba")] if pcols.get("dba") in r else None
            website = r[pcols.get("website")] if pcols.get("website") in r else None
            owners_raw = r[pcols.get("beneficial_owners")] if pcols.get("beneficial_owners") in r else ""
            owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]

            base: Dict[str, Any] = {
                "callsign": r[pcols.get("callsign")],
                "dba": dba,
                "website": website,
                "aka_names": r[pcols.get("aka_names")] if pcols.get("aka_names") in r else None,
                "blog_url": r[pcols.get("blog_url")] if pcols.get("blog_url") in r else None,
                "rss_feeds": r[pcols.get("rss_feeds")] if pcols.get("rss_feeds") in r else None,
                "linkedin_url": r[pcols.get("linkedin_url")] if pcols.get("linkedin_url") in r else None,
                "twitter_handle": r[pcols.get("twitter_handle")] if pcols.get("twitter_handle") in r else None,
                "crunchbase_url": r[pcols.get("crunchbase_url")] if pcols.get("crunchbase_url") in r else None,
                "industry_tags": r[pcols.get("industry_tags")] if pcols.get("industry_tags") in r else None,
                "hq_city": r[pcols.get("hq_city")] if pcols.get("hq_city") in r else None,
                "hq_region": r[pcols.get("hq_region")] if pcols.get("hq_region") in r else None,
                "hq_country": r[pcols.get("hq_country")] if pcols.get("hq_country") in r else None,
                "owners": owners,
            }

            # Domain from profile CSV (or derive from website)
            domain_from_profile = extract_domain_from_row(r, pcols)
            base["domain_root"] = domain_from_profile or compute_domain_root(base.get("website"))
            base["domain"] = base.get("domain") or base.get("domain_root")

            prof[cs] = base

    # Merge fallback data from weekly CSV
    if weekly is not None:
        wcols = lower_cols(weekly)
        for _, r in weekly.iterrows():
            cs = str(r[wcols.get("callsign")]).strip().lower() if wcols.get("callsign") in r else ""
            if not cs:
                continue
            base = prof.get(cs, {"callsign": r[wcols.get("callsign")], "owners": []})

            # Fill DBA/website if missing
            if not base.get("dba") and wcols.get("dba") in r:
                base["dba"] = r[wcols.get("dba")]
            if not base.get("website") and wcols.get("website") in r:
                base["website"] = r[wcols.get("website")]

            # Merge owners
            if wcols.get("beneficial_owners") in r:
                owners_raw = r[wcols.get("beneficial_owners")]
                owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]
                if owners:
                    base["owners"] = sorted(set((base.get("owners") or []) + owners))

            # Domain from weekly CSV (or derive from website), but don't overwrite an existing profile domain
            domain_from_weekly = extract_domain_from_row(r, wcols)
            if not base.get("domain_root"):
                base["domain_root"] = domain_from_weekly or compute_domain_root(base.get("website"))
            if not base.get("domain") and base.get("domain_root"):
                base["domain"] = base["domain_root"]

            prof[cs] = base

    # Build list of callsigns and apply optional filter (BASELINE_CALLSIGNS)
    base_keys = sorted(prof.keys())
    requested = (getenv("BASELINE_CALLSIGNS") or "").strip()
    if requested and requested.upper() != "ALL":
        want = [c.strip().lower() for c in requested.split(",") if c.strip()]
        base_keys = [c for c in base_keys if c in want]

    # Batching
    batch_size = int(getenv("BATCH_SIZE", "0") or "0") or None
    batch_index = int(getenv("BATCH_INDEX", "0") or "0") if batch_size else None
    targets_keys = slice_batch(base_keys, batch_size, batch_index)

    print(
        f"Roster total: {len(base_keys)} | This batch: {len(targets_keys)} "
        f"(batch_size={batch_size or '∞'}, batch_index={batch_index if batch_size else '-'})"
    )
    if targets_keys:
        head = targets_keys[:5]
        remainder = max(0, len(targets_keys) - len(head))
        print("Batch head (first 5 callsigns):", ", ".join(head) + (f" … (+{remainder} more)" if remainder else ""))
    else:
        print("Batch head: (empty)")

    if not targets_keys:
        print("No callsigns in this batch; nothing to do.")
        return

    # Knobs
    lookback_days = int(getenv("BASELINE_LOOKBACK_DAYS", "180") or "180")
    g_api_key = getenv("GOOGLE_API_KEY")
    g_cse_id  = getenv("GOOGLE_CSE_ID")
    llm_delay = float(getenv("LLM_DELAY_SEC", "0") or "0")
    notion_delay = float(getenv("NOTION_THROTTLE_SEC", "0.35") or "0.35")

    # Build concrete target dicts
    targets: List[Dict[str, Any]] = [prof[k] for k in targets_keys]

    dossiers: List[Dict[str, Any]] = []

    for org in targets:
        # Domain resolution based on strategy
        dr, url = resolve_domain_for_org(org, g_api_key, g_cse_id)
        if dr:
            org["domain"] = dr
            org["domain_root"] = org.get("domain_root") or dr
            if not (org.get("website") or "").strip():
                org["website"] = url

        # Collect intel
        news_items = collect_recent_news(org, lookback_days, g_api_key, g_cse_id)
        people_bg  = collect_people_background(org, lookback_days, g_api_key, g_cse_id)

        # If you want to feed content pages to funding parser, you can pass [] for now
        funding = best_funding(org, fetched_pages=[])

        # Narrative
        narr = generate_narrative(org, news_items, people_bg, funding)

        # Notion push
        try:
            logd(f"[NOTION] upsert payload: callsign={org.get('callsign')} company={org.get('dba')} domain={org.get('domain')} website={org.get('website')}")
            push_dossier_to_notion((org.get("callsign") or "").strip(), org, narr, throttle_sec=notion_delay)
        except Exception as e:
            print("Notion dossier push error:", repr(e))

        dossiers.append({"callsign": org.get("callsign"), "body_md": narr})

        if llm_delay:
            time.sleep(llm_delay)

    # Preview or optional email
    preview = getenv("PREVIEW_ONLY", "false").lower() in ("1", "true", "yes", "y")
    if preview:
        for d in dossiers:
            print(f"\n=== {d.get('callsign')} ===\n")
            print(d["body_md"][:2500])
            print("\n----------------------------")
        return

    if getenv("SEND_EMAIL", "false").lower() in ("1", "true", "yes", "y"):
        body = ["<html><body><h2>Baselines</h2>"]
        for d in dossiers:
            body.append(f"<h3>{d.get('callsign')}</h3><pre style='white-space:pre-wrap'>{d['body_md']}</pre><hr/>")
        body.append("</body></html>")
        html = "\n".join(body)
        to = getenv("DIGEST_TO") or getenv("GMAIL_USER") or ""
        send_html_email(
            build_service(
                client_id=os.environ["GMAIL_CLIENT_ID"],
                client_secret=os.environ["GMAIL_CLIENT_SECRET"],
                refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
            ),
            getenv("GMAIL_USER") or "",
            to,
            f"Baselines — {datetime.utcnow().date()}",
            html,
        )
        print("Baselines emailed to", to)

if __name__ == "__main__":
    main()
