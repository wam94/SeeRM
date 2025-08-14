# app/dossier_baseline.py
from __future__ import annotations

"""
Baseline dossier builder
- Pulls roster from Profile/Weekly CSVs (via Gmail)
- Resolves/validates company domains (lightweight, fast timeouts)
- Gathers recent news + people background (RSS + Google CSE, optional)
- Heuristically extracts funding from fetched pages, optionally enriches via Crunchbase API
- Writes a "Dossier" section to the Notion Companies DB and clears "Needs Dossier"
- Supports batching + rate-limit knobs
"""

import os, io, re, math, time, json
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.notion_client import (
    upsert_company_page,
    append_dossier_blocks,
    set_needs_dossier,
    patch_company_properties,  # optional safety patch
)

import pandas as pd
import requests
import tldextract
from trafilatura import fetch_url, extract as trafi_extract

# --- Local modules
from app.gmail_client import (
    build_service, search_messages, get_message,
    extract_csv_attachments, send_html_email
)
from app.news_job import (
    fetch_csv_by_subject, build_queries, try_rss_feeds,
    google_cse_search, dedupe, within_days
)
from app.notion_client import upsert_company_page, set_needs_dossier


# =========================
# Env + small utilities
# =========================

def getenv(n: str, d: str | None = None) -> str | None:
    v = os.getenv(n)
    return d if v in (None, "") else v

DEBUG = (getenv("BASELINE_DEBUG","").lower() in ("1","true","yes","y"))

def logd(*parts: Any) -> None:
    if DEBUG:
        print(*parts)

def ensure_http(url: str | None) -> Optional[str]:
    if not url: return None
    u = url.strip()
    if not u: return None
    if not u.startswith(("http://","https://")):
        u = "https://" + u
    return u

def compute_domain_root(website_or_host: str | None) -> Optional[str]:
    if not website_or_host: return None
    w = website_or_host.strip().lower()
    w = re.sub(r'^https?://', '', w)
    w = re.sub(r'^www\.', '', w)
    host = w.split('/')[0]
    ext = tldextract.extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return host or None


# =========================
# Domain resolution
# =========================

_BLOCKED_SITES = {
    "linkedin.com","x.com","twitter.com","facebook.com","instagram.com","youtube.com",
    "github.com","medium.com","substack.com","notion.so","notion.site",
    "docs.google.com","wikipedia.org","angel.co"
}

HEAD_TIMEOUT = float(getenv("DOMAIN_HEAD_TIMEOUT_SEC", "2") or "2")
FETCH_TIMEOUT = float(getenv("FETCH_TIMEOUT_SEC", "5") or "5")

def _url_responds(url: str) -> bool:
    # Try HEAD quickly; if blocked, try a tiny GET
    try:
        r = requests.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True)
        if r.status_code < 400:
            return True
    except Exception:
        pass
    try:
        r = requests.get(url, timeout=HEAD_TIMEOUT, allow_redirects=True, stream=True)
        return r.status_code < 400
    except Exception:
        return False

def validate_domain_to_url(domain_root: str) -> Optional[str]:
    candidates = [f"https://{domain_root}", f"https://www.{domain_root}", f"http://{domain_root}"]
    for u in candidates:
        if _url_responds(u):
            return u
    # If nothing responds, return the first candidate to keep things moving
    return candidates[0] if candidates else None

def discover_domain_by_search(name: Optional[str],
                              g_api_key: Optional[str],
                              g_cse_id: Optional[str]) -> Optional[str]:
    """Use Google CSE to find an 'official' site; returns registered_domain."""
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
        logd("[discover_domain_by_search] error:", repr(e))
    return None

def resolve_domain_for_org(org: dict,
                           g_api_key: Optional[str],
                           g_cse_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Decide a domain for the org. Returns (domain_root, validated_url or None).
    Order: explicit org['domain'] -> compute from org['website'] -> search by DBA/callsign.
    """
    raw_site = (org.get("website") or "").strip() or None
    raw_domain = (org.get("domain") or org.get("domain_root") or "").strip() or None

    if raw_domain is None and raw_site:
        raw_domain = compute_domain_root(raw_site)

    if raw_domain is None:
        candidate = discover_domain_by_search(org.get("dba") or org.get("callsign"), g_api_key, g_cse_id)
        if candidate:
            raw_domain = candidate

    if raw_domain:
        url = validate_domain_to_url(raw_domain)
        return raw_domain, url
    return None, None


# =========================
# Evidence collection
# =========================

def load_latest_weekly_csv(service, user, q, attachment_regex):
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

def lower_cols(df: pd.DataFrame) -> Dict[str, str]:
    return {c.lower().strip(): c for c in df.columns}

def collect_recent_news(org: Dict[str, Any], lookback_days: int,
                        g_api_key: Optional[str], g_cse_id: Optional[str],
                        max_items: int = 6, max_queries: int = 5) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    # RSS/blog (prefer explicit blog_url; else website)
    site_for_rss = ensure_http(org.get("blog_url") or org.get("website") or org.get("domain_root"))
    if site_for_rss:
        try:
            items += try_rss_feeds(site_for_rss)
        except Exception:
            pass

    # Google CSE (site + name queries + optional owners)
    if (g_api_key and g_cse_id) and str(getenv("BASELINE_DISABLE_CSE","false")).lower() not in ("1","true","yes","y"):
        queries = build_queries(
            org.get("dba"), org.get("website"), org.get("owners"),
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

    # Clean and limit to window
    items = dedupe(items, key=lambda x: x.get("url"))
    items = [x for x in items if within_days(x.get("published_at", datetime.utcnow()), lookback_days)]
    # Normalize minimal fields
    for it in items:
        it["title"] = it.get("title") or ""
        it["url"] = it.get("url") or ""
        it["source"] = it.get("source") or (org.get("domain_root") or "")
        if isinstance(it.get("published_at"), datetime):
            it["published_at"] = it["published_at"].strftime("%Y-%m-%d")
    return items[:max_items]

def collect_people_background(org: Dict[str, Any], lookback_days: int,
                              g_api_key: Optional[str], g_cse_id: Optional[str],
                              max_people: int = 3) -> List[Dict[str, Any]]:
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
        items = dedupe(items, key=lambda x: x.get("url"))[:5]
        for it in items:
            it["title"] = it.get("title") or ""
            it["url"] = it.get("url") or ""
            it["source"] = it.get("source") or ""
        results.append({"name": person, "findings": items})
    return results


# =========================
# Funding heuristics + optional Crunchbase API
# =========================

AMOUNT_RE = re.compile(r'(?<![\d$])(?:USD\s*)?\$?\s*([0-9][\d,\.]*)\s*(billion|bn|million|mm|m|thousand|k)?', re.I)
ROUND_RE  = re.compile(r'\b(Pre-Seed|Seed|Angel|Series\s+[A-K]|Series\s+[A-K]\s+extension|Bridge|Convertible\s+Note|SAFE|Debt|Venture\s+Round|Equity\s+Round)\b', re.I)
DATE_RE   = re.compile(r'\b(20\d{2}|19\d{2})[-/\.](\d{1,2})[-/\.](\d{1,2})\b')
LED_BY_RE = re.compile(r'\b(led by|co-led by)\s+([^.;,\n]+)', re.I)
WITH_PARTICIPATION_RE = re.compile(r'\b(with participation from|including)\s+([^.;\n]+)', re.I)

def _to_usd(value_str: str, unit: Optional[str]) -> Optional[float]:
    try:
        n = float(value_str.replace(",", ""))
    except Exception:
        return None
    unit = (unit or "").lower()
    if unit in ("billion", "bn"):
        n *= 1_000_000_000
    elif unit in ("million", "mm", "m"):
        n *= 1_000_000
    elif unit in ("thousand", "k"):
        n *= 1_000
    return n

def _norm_date(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    if not m: return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except Exception:
        return None

def extract_funding_from_text(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    res: Dict[str, Any] = {}
    m = ROUND_RE.search(text)
    if m:
        res["last_round_type"] = m.group(1).title()
    m = AMOUNT_RE.search(text)
    if m:
        amt = _to_usd(m.group(1), m.group(2))
        if amt and math.isfinite(amt):
            res["last_round_amount_usd"] = int(round(amt))
    date = _norm_date(text)
    if date:
        res["last_round_date"] = date
    investors: List[str] = []
    for rx in (LED_BY_RE, WITH_PARTICIPATION_RE):
        mm = rx.search(text)
        if mm:
            investors += [p.strip(" .") for p in re.split(r',| and ', mm.group(2)) if p.strip()]
    if investors:
        investors = [re.sub(r'\(.*?\)$', '', i).strip() for i in investors]
        res["investors"] = sorted(set(investors))
    return res

def crunchbase_enrich(domain_root: Optional[str], name: Optional[str]) -> Dict[str, Any]:
    key = os.getenv("CRUNCHBASE_API_KEY")
    if not key:
        return {}
    H = {"X-cb-user-key": key, "Content-Type": "application/json"}
    BASE = "https://api.crunchbase.com/api/v4"

    # Search by domain, then by name
    payloads = []
    if domain_root:
        payloads.append({
            "field_ids": ["identifier","name","website","short_description"],
            "query": [{"type":"predicate","field_id":"website","operator_id":"contains","values":[domain_root]}],
            "limit": 1
        })
    if name:
        payloads.append({
            "field_ids": ["identifier","name","website","short_description"],
            "query": [{"type":"predicate","field_id":"name","operator_id":"contains","values":[name]}],
            "limit": 1
        })

    org_id = None
    for body in payloads:
        try:
            r = requests.post(f"{BASE}/searches/organizations", headers=H, json=body, timeout=10)
            if r.status_code != 200:
                continue
            ents = (r.json().get("entities") or [])
            if ents:
                org_id = ents[0]["identifier"].get("uuid") or ents[0]["identifier"].get("permalink")
                break
        except Exception:
            continue
    if not org_id:
        return {}

    body = {
        "field_ids": [
            "name","identifier","website",
            "last_funding_type","last_funding_at","last_funding_total_usd",
            "funding_total_usd","investors","investors_names","announced_on"
        ]
    }
    try:
        r = requests.post(f"{BASE}/entities/organizations/{org_id}", headers=H, json=body, timeout=10)
        if r.status_code != 200:
            return {}
        ent = r.json().get("properties", {})
        out: Dict[str, Any] = {}
        def get(*keys):
            for k in keys:
                if k in ent:
                    return ent.get(k)
            return None
        out["total_funding_usd"]   = get("funding_total_usd")
        out["last_round_type"]     = get("last_funding_type")
        out["last_round_date"]     = get("last_funding_at") or get("announced_on")
        out["last_round_amount_usd"] = get("last_funding_total_usd")
        inv = get("investors_names") or get("investors")
        if isinstance(inv, list):
            out["investors"] = inv[:10]
        elif isinstance(inv, str):
            out["investors"] = [s.strip() for s in inv.split(",") if s.strip()][:10]
        out["source_cb"] = True
        return {k:v for k,v in out.items() if v not in (None, "", [], 0)}
    except Exception:
        return {}

def merge_funding(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(primary or {})
    for k, v in (secondary or {}).items():
        if k not in out or out[k] in (None, "", [], 0):
            out[k] = v
    return out

def best_funding(org: Dict[str, Any], fetched_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    heur: Dict[str, Any] = {}
    sources: List[str] = []
    for p in fetched_pages:
        text = p.get("text") or ""
        if not text:
            continue
        cand = extract_funding_from_text(text)
        if cand:
            heur = merge_funding(cand, heur)
            if p.get("url"): sources.append(p["url"])
    cb = crunchbase_enrich(org.get("domain_root"), org.get("dba"))
    out = merge_funding(cb, heur) if cb else heur
    if sources:
        out["funding_sources"] = list(dict.fromkeys(sources))[:5]
    if out:
        out["funding_present"] = True
    return out


# =========================
# LLM narrative
# =========================

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

def _build_evidence_block(org: dict, news_items: list[dict], people_bg: list[dict], funding: dict | None) -> str:
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
        model = (os.getenv("OPENAI_CHAT_MODEL_DOSSIER")
                 or os.getenv("OPENAI_CHAT_MODEL")
                 or "gpt-5-mini").strip()
        temp_env = (os.getenv("OPENAI_TEMPERATURE_DOSSIER")
                    or os.getenv("OPENAI_TEMPERATURE")
                    or "").strip()
        temperature = None if temp_env in ("", "auto", "none") else float(temp_env)

        def try_call(send_temperature: bool):
            if model.startswith("gpt-5"):
                kwargs = {"model": model, "input": prompt}
                if send_temperature and temperature is not None:
                    kwargs["temperature"] = temperature
                r = client.responses.create(**kwargs)
                return r.output_text
            else:
                kwargs = {"model": model, "messages": [{"role":"user","content":prompt}]}
                if send_temperature and temperature is not None:
                    kwargs["temperature"] = temperature
                r = client.chat.completions.create(**kwargs)
                return r.choices[0].message.content

        try:
            out = try_call(send_temperature=True)
            return (out or "").strip()
        except Exception as e1:
            if "temperature" in repr(e1).lower() or "unrecognized request argument" in repr(e1).lower():
                out = try_call(send_temperature=False)
                return (out or "").strip()
            raise
    except Exception:
        return None

def generate_narrative(org: dict, news_items: list[dict], people_bg: list[dict], funding: dict | None) -> str:
    evidence = _build_evidence_block(org, news_items, people_bg, funding)
    prompt = f"{DOSSIER_GUIDANCE}\n\nEVIDENCE START\n{evidence}\nEVIDENCE END\n\nWrite the profile now."
    text = _openai_write_narrative(prompt)
    if text:
        return text

    # Fallback if no OpenAI lib/key: compact evidence-based
    lines = []
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
            date = n.get("date") or n.get("published_at","")
            src  = n.get("source","")
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
# Notion push
# =========================

def append_dossier_blocks(page_id: str, markdown_body: str):
    # very simple chunking as plain paragraphs
    chunks = [markdown_body[i:i+1800] for i in range(0, len(markdown_body), 1800)] or [markdown_body]
    hdr = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Dossier"}}]}
    }
    paras = [{
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": ch}}]}
    } for ch in chunks]
    r = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers={
            "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
            "Notion-Version": os.getenv("NOTION_VERSION","2022-06-28"),
            "Content-Type": "application/json",
        },
        json={"children": [hdr] + paras},
        timeout=10
    )
    r.raise_for_status()

def push_dossier_to_notion(callsign: str, org: dict, markdown_body: str, throttle_sec: float = 0.35):
    token = os.getenv("NOTION_API_KEY")
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    if not (token and companies_db):
        return

    # Prefer the normalized 'domain' key, fallback to legacy 'domain_root'
    domain_root = (org.get("domain") or org.get("domain_root") or "").strip() or None

    # If Website is absent but we have a domain, construct a URL for Notion URL fields
    website = (org.get("website") or "").strip() or None
    if not website and domain_root:
        website = ("https://" + domain_root)  # Notion URL props want a scheme

    payload = {
        "callsign": callsign,                    # used as the title field
        "company":  (org.get("dba") or "").strip(),
        "website":  website,                     # url or None
        "domain":   domain_root,                 # bare root, e.g., "example.com"
        "owners":   org.get("owners") or [],
        "needs_dossier": False,
    }

    # Upsert page and let the client write Website/Domain in a schema-aware way
    page_id = upsert_company_page(companies_db, payload)

    # Optional: belt-and-suspenders property patch after schema changes
    # Enable by setting BASELINE_NOTION_FORCE_PATCH=true
    if (os.getenv("BASELINE_NOTION_FORCE_PATCH", "").lower() in ("1", "true", "yes", "y")):
        try:
            patch_company_properties(page_id, companies_db, payload)
        except Exception as e:
            print("[Notion] property patch warning:", repr(e))

    # Append dossier content
    try:
        append_dossier_blocks(page_id, markdown_body)
    except Exception as e:
        print("[Notion] append blocks warning:", repr(e))

    # Clear “Needs Dossier”
    try:
        set_needs_dossier(page_id, False)
    except Exception:
        pass

    # Gentle throttle
    if throttle_sec and throttle_sec > 0:
        time.sleep(throttle_sec)


# =========================
# Batching
# =========================

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


# =========================
# Main
# =========================

def main():
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
            domain_root = r[pcols.get("domain_root")] if pcols.get("domain_root") in r else None
            owners_raw = r[pcols.get("beneficial_owners")] if pcols.get("beneficial_owners") in r else ""
            owners = [s.strip() for s in str(owners_raw or "").split(",") if s.strip()]
            base = {
                "callsign": r[pcols.get("callsign")],
                "dba": dba,
                "website": website,
                "domain_root": domain_root,
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
            # Always compute/repair domain_root
            base["domain_root"] = base.get("domain_root") or compute_domain_root(base.get("website"))
            prof[cs] = base

    # Merge fallback data from weekly
    if weekly is not None:
        wcols = lower_cols(weekly)
        for _, r in weekly.iterrows():
            cs = str(r[wcols.get("callsign")]).strip().lower() if wcols.get("callsign") in r else ""
            if not cs:
                continue
            base = prof.get(cs, {"callsign": r[wcols.get("callsign")], "owners": []})
            # Fill dba/website if missing
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
            # Repair domain_root
            base["domain_root"] = base.get("domain_root") or compute_domain_root(base.get("website"))
            prof[cs] = base

    # Build base list and apply optional manual filter (BASELINE_CALLSIGNS)
    base_list = sorted(prof.keys())
    requested = (getenv("BASELINE_CALLSIGNS") or "").strip()
    if requested and requested.upper() != "ALL":
        want = [c.strip().lower() for c in requested.split(",") if c.strip()]
        base_list = [c for c in base_list if c in want]

    # Batching
    batch_size = int(getenv("BATCH_SIZE", "0") or "0") or None
    batch_index = int(getenv("BATCH_INDEX", "0") or "0") if batch_size else None
    targets_keys = slice_batch(base_list, batch_size, batch_index)

    print(
        f"Roster total: {len(base_list)} | This batch: {len(targets_keys)} "
        f"(batch_size={batch_size or '∞'}, batch_index={batch_index if batch_size else '-'})"
    )
    # Hygiene: show first 5 of this batch
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

    # --- Process per org ---
    dossiers: List[Dict[str, Any]] = []
    for cs in targets_keys:
        org = prof.get(cs, {"callsign": cs, "dba": cs, "owners": []})

        # Domain resolution & website fill
        dr, url = resolve_domain_for_org(org, g_api_key, g_cse_id)
        if dr:
            org["domain"] = dr
            org["domain_root"] = dr
            org["website"] = org.get("website") or url
        if DEBUG:
            print(f"[DOMAIN] cs={org.get('callsign')} -> domain_root={dr} url={url}")

        # Recent intel
        news_items = collect_recent_news(org, lookback_days, g_api_key, g_cse_id)
        people_bg  = collect_people_background(org, lookback_days, g_api_key, g_cse_id)

        # Fetch a bit of page text for funding heuristics with short timeouts
        page_texts: List[Dict[str, Any]] = []
        def slurp(u: Optional[str]) -> Optional[str]:
            if not u: return None
            try:
                html = fetch_url(u, timeout=FETCH_TIMEOUT, no_ssl=True)
                if not html: return None
                txt = trafi_extract(html, output="txt", include_comments=False, favor_precision=True)
                return txt
            except Exception:
                return None

        if org.get("website"):
            t = slurp(org["website"])
            if t:
                page_texts.append({"url": org["website"], "text": t})

        for it in news_items[:3]:
            u = it.get("url")
            t = slurp(u)
            if t:
                page_texts.append({"url": u, "text": t})

        # Funding
        funding = best_funding(org, page_texts)

        # LLM narrative
        narr = generate_narrative(org, news_items, people_bg, funding)
        dossiers.append({"callsign": org.get("callsign"), "body_md": narr})

        # Notion
        try:
            logd(f"[NOTION] upsert payload: company={org.get('dba')} domain={org.get('domain')} website={org.get('website')}")
            push_dossier_to_notion((org.get("callsign") or "").strip(), org, narr, throttle_sec=notion_delay)
        except Exception as e:
            print("Notion dossier push error:", repr(e))

        if llm_delay:
            time.sleep(llm_delay)

    # Preview or optional email
    preview = getenv("PREVIEW_ONLY", "false").lower() in ("1","true","yes","y")
    if preview:
        for d in dossiers:
            print(f"\n=== {d.get('callsign')} ===\n")
            print(d["body_md"][:2500])
            print("\n----------------------------")
        return

    if getenv("SEND_EMAIL","false").lower() in ("1","true","yes","y"):
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
            html
        )
        print("Baselines emailed to", to)


if __name__ == "__main__":
    main()
