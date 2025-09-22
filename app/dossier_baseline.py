# app/dossier_baseline.py
from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import tldextract

from app.data.csv_parser import filter_dataframe_by_relationship_manager
from app.gmail_client import (
    build_service,
    extract_csv_attachments,
    get_message,
    search_messages,
    send_html_email,
)
from app.news_job import (
    build_queries,
    dedupe,
    fetch_csv_by_subject,
    google_cse_search,
    try_rss_feeds,
    within_days,
)
from app.notion_client import (
    get_companies_needing_dossiers,
    page_has_dossier,
    replace_dossier_blocks,
    set_needs_dossier,
    upsert_company_page,
)
from app.performance_utils import (
    DEFAULT_RATE_LIMITER,
    PERFORMANCE_MONITOR,
    ParallelProcessor,
    should_skip_processing,
)

# Import probe_funding functionality
from scripts.probe_funding import probe_funding

# ---------- Debug ----------

DEBUG = os.getenv("BASELINE_DEBUG", "").lower() in ("1", "true", "yes")


def logd(msg: str):
    if DEBUG:
        print(msg)


# ---------- Normalization (date, source, url, title) ----------


def _source_from_url(url: str | None) -> str:
    if not url:
        return ""
    ext = tldextract.extract(url)
    return (
        ext.registered_domain
        or (f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else "")
        or ""
    )


def _iso_date(dt_or_str) -> str:
    if not dt_or_str:
        return ""
    if isinstance(dt_or_str, datetime):
        return dt_or_str.strftime("%Y-%m-%d")
    s = str(dt_or_str).strip()
    try:
        s2 = s.replace("/", "-").replace(".", "-")
        parts = [int(x) for x in s2.split("-") if x.isdigit()]
        if len(parts) >= 3:
            y, m, d = parts[:3]
            return datetime(y, m, d).strftime("%Y-%m-%d")
    except Exception:
        pass
    return s


def normalize_news_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        src = (it.get("source") or "").strip() or _source_from_url(url)
        date = it.get("date") or it.get("published_at")
        out.append(
            {
                "url": url,
                "title": title,
                "source": src,
                "published_at": _iso_date(date),
            }
        )
    return out


# ---------- Domain helpers ----------


def ensure_http(u: str | None) -> str | None:
    if not u:
        return None
    s = u.strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s


def compute_domain_root(website: str | None) -> str | None:
    if not website:
        return None
    w = website.strip().lower()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    host = w.split("/")[0]
    ext = tldextract.extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return host or None


_BLOCKED_SITES = {
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


def _website_is_accessible(url: str) -> bool:
    """More permissive check - only fail on clear errors like 404."""
    try:
        r = requests.head(url, timeout=6, allow_redirects=True)
        # Allow anything except clear client errors (4xx except 403 which can be anti-bot)
        return r.status_code < 400 or r.status_code == 403
    except requests.RequestException:
        # Network errors, timeouts etc. - assume the website is valid
        return True
    except Exception:
        # Any other error - assume the website is valid
        return True


def validate_domain_to_url(domain_root: str) -> str | None:
    candidates = [
        f"https://{domain_root}",
        f"https://www.{domain_root}",
        f"http://{domain_root}",
    ]
    for u in candidates:
        if _website_is_accessible(u):
            return u
    return candidates[0]


def discover_domain_by_search(
    name: str, g_api_key: Optional[str], g_cse_id: Optional[str]
) -> Optional[str]:
    if not (g_api_key and g_cse_id and name):
        return None
    try:
        q = f"{name} (official site OR homepage) -site:linkedin.com -site:twitter.com -site:x.com"
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


def _is_blank(x) -> bool:
    if x is None:
        return True
    s = str(x).strip().lower()
    return s in ("", "none", "nan")


def _norm(x: Any) -> Optional[str]:
    """Normalize CSV cell to a clean string or None."""
    return None if _is_blank(x) else str(x).strip()


def resolve_domain_for_org(
    org: dict, g_api_key: Optional[str], g_cse_id: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """
    Final authority order:
      1) CSV `domain_root` (trusted). Build URL variants and accept 200–399/403/405.
      2) CSV `website` (trusted). Derive domain_root from it.
      3) Search (domain_resolver). Only if BOTH CSV fields are empty.
    Returns: (domain_root, homepage_url) – either or both may be None.
    """
    csv_domain_root = _norm(org.get("domain_root"))
    csv_website = _norm(org.get("website"))

    # 1) CSV domain_root is gospel
    if csv_domain_root:
        for u in (
            f"https://{csv_domain_root}",
            f"https://www.{csv_domain_root}",
            f"http://{csv_domain_root}",
        ):
            if _website_is_accessible(u):
                logd(f"[DOMAIN] Trust CSV domain_root -> {csv_domain_root} ({u})")
                return csv_domain_root, u
        return csv_domain_root, f"https://{csv_domain_root}"

    # 2) CSV website is trusted
    if csv_website:
        url = ensure_http(csv_website) or csv_website
        dom = compute_domain_root(url) or None
        logd(f"[DOMAIN] Trust CSV website -> {url} (root={dom})")
        return dom, url

    # 3) Only then search
    company_name = (org.get("dba") or org.get("callsign") or "").strip()
    if not company_name or not (g_api_key and g_cse_id):
        return None, None

    try:
        from scripts.domain_resolver import resolve_domain

        owners_csv = ",".join(org.get("owners") or [])
        result = resolve_domain(company_name, owners_csv, g_api_key, g_cse_id, debug=DEBUG) or {}
        dr = (result.get("domain_root") or "").strip() or None
        url = (result.get("homepage_url") or "").strip() or None
        if dr and url:
            logd(f"[DOMAIN] Search accepted -> {dr} ({url}) why={result.get('why')}")
            return dr, url
    except Exception as e:
        logd(f"[DOMAIN] Search error: {e}")

    # Fallback: minimal CSE
    cand = discover_domain_by_search(company_name, g_api_key, g_cse_id)
    if cand:
        return cand, validate_domain_to_url(cand)

    return None, None


# ---------- Env helpers ----------


def getenv(n: str, d: Optional[str] = None) -> Optional[str]:
    v = os.getenv(n)
    return d if v in (None, "") else v


def load_latest_weekly_csv(service, user, q, attachment_regex):
    if not q:
        return None
    relationship_manager = os.getenv("RELATIONSHIP_MANAGER_NAME", "Will Mitchell")
    msgs = search_messages(service, user, q, max_results=5)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if atts:
            _, data = atts[0]
            try:
                df = pd.read_csv(io.BytesIO(data))
                df = filter_dataframe_by_relationship_manager(df, relationship_manager)
                if df.empty:
                    continue
                return df
            except Exception:
                pass
    return None


def lower_cols(df: pd.DataFrame) -> Dict[str, str]:
    return {c.lower().strip(): c for c in df.columns}


# ---------- Evidence collection ----------


def collect_recent_news(
    org: Dict[str, Any],
    lookback_days: int,
    g_api_key: Optional[str],
    g_cse_id: Optional[str],
    max_items: int = 6,
    max_queries: int = 5,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    site_for_rss = ensure_http(org.get("blog_url") or org.get("website"))
    if site_for_rss:
        try:
            items += try_rss_feeds(site_for_rss)
        except Exception:
            pass
    if (g_api_key and g_cse_id) and str(getenv("BASELINE_DISABLE_CSE", "false")).lower() not in (
        "1",
        "true",
        "yes",
        "y",
    ):
        queries = build_queries(
            org.get("callsign") or org.get("dba") or "",
            org.get("dba"),
            org.get("website"),
            org.get("owners"),
            domain_root=org.get("domain_root") or org.get("domain"),
            aka_names=org.get("aka_names"),
            tags=org.get("industry_tags"),
            blog_url=org.get("blog_url"),
        )
        limit = int(getenv("BASELINE_CSE_MAX_QUERIES", str(max_queries)) or max_queries)
        for q in queries[:limit]:
            try:
                items += google_cse_search(
                    g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=5
                )
            except Exception:
                continue
    items = dedupe(items, key=lambda x: x.get("url"))
    items = [
        x
        for x in items
        if within_days(x.get("published_at", datetime.now(timezone.utc)), lookback_days)
    ]
    items = normalize_news_items(items)
    return items[:max_items]


def collect_people_background(
    org: Dict[str, Any],
    lookback_days: int,
    g_api_key: Optional[str],
    g_cse_id: Optional[str],
    max_people: int = 3,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    owners = [o for o in (org.get("owners") or []) if o][:max_people]
    if not (g_api_key and g_cse_id) or not owners:
        return results
    for person in owners:
        org_label = (org.get("dba") or org.get("domain_root") or "").strip()
        org_clause = f' "{org_label}"' if org_label else ""
        qs = [
            f'"{person}"{org_clause} (founder OR cofounder OR CFO OR COO OR CTO OR CEO OR head)',
            f'"{person}" (LinkedIn OR Crunchbase OR AngelList OR PitchBook)',
            f'"{person}" (previous OR formerly OR ex-)',
        ]
        items: List[Dict[str, Any]] = []
        for q in qs:
            try:
                items += google_cse_search(
                    g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=3
                )
            except Exception:
                continue
        items = dedupe(items, key=lambda x: x.get("url"))[:5]
        for it in items:
            it["title"] = it.get("title") or ""
            it["url"] = it.get("url") or ""
            it["source"] = it.get("source") or _source_from_url(it.get("url"))
        results.append({"name": person, "findings": items})
    return results


# ---------- Funding data collection ----------


def collect_funding_data(org: Dict[str, Any], lookback_days: int = 540) -> Dict[str, Any]:
    """
    Collect funding information using the probe_funding functionality.
    Returns structured funding data or empty dict if collection fails.
    """
    try:
        name = org.get("dba") or org.get("callsign") or ""
        domain = org.get("domain_root") or org.get("domain")
        owners = org.get("owners") or []

        if not name:
            logd("[FUNDING] No company name available, skipping funding collection")
            return {}

        logd(f"[FUNDING] Collecting funding data for {name}, domain={domain}, owners={owners}")

        # Use probe_funding with reduced result count to keep it fast
        result = probe_funding(
            name=name,
            domain=domain,
            owners=owners,
            aka=None,
            lookback_days=lookback_days,
            max_results=3,  # Keep it concise
            fetch_pages=False,  # Skip page fetching for speed
        )

        best_guess = result.get("best_guess")
        crunchbase_hint = result.get("crunchbase_hint", {})

        funding_data = {}

        # Extract key funding information
        if best_guess:
            facts = best_guess.get("facts", {})
            funding_data.update(
                {
                    "latest_funding_source": best_guess.get("source", ""),
                    "latest_funding_url": best_guess.get("url", ""),
                    "latest_funding_date": best_guess.get("published_at")
                    or facts.get("announced_on", ""),
                    "latest_funding_title": best_guess.get("title", ""),
                    "score": best_guess.get("score", 0.0),
                }
            )

            if "amount_usd" in facts:
                funding_data["latest_amount_usd"] = facts["amount_usd"]
            if "round_type" in facts:
                funding_data["latest_round_type"] = facts["round_type"]
            if "investors" in facts and facts["investors"]:
                funding_data["latest_investors"] = facts["investors"][:5]  # Limit to top 5

        # Add Crunchbase data if available
        if crunchbase_hint:
            funding_data.update(
                {
                    "total_funding_usd": crunchbase_hint.get("total_funding_usd"),
                    "cb_last_round_type": crunchbase_hint.get("last_round_type"),
                    "cb_last_round_date": crunchbase_hint.get("last_round_date"),
                    "cb_last_round_amount_usd": crunchbase_hint.get("last_round_amount_usd"),
                    "cb_investors": crunchbase_hint.get("investors", [])[:5],  # Limit to top 5
                }
            )

        # Clean up empty values
        funding_data = {k: v for k, v in funding_data.items() if v not in (None, "", [], 0)}

        if funding_data:
            logd(f"[FUNDING] Found funding data: {funding_data}")
        else:
            logd("[FUNDING] No funding data found")

        return funding_data

    except Exception as e:
        logd(f"[FUNDING] Error collecting funding data: {e}")
        return {}


# ---------- LLM: dossier narrative ----------

DOSSIER_GUIDANCE = """
You are an account manager at a banking technology company serving startups (often growth-stage, 5–100 employees).
Produce a concise, executive-style client profile based ONLY on the evidence provided below and the internal fields.
Do not invent facts; if identity or facts are uncertain, say so briefly.

Process to follow:
1) Cross-reference the DBA/company name, website, and the named contacts to decide which business we're examining.
   Some companies change names or websites—do not treat either as gospel; resolve the most likely identity.
2) Pull key company info: what they do, products/services and use cases, any publicly mentioned key customers.
3) Find recent announcements (last 6 months): launches, partnerships, product releases. Summarize the 1–2 most relevant.
4) Add short background notes on key people (the provided contacts and any clearly relevant leaders): prior roles/companies/startups.

Output format: Markdown. Keep it tight (~400–600 words), crisp, factual, and skimmable. Avoid hype.
Use the following sections and headings exactly:

🔍 Company & Identity
- Who we're talking about; how you resolved the identity (DBA vs brand vs domain); HQ if available.

🏢 Company Overview
- One short paragraph on company stage, business model, and number of employees (avoid speculation about funding/investment details)

🚀 Product & Use Cases
- 3–6 bullets: product, core capabilities, typical users, high-level use cases.

📰 Recent Announcements (last ~6 months)
- 1–2 bullets with a date, 1–2 sentence summary, and source name. Prefer company sources; include one credible external if useful.

👥 Your Contacts & Key Team
- 2–4 bullets: each person, role, most relevant prior roles/companies/startups.

(Optional) Risks/Unknowns
- 1–3 bullets for uncertainty, gaps, or identity ambiguities that need confirmation.
"""


def _build_evidence_block(
    org: dict, news_items: List[dict], people_bg: List[dict], funding_data: dict = None
) -> str:
    news_lines = []
    for n in news_items[:8]:
        date = n.get("published_at", "")
        src = n.get("source", "")
        title = n.get("title", "")
        url = n.get("url", "")
        news_lines.append(f"- {date} — {title} — {src} {url}")

    ppl_lines = []
    for p in people_bg:
        name = p.get("name") or ""
        finds = p.get("findings", [])[:4]
        inner = "\n  ".join(
            [f"- {it.get('title','')} — {it.get('source','')} {it.get('url','')}" for it in finds]
        )
        ppl_lines.append(f"* {name}:\n  {inner}" if inner else f"* {name}")

    evidence = []
    evidence.append("Internal fields:")
    evidence.append(f"- Callsign: {org.get('callsign')}")
    evidence.append(f"- DBA: {org.get('dba')}")
    evidence.append(f"- Website: {org.get('website')}")
    evidence.append(f"- Domain root: {org.get('domain_root') or org.get('domain')}")
    evidence.append(f"- AKA: {org.get('aka_names')}")
    evidence.append(f"- Contacts: {', '.join(org.get('owners') or [])}")
    evidence.append(f"- Tags: {org.get('industry_tags')}")
    evidence.append("")
    evidence.append("Recent items (last 6 months):")
    evidence.append("\n".join(news_lines) if news_lines else "(none)")
    evidence.append("")
    evidence.append("People background findings:")
    evidence.append("\n".join(ppl_lines) if ppl_lines else "(none)")

    # Add funding information if available
    if funding_data:
        evidence.append("")
        evidence.append("Funding information:")
        if funding_data.get("latest_amount_usd"):
            amount = f"${funding_data['latest_amount_usd']:,}"
            round_type = funding_data.get("latest_round_type", "")
            date = funding_data.get("latest_funding_date", "")
            evidence.append(f"- Latest round: {amount} {round_type} ({date})")

        if funding_data.get("latest_investors"):
            investors = ", ".join(funding_data["latest_investors"])
            evidence.append(f"- Recent investors: {investors}")

        if funding_data.get("total_funding_usd"):
            total = f"${funding_data['total_funding_usd']:,}"
            evidence.append(f"- Total funding (CB): {total}")

        if funding_data.get("cb_investors"):
            cb_investors = ", ".join(funding_data["cb_investors"])
            evidence.append(f"- All investors (CB): {cb_investors}")

        if funding_data.get("latest_funding_url"):
            evidence.append(
                f"- Source: {funding_data.get('latest_funding_title', '')} — {funding_data.get('latest_funding_source', '')} {funding_data['latest_funding_url']}"
            )

    return "\n".join(evidence)


def _openai_write_narrative(prompt: str) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai

        client = openai.OpenAI(api_key=api_key)
        model = (
            os.getenv("OPENAI_CHAT_MODEL_DOSSIER") or os.getenv("OPENAI_CHAT_MODEL") or "gpt-5-mini"
        ).strip()
        temp_env = (
            os.getenv("OPENAI_TEMPERATURE_DOSSIER") or os.getenv("OPENAI_TEMPERATURE") or ""
        ).strip()
        temperature = None if temp_env in ("", "auto", "none") else float(temp_env)

        def try_call(send_temperature: bool):
            if model.startswith("gpt-5"):
                kwargs = {"model": model, "input": prompt}
                if send_temperature and temperature is not None:
                    kwargs["temperature"] = temperature
                r = client.responses.create(**kwargs)
                return r.output_text
            else:
                kwargs = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if send_temperature and temperature is not None:
                    kwargs["temperature"] = temperature
                r = client.chat.completions.create(**kwargs)
                return r.choices[0].message.content

        try:
            out = try_call(send_temperature=True)
            return (out or "").strip()
        except Exception:
            out = try_call(send_temperature=False)
            return (out or "").strip()
    except Exception:
        return None


def generate_narrative(
    org: dict, news_items: List[dict], people_bg: List[dict], funding_data: dict = None
) -> str:
    evidence = _build_evidence_block(org, news_items, people_bg, funding_data)
    prompt = (
        f"{DOSSIER_GUIDANCE}\n\nEVIDENCE START\n{evidence}\nEVIDENCE END\n\nWrite the profile now."
    )
    text = _openai_write_narrative(prompt)
    if text:
        return text

    # Fallback
    lines = []
    lines.append("🔍 Company & Identity")
    lines.append(f"- DBA: {org.get('dba') or org.get('domain_root') or org.get('callsign')}")
    lines.append(f"- Website: {org.get('website') or '—'}\n")
    lines.append("🏢 Company Overview")
    lines.append("- (LLM not available) See recent items and people notes below.\n")
    lines.append("🚀 Product & Use Cases")
    lines.append("- (summarize after LLM is enabled)\n")
    lines.append("📰 Recent Announcements (last ~6 months)")
    if news_items:
        for n in news_items[:4]:
            date = n.get("published_at", "")
            src = n.get("source", "")
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


def push_dossier_to_notion(
    callsign: str, org: dict, markdown_body: str, throttle_sec: float = 0.35
):
    token = os.getenv("NOTION_API_KEY")
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    if not (token and companies_db):
        return

    domain_root = (org.get("domain") or org.get("domain_root") or "").strip() or None
    website = (org.get("website") or "").strip() or None
    if not website and domain_root:
        website = ensure_http(domain_root)

    payload = {
        "callsign": callsign,
        "company": (org.get("dba") or "").strip(),
        "website": website,
        "domain": domain_root,
        "owners": org.get("owners") or [],
        "needs_dossier": False,
    }

    page_id = upsert_company_page(companies_db, payload)
    try:
        replace_dossier_blocks(page_id, markdown_body)
    except Exception as e:
        print("[Notion] dossier write warning:", repr(e))
    try:
        set_needs_dossier(page_id, False)
    except Exception:
        pass
    if throttle_sec and throttle_sec > 0:
        time.sleep(throttle_sec)


# ---------- Batching ----------


def slice_batch(
    keys: List[str], batch_size: Optional[int], batch_index: Optional[int]
) -> List[str]:
    if not keys:
        return []
    if not batch_size:
        return keys
    n = max(1, int(batch_size))
    i = max(0, int(batch_index or 0))
    start = i * n
    end = start + n
    return keys[start:end]


# ---------- Main ----------


def main():
    """
    Generate baseline dossiers for companies.

    Two modes:
    1. Notion Mode (BASELINE_USE_NOTION_FLAGS=true): Query Notion for companies with 'Needs Dossier' = true
    2. CSV Mode (default): Use CSV data filtered by BASELINE_CALLSIGNS

    Notion mode is preferred as it doesn't rely on the unreliable CSV is_new_account flag.
    """
    # Gmail
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = getenv("GMAIL_USER") or os.environ["GMAIL_USER"]

    profile_subject = getenv("PROFILE_SUBJECT") or getenv("NEWS_PROFILE_SUBJECT")
    weekly_query = getenv("WEEKLY_GMAIL_QUERY") or getenv("NEWS_GMAIL_QUERY")
    attach_rx = getenv("ATTACHMENT_REGEX", r".*\.csv$")

    df_profile = fetch_csv_by_subject(svc, user, profile_subject) if profile_subject else None
    weekly = load_latest_weekly_csv(svc, user, weekly_query, attach_rx) if weekly_query else None
    if df_profile is None and weekly is None:
        raise SystemExit("Need at least one CSV (profile or weekly) to build a baseline.")

    prof: Dict[str, Dict[str, Any]] = {}
    if df_profile is not None:
        pcols = lower_cols(df_profile)

        for _, r in df_profile.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower() if pcols.get("callsign") in r else ""
            if not cs:
                continue
            owners_raw = (
                r[pcols.get("beneficial_owners")] if pcols.get("beneficial_owners") in r else ""
            )
            owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]

            # BEFORE building base
            dom_val = None
            web_val = None

            # accept domain_root OR domain
            p_dom_col = pcols.get("domain_root") or pcols.get("domain")
            if p_dom_col in r:
                dom_val = _norm(r[p_dom_col])

            # accept website/homepage/site/url
            for cand in ("website", "homepage", "site", "url"):
                c = pcols.get(cand)
                if c in r and _norm(r[c]):
                    web_val = _norm(r[c])
                    break

            base = {
                "callsign": r[pcols.get("callsign")],
                "dba": _norm(r[pcols.get("dba")]) if pcols.get("dba") in r else None,
                "website": web_val,
                "domain_root": dom_val,
                "aka_names": (
                    _norm(r[pcols.get("aka_names")]) if pcols.get("aka_names") in r else None
                ),
                "blog_url": (
                    _norm(r[pcols.get("blog_url")]) if pcols.get("blog_url") in r else None
                ),
                "rss_feeds": (
                    _norm(r[pcols.get("rss_feeds")]) if pcols.get("rss_feeds") in r else None
                ),
                "linkedin_url": (
                    _norm(r[pcols.get("linkedin_url")]) if pcols.get("linkedin_url") in r else None
                ),
                "twitter_handle": (
                    _norm(r[pcols.get("twitter_handle")])
                    if pcols.get("twitter_handle") in r
                    else None
                ),
                "crunchbase_url": (
                    _norm(r[pcols.get("crunchbase_url")])
                    if pcols.get("crunchbase_url") in r
                    else None
                ),
                "industry_tags": (
                    _norm(r[pcols.get("industry_tags")])
                    if pcols.get("industry_tags") in r
                    else None
                ),
                "hq_city": (_norm(r[pcols.get("hq_city")]) if pcols.get("hq_city") in r else None),
                "hq_region": (
                    _norm(r[pcols.get("hq_region")]) if pcols.get("hq_region") in r else None
                ),
                "hq_country": (
                    _norm(r[pcols.get("hq_country")]) if pcols.get("hq_country") in r else None
                ),
                "owners": owners,
            }

            # If no CSV domain_root but we do have website, derive it
            if not base.get("domain_root"):
                base["domain_root"] = compute_domain_root(base.get("website"))
            prof[cs] = base

    if weekly is not None:
        wcols = lower_cols(weekly)
        for _, r in weekly.iterrows():
            cs = str(r[wcols.get("callsign")]).strip().lower() if wcols.get("callsign") in r else ""
            if not cs:
                continue

            base = prof.get(cs, {"callsign": r[wcols.get("callsign")], "owners": []})

            # dba
            if (not base.get("dba")) and wcols.get("dba") in r:
                base["dba"] = _norm(r[wcols.get("dba")])

            # website (first non-blank among common headings)
            if not base.get("website"):
                for cand in ("website", "homepage", "site", "url"):
                    c = wcols.get(cand)
                    if c in r and _norm(r[c]):
                        base["website"] = _norm(r[c])
                        break

            # beneficial owners
            if wcols.get("beneficial_owners") in r:
                owners_raw = r[wcols.get("beneficial_owners")]
                owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]
                if owners:
                    base["owners"] = sorted(set((base.get("owners") or []) + owners))

            # domain_root / domain — TRUST CSV, but fill from weekly if missing
            if not base.get("domain_root"):
                w_dom_col = wcols.get("domain_root") or wcols.get("domain")
                if w_dom_col in r and _norm(r[w_dom_col]):
                    base["domain_root"] = _norm(r[w_dom_col])

            # If still missing, derive from website
            if not base.get("domain_root"):
                base["domain_root"] = compute_domain_root(base.get("website"))

            prof[cs] = base

    # Determine target selection mode
    companies_db = getenv("NOTION_COMPANIES_DB_ID")
    requested_raw = (getenv("BASELINE_CALLSIGNS") or "").strip()
    manual_override = bool(requested_raw)
    manual_filter = []
    if requested_raw and requested_raw.upper() != "ALL":
        manual_filter = [c.strip().lower() for c in requested_raw.split(",") if c.strip()]

    env_use_flags = getenv("BASELINE_USE_NOTION_FLAGS")
    if manual_override:
        use_notion_flags = False
    elif env_use_flags is None:
        use_notion_flags = bool(companies_db)
    else:
        use_notion_flags = env_use_flags.lower() in ("1", "true", "yes", "y")

    if use_notion_flags and companies_db:
        print("[NOTION MODE] Using Notion 'Needs Dossier' flags to determine targets")

        # Query Notion for companies needing dossiers
        try:
            notion_targets = get_companies_needing_dossiers(companies_db)
            print(f"[NOTION MODE] Found {len(notion_targets)} companies flagged for dossiers")

            # Filter to only include companies we have profile data for, or create minimal entries
            notion_callsigns = []
            for callsign, page_id in notion_targets:
                cs_lower = callsign.lower()
                notion_callsigns.append(cs_lower)

                # If we don't have profile data, create a minimal entry
                if cs_lower not in prof:
                    prof[cs_lower] = {
                        "callsign": callsign,
                        "dba": callsign,  # Use callsign as fallback
                        "owners": [],
                    }

                # Double-check that this page doesn't already have a dossier
                # (optional safety check in case flags weren't cleared)
                if page_has_dossier(page_id):
                    print(
                        f"[NOTION MODE] WARNING: {callsign} flagged but already has dossier, processing anyway"
                    )

            base_list = notion_callsigns
            print(f"[NOTION MODE] Targeting {len(base_list)} companies from Notion flags")

        except Exception as e:
            print(f"[NOTION MODE] ERROR: Failed to query Notion: {e}")
            print("[NOTION MODE] Falling back to CSV-based mode")
            base_list = sorted(prof.keys())
    else:
        if manual_override:
            print("[CSV MODE] Using explicit BASELINE_CALLSIGNS selection for targeting")
        else:
            print("[CSV MODE] Using CSV roster for targeting (no Notion flags available)")
        base_list = sorted(prof.keys())
        if manual_filter:
            base_list = [c for c in base_list if c in manual_filter]

    batch_size = int(getenv("BATCH_SIZE", "0") or "0") or None
    batch_index = int(getenv("BATCH_INDEX", "0") or "0") if batch_size else None
    targets_keys = slice_batch(base_list, batch_size, batch_index)

    print(
        f"Roster total: {len(base_list)} | This batch: {len(targets_keys)} "
        f"(batch_size={batch_size or '∞'}, batch_index={batch_index if batch_size else '-'})"
    )
    if targets_keys:
        head = targets_keys[:5]
        remainder = max(0, len(targets_keys) - len(head))
        print(
            "Batch head (first 5 callsigns):",
            ", ".join(head) + (f" … (+{remainder} more)" if remainder else ""),
        )
    else:
        print("Batch head: (empty)")
    if not targets_keys:
        print("No callsigns in this batch; nothing to do.")
        return

    lookback_days = int(getenv("BASELINE_LOOKBACK_DAYS", "180") or "180")
    g_api_key = getenv("GOOGLE_API_KEY")
    g_cse_id = getenv("GOOGLE_CSE_ID")

    # PARALLEL BASELINE PROCESSING
    PERFORMANCE_MONITOR.start_timer("baseline_processing")

    def process_single_company(cs):
        org = prof.get(cs, {"callsign": cs, "dba": cs, "owners": []})

        try:

            # Always resolve domain - function will prioritize CSV data or search as needed
            dr, url = resolve_domain_for_org(org, g_api_key, g_cse_id)

            if dr and not org.get("domain_root"):
                org["domain_root"] = dr
                org["domain"] = dr
            if url and not org.get("website"):
                org["website"] = url

            # Collect intelligence data
            news_items = collect_recent_news(org, lookback_days, g_api_key, g_cse_id)
            people_bg = collect_people_background(org, lookback_days, g_api_key, g_cse_id)

            # Skip funding collection if we have recent data
            if should_skip_processing(org, "funding_collection"):
                funding_data = org.get("cached_funding_data", {})
            else:
                funding_data = collect_funding_data(
                    org, lookback_days=540
                )  # 18 months for funding searches

            if isinstance(funding_data, str):
                try:
                    import json

                    funding_data = json.loads(funding_data)
                except Exception:
                    funding_data = {}

            if isinstance(funding_data, list):
                # Some cached payloads store a single funding dict in a list; take the first
                funding_data = (
                    funding_data[0] if funding_data and isinstance(funding_data[0], dict) else {}
                )

            if not isinstance(funding_data, dict):
                funding_data = {}

            # Generate narrative
            narr = generate_narrative(org, news_items, people_bg, funding_data)

            # Notion push with rate limiting
            try:
                push_dossier_to_notion(
                    (org.get("callsign") or "").strip(), org, narr, throttle_sec=0
                )  # Remove throttle, using smart rate limiting instead
                DEFAULT_RATE_LIMITER.wait_if_needed()  # Smart rate limiting
            except Exception as e:
                print(f"Notion dossier push error for {cs}: {e}")

            return {
                "callsign": org.get("callsign"),
                "body_md": narr,
                "status": "success",
            }

        except Exception as e:
            print(f"Error processing company {cs}: {e}")
            return {
                "callsign": cs,
                "body_md": f"Error processing {cs}: {e}",
                "status": "error",
            }

    print(f"Processing {len(targets_keys)} companies for baseline generation...")

    # Use smaller batches for baseline processing due to complexity
    batch_size = min(4, len(targets_keys))  # Conservative for complex operations
    dossiers: List[Dict[str, Any]] = []

    # Process in batches to avoid overwhelming APIs
    for i in range(0, len(targets_keys), batch_size):
        batch = targets_keys[i : i + batch_size]
        print(f"Processing batch {i//batch_size + 1} ({len(batch)} companies)")

        batch_results = ParallelProcessor.process_batch(
            batch,
            process_single_company,
            max_workers=batch_size,
            timeout=600,  # 10 minutes per batch
        )

        # Collect results
        for cs in batch:
            result = batch_results.get(cs)
            if result:
                dossiers.append(result)

        # Brief pause between batches to be respectful to APIs
        if i + batch_size < len(targets_keys):
            time.sleep(2)

    processing_time = PERFORMANCE_MONITOR.end_timer("baseline_processing")
    successful_count = sum(1 for d in dossiers if d.get("status") == "success")
    print(
        f"Baseline processing completed in {processing_time:.2f}s ({successful_count}/{len(targets_keys)} successful)"
    )

    # Preview or email
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
            body.append(
                f"<h3>{d.get('callsign')}</h3><pre style='white-space:pre-wrap'>{d['body_md']}</pre><hr/>"
            )
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
            f"Baselines — {datetime.now(timezone.utc).date()}",
            html,
        )
        print("Baselines emailed to", to)

    # Print performance statistics
    PERFORMANCE_MONITOR.print_stats()


if __name__ == "__main__":
    main()
