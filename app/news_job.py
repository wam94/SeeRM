# app/news_job.py
from __future__ import annotations
import os, io, re, json, math, time, requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Callable
import pandas as pd
import feedparser
import tldextract

from app.gmail_client import (
    build_service, search_messages, get_message,
    extract_csv_attachments, send_html_email
)
from app.notion_client import (
    upsert_company_page, set_latest_intel, update_intel_archive_for_company, get_all_companies_domain_data
)
from app.performance_utils import (
    ParallelProcessor, ConcurrentAPIClient, DEFAULT_RATE_LIMITER, 
    PERFORMANCE_MONITOR, has_valid_domain, should_skip_processing
)

# ---------------- Notion helpers for new company detection ----------------

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = lambda: {
    "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
    "Notion-Version": os.getenv("NOTION_VERSION","2022-06-28"),
    "Content-Type": "application/json",
}

def _dash32(x: str) -> str:
    return re.sub(r'^(.{8})(.{4})(.{4})(.{4})(.{12})$', r'\1-\2-\3-\4-\5', re.sub(r'-','',x))

def _companies_title_prop(companies_db_id: str) -> str:
    r = requests.get(f"{NOTION_API}/databases/{_dash32(companies_db_id)}", headers=NOTION_HEADERS(), timeout=30)
    r.raise_for_status()
    props = r.json().get("properties", {})
    for k,v in props.items():
        if v.get("type") == "title":
            return k
    return "Name"

def _find_company_page(companies_db_id: str, title_prop: str, callsign: str) -> Optional[str]:
    q = {
        "filter": {"property": title_prop, "title": {"equals": callsign}}
    }
    r = requests.post(f"{NOTION_API}/databases/{_dash32(companies_db_id)}/query",
                      headers=NOTION_HEADERS(), json=q, timeout=30)
    r.raise_for_status()
    res = r.json().get("results", [])
    return res[0]["id"] if res else None

def _set_needs_dossier(page_id: str, needs: bool = True):
    """Set the 'Needs Dossier' checkbox property for a company page."""
    props = {"Needs Dossier": {"checkbox": needs}}
    r = requests.patch(f"{NOTION_API}/pages/{page_id}", 
                       headers=NOTION_HEADERS(), 
                       json={"properties": props}, 
                       timeout=30)
    r.raise_for_status()

def ensure_company_page(companies_db_id: str, callsign: str,
                        website: Optional[str]=None,
                        domain: Optional[str]=None,
                        company: Optional[str]=None) -> tuple[str,bool]:
    """
    Returns (page_id, created_flag).
    Creates a Companies page if missing; sets Website/Domain when present in schema.
    """
    title_prop = _companies_title_prop(companies_db_id)
    pid = _find_company_page(companies_db_id, title_prop, callsign)
    created = False

    props = { title_prop: {"title":[{"type":"text","text":{"content": callsign[:200]}}]} }
    # We write extra props only if they exist in schema (URL or rich_text)
    schema = requests.get(f"{NOTION_API}/databases/{_dash32(companies_db_id)}", headers=NOTION_HEADERS(), timeout=30).json().get("properties",{})

    if company and schema.get("Company",{}).get("type") == "rich_text":
        props["Company"] = {"rich_text":[{"type":"text","text":{"content": company[:1000]}}]}
    if website and schema.get("Website",{}).get("type") == "url":
        props["Website"] = {"url": website}
    if domain:
        if schema.get("Domain",{}).get("type") == "url":
            props["Domain"] = {"url": f"https://{domain}"}
        elif schema.get("Domain",{}).get("type") == "rich_text":
            props["Domain"] = {"rich_text":[{"type":"text","text":{"content": domain}}]}

    if pid:
        requests.patch(f"{NOTION_API}/pages/{pid}", headers=NOTION_HEADERS(), json={"properties": props}, timeout=30).raise_for_status()
    else:
        r = requests.post(f"{NOTION_API}/pages", headers=NOTION_HEADERS(),
                          json={"parent":{"database_id": _dash32(companies_db_id)},
                                "properties": props}, timeout=30)
        r.raise_for_status()
        pid = r.json()["id"]
        created = True

    return pid, created

# ---------------- Gmail CSV fetch ----------------

def fetch_csv_by_subject(service, user: str, subject: str) -> Optional[pd.DataFrame]:
    """Find the newest email with subject containing <subject> and a CSV attachment."""
    if not subject:
        return None
    msgs = search_messages(service, user, f'subject:"{subject}" has:attachment filename:csv', max_results=5)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, r".*\.csv$")
        if atts:
            _, data = atts[0]
            try:
                return pd.read_csv(io.BytesIO(data))
            except Exception:
                continue
    return None

# ---------------- Query building ----------------

def build_queries(dba: Optional[str], website: Optional[str], owners: Optional[List[str]],
                  domain_root: Optional[str] = None,
                  aka_names: Optional[str] = None,
                  tags: Optional[str] = None) -> List[str]:
    names: List[str] = []
    if dba: names.append(str(dba).strip())
    if aka_names:
        names.extend([n.strip() for n in str(aka_names).split(",") if n.strip()])
    names = [n for n in names if n]
    domains: List[str] = []
    if domain_root: domains.append(domain_root)
    if website:
        w = re.sub(r"^https?://", "", website.strip().lower())
        w = re.sub(r"^www\.", "", w).split("/")[0]
        ext = tldextract.extract(w)
        if ext.registered_domain:
            domains.append(ext.registered_domain)

    Q: List[str] = []
    if domains:
        for d in set(domains):
            Q.append(f'site:{d} (launch OR announce OR funding OR partnership)')
    if names:
        for n in set(names):
            Q.append(f'"{n}" (launch OR product OR partnership OR funding OR raises)')
    if owners:
        for p in owners[:3]:
            p = p.strip()
            if p:
                Q.append(f'"{p}" ("{names[0]}" OR site:{domains[0] if domains else ""}) (CEO OR founder OR CTO OR CFO OR raises OR interview)')
    return [q for q in Q if q.strip()]

# ---------------- RSS / Feeds ----------------

def _try_feed(url: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    fp = feedparser.parse(url)
    for e in fp.entries[:10]:
        title = getattr(e, "title", "") or ""
        link  = getattr(e, "link", "") or ""
        date  = ""
        for key in ("published", "updated"):
            if hasattr(e, key):
                date = getattr(e, key) or ""
                break
        out.append({"title": title, "url": link, "source": "", "published_at": date})
    return out

def try_rss_feeds(site: Optional[str]) -> List[Dict[str, Any]]:
    if not site:
        return []
    site = str(site).strip()
    cand = []
    if site.startswith("http"):
        base = site.rstrip("/")
        cand = [f"{base}/feed", f"{base}/rss", base]
    else:
        cand = [f"https://{site}/feed", f"https://{site}/rss", f"https://{site}"]
    items: List[Dict[str, Any]] = []
    for u in cand:
        try:
            items.extend(_try_feed(u))
        except Exception:
            continue
    return items

# ---------------- Google CSE ----------------

def google_cse_search(api_key: str, cse_id: str, q: str, date_restrict: Optional[str] = None, num: int = 5) -> List[Dict[str, Any]]:
    if not (api_key and cse_id and q):
        return []
    params = {"key": api_key, "cx": cse_id, "q": q, "num": min(10, max(1, num))}
    if date_restrict:
        params["dateRestrict"] = date_restrict  # e.g., 'd10'
    r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=25)
    if not r.ok:
        return []
    out: List[Dict[str, Any]] = []
    for it in r.json().get("items", [])[:num]:
        link = it.get("link") or ""
        title = it.get("title") or ""
        snippet = it.get("snippet") or ""
        pagemap = it.get("pagemap") or {}
        dt = ""
        # crude date extraction
        if "metatags" in pagemap and pagemap["metatags"]:
            tags = pagemap["metatags"][0]
            for k in ("article:published_time", "og:updated_time", "date"):
                if k in tags:
                    dt = tags[k]
                    break
        out.append({
            "title": title,
            "url": link,
            "source": "",     # normalized later
            "published_at": dt or snippet  # fallback: snippet may contain date-ish text
        })
    return out

# ---------------- Utilities ----------------

def dedupe(items: List[Dict[str, Any]], key: Callable[[Dict[str, Any]], Any]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        k = key(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out

def within_days(published_at: Any, days: int) -> bool:
    """Allow both ISO date strings and datetime objects; default True if unknown."""
    if published_at is None or published_at == "":
        return True
    try:
        if isinstance(published_at, datetime):
            dt = published_at
        else:
            s = str(published_at).strip()
            # light normalization
            s = s.replace("/", "-").replace(".", "-")
            parts = [int(x) for x in s.split("-") if x.isdigit()]
            if len(parts) >= 3:
                y, m, d = parts[:3]
                dt = datetime(y, m, d)
            else:
                return True
        return (datetime.utcnow() - dt) <= timedelta(days=int(days))
    except Exception:
        return True

# ---------- Normalization helpers (date, source, url, title) ----------

def _source_from_url(url: str | None) -> str:
    if not url:
        return ""
    ext = tldextract.extract(url)
    return ext.registered_domain or (f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else "") or ""

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
        url   = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        src   = (it.get("source") or "").strip() or _source_from_url(url)
        date  = it.get("date") or it.get("published_at")
        out.append({
            "url": url,
            "title": title,
            "source": src,
            "published_at": _iso_date(date),
        })
    return out

# ---------------- Collection ----------------

def collect_recent_news(org: Dict[str, Any], lookback_days: int,
                        g_api_key: Optional[str], g_cse_id: Optional[str],
                        max_items: int = 6, max_queries: int = 5) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    # RSS/blog (prefer explicit blog_url; else website)
    site_for_rss = org.get("blog_url") or org.get("website")
    if site_for_rss:
        try:
            items += try_rss_feeds(site_for_rss)
        except Exception:
            pass

    # Google CSE (site + name queries + optional owners) - CONCURRENT
    disable_cse = str(os.getenv("CSE_DISABLE", "")).lower() in ("1","true","yes")
    if (g_api_key and g_cse_id) and not disable_cse:
        queries = build_queries(
            org.get("dba"), org.get("website"), org.get("owners"),
            domain_root=org.get("domain_root"),
            aka_names=org.get("aka_names"),
            tags=org.get("industry_tags"),
        )
        limit = int(os.getenv("CSE_MAX_QUERIES_PER_ORG", str(max_queries)) or max_queries)
        
        # Create API call functions for concurrent execution
        api_calls = []
        for q in queries[:limit]:
            api_calls.append(
                lambda query=q: google_cse_search(g_api_key, g_cse_id, query, date_restrict=f"d{lookback_days}", num=5)
            )
        
        # Execute queries concurrently with rate limiting
        if api_calls:
            api_client = ConcurrentAPIClient(DEFAULT_RATE_LIMITER)
            concurrent_results = api_client.batch_api_calls(api_calls, max_workers=4, timeout=30)
            
            # Flatten results
            for result in concurrent_results:
                if result:
                    items += result

    # Clean / dedupe / window
    items = dedupe(items, key=lambda x: x.get("url"))
    items = [x for x in items if within_days(x.get("published_at", datetime.utcnow()), lookback_days)]
    items = normalize_news_items(items)
    return items[:max_items]

# ---------------- LLM summary (optional) ----------------

def _openai_summarize(text: str) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not text:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-5-mini").strip()
        temp_env = (os.getenv("OPENAI_TEMPERATURE") or "0.2").strip()
        temperature = None if temp_env in ("", "auto", "none") else float(temp_env)

        prompt = (
            "Summarize the following items into a crisp 2–3 sentence weekly intel highlight. "
            "Keep dates and sources implicit; focus on what happened and why it matters:\n\n" + text
        )

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
        except Exception:
            out = try_call(send_temperature=False)
            return (out or "").strip()
    except Exception:
        return None

# ---------------- Email digest ----------------

def build_email_digest(intel: Dict[str, List[Dict[str, Any]]]) -> str:
    parts = ["<html><body><h2>Weekly Intel</h2>"]
    for cs, items in intel.items():
        parts.append(f"<h3>{cs}</h3><ul>")
        for it in items:
            line = f"- {it.get('published_at','')} — <a href=\"{it.get('url','')}\">{it.get('title') or it.get('url')}</a> — {it.get('source','')}"
            parts.append(f"<li>{line}</li>")
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)

# ---------------- Main job ----------------

def main():
    # Gmail
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = os.getenv("GMAIL_USER") or ""

    # Inputs / knobs
    filter_callsigns = [c.strip().lower() for c in (os.getenv("FILTER_CALLSIGNS") or "").split(",") if c.strip()]
    lookback_days = int(os.getenv("INTEL_LOOKBACK_DAYS", "10") or "10")
    max_per_org = int(os.getenv("INTEL_MAX_PER_ORG", "5") or "5")
    preview_only = str(os.getenv("PREVIEW_ONLY", "true")).lower() in ("1","true","yes","y")

    g_api_key = os.getenv("GOOGLE_API_KEY")
    g_cse_id  = os.getenv("GOOGLE_CSE_ID")

    # Pull roster CSV (subject configured via env)
    profile_subject = os.getenv("NEWS_PROFILE_SUBJECT") or "Org Profile — Will Mitchell"
    df = fetch_csv_by_subject(svc, user, profile_subject)
    if df is None:
        print("No profile CSV found by subject; exiting.")
        return

    # Build roster dict
    def lower_cols(df): return {c.lower().strip(): c for c in df.columns}
    def safe_str(val):
        """Safely convert value to string, handling NaN and None"""
        if val is None or (hasattr(val, '__name__') and val.__name__ == 'nan') or str(val).lower() == 'nan':
            return ""
        return str(val).strip()
    
    cols = lower_cols(df)
    roster: Dict[str, Dict[str, Any]] = {}
    for _, r in df.iterrows():
        cs = safe_str(r[cols.get("callsign")]).lower() if cols.get("callsign") in r else ""
        if not cs:
            continue
        if filter_callsigns and cs not in filter_callsigns:
            continue
        roster[cs] = {
            "callsign": safe_str(r[cols.get("callsign")]) if cols.get("callsign") in r else cs,
            "dba": safe_str(r[cols.get("dba")]) if cols.get("dba") in r and r[cols.get("dba")] is not None else None,
            "website": safe_str(r[cols.get("website")]) if cols.get("website") in r and r[cols.get("website")] is not None else None,
            "domain_root": safe_str(r[cols.get("domain_root")]) if cols.get("domain_root") in r and r[cols.get("domain_root")] is not None else None,
            "owners": [s.strip() for s in safe_str(r[cols.get("beneficial_owners")] if cols.get("beneficial_owners") in r else "").split(",") if s.strip()],
            "aka_names": safe_str(r[cols.get("aka_names")]) if cols.get("aka_names") in r and r[cols.get("aka_names")] is not None else None,
            "industry_tags": safe_str(r[cols.get("industry_tags")]) if cols.get("industry_tags") in r and r[cols.get("industry_tags")] is not None else None,
            "blog_url": safe_str(r[cols.get("blog_url")]) if cols.get("blog_url") in r and r[cols.get("blog_url")] is not None else None,
        }

    # Detect new companies and flag for baseline generation
    new_callsigns: List[str] = []
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    
    if companies_db:
        for cs, org in roster.items():
            callsign = str(org.get("callsign") or "").strip()
            dba = str(org.get("dba") or "").strip() or None
            website = str(org.get("website") or "").strip() or None
            domain = str(org.get("domain_root") or "").strip() or None
            
            if callsign:
                try:
                    page_id, created = ensure_company_page(companies_db, callsign, 
                                                         website=website, domain=domain, company=dba)
                    if created:
                        new_callsigns.append(callsign)
                        # Set needs_dossier flag for new companies
                        try:
                            _set_needs_dossier(page_id, True)
                            print(f"[NEW COMPANY] Created and flagged for baseline: {callsign}")
                        except Exception as e:
                            print(f"[ERROR] Failed to set needs_dossier for {callsign}: {e}")
                except Exception as e:
                    print(f"[ERROR] Failed to ensure company page for {callsign}: {e}")
    
    if new_callsigns:
        print(f"[NEW COMPANIES] Created {len(new_callsigns)} new company pages: {', '.join(new_callsigns[:8])}{' ...' if len(new_callsigns) > 8 else ''}")
        
        # Write new callsigns to trigger baseline generation
        try:
            with open("/tmp/new_callsigns.txt", "w") as f:
                f.write(",".join(new_callsigns))
            print(f"[TRIGGER] Wrote {len(new_callsigns)} new callsigns to /tmp/new_callsigns.txt for baseline generation")
        except Exception as e:
            print(f"[ERROR] Failed to write new callsigns trigger file: {e}")

    # Fetch canonical domain data from Notion for all companies (batched for efficiency)
    PERFORMANCE_MONITOR.start_timer("notion_domain_fetch")
    notion_domain_data = {}
    if companies_db:
        print(f"[NOTION] Fetching canonical domain data for {len(roster)} companies...")
        try:
            callsigns = list(roster.keys())
            notion_domain_data = get_all_companies_domain_data(companies_db, callsigns)
            found_domains = sum(1 for data in notion_domain_data.values() if data.get("domain") or data.get("website"))
            print(f"[NOTION] Found domain data for {found_domains}/{len(callsigns)} companies")
        except Exception as e:
            print(f"[ERROR] Failed to fetch domain data from Notion: {e}")
            # Fallback to empty dict
            notion_domain_data = {cs: {"domain": None, "website": None} for cs in roster.keys()}
    
    domain_fetch_time = PERFORMANCE_MONITOR.end_timer("notion_domain_fetch")
    print(f"[PERFORMANCE] Domain data fetch completed in {domain_fetch_time:.2f}s")

    # Collect intel - PARALLEL PROCESSING
    PERFORMANCE_MONITOR.start_timer("intel_collection")
    
    def collect_news_for_company(cs, org):
        # Skip if we have very recent data
        if should_skip_processing(org, "news_collection"):
            print(f"[SKIP] Recent news data exists for {cs}")
            return cs, org.get("cached_news", [])
        
        # Enhance org data with canonical domain data from Notion
        enhanced_org = dict(org)  # Copy original org data
        notion_domains = notion_domain_data.get(cs, {})
        domain_sources = []
        
        if notion_domains.get("domain"):
            if enhanced_org.get("domain_root") != notion_domains["domain"]:
                domain_sources.append(f"domain: CSV '{enhanced_org.get('domain_root')}' → Notion '{notion_domains['domain']}'")
            enhanced_org["domain_root"] = notion_domains["domain"]
        
        if notion_domains.get("website"):
            if enhanced_org.get("website") != notion_domains["website"]:
                domain_sources.append(f"website: CSV '{enhanced_org.get('website')}' → Notion '{notion_domains['website']}'")
            enhanced_org["website"] = notion_domains["website"]
        
        if domain_sources and os.getenv("DEBUG"):
            print(f"[DOMAIN] {cs}: {'; '.join(domain_sources)}")
        
        items = collect_recent_news(enhanced_org, lookback_days, g_api_key, g_cse_id, max_items=max_per_org)
        return cs, items
    
    # Process companies in parallel
    print(f"[PARALLEL] Processing {len(roster)} companies for news collection...")
    
    results = ParallelProcessor.process_dict_batch(
        roster, 
        collect_news_for_company,
        max_workers=6,  # Conservative for API rate limits
        timeout=300  # 5 minutes total
    )
    
    intel_by_cs: Dict[str, List[Dict[str, Any]]] = {}
    for cs in roster.keys():
        intel_by_cs[cs] = results.get(cs, [])
    
    collection_time = PERFORMANCE_MONITOR.end_timer("intel_collection")
    print(f"[PERFORMANCE] Intel collection completed in {collection_time:.2f}s")

    # Notion (optional) - PARALLEL PROCESSING
    token = os.getenv("NOTION_API_KEY")
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    intel_db     = os.getenv("NOTION_INTEL_DB_ID")
    if token and companies_db and intel_db:
        PERFORMANCE_MONITOR.start_timer("notion_updates")
        
        def process_company_notion(cs, org):
            
            # Skip if no new intelligence data
            intel_items = intel_by_cs.get(cs, [])
            if not intel_items:
                return None
                
            try:
                # Ensure org is a dict (safety check)
                if not isinstance(org, dict):
                    print(f"[ERROR] Expected dict for org data, got {type(org)}: {org}")
                    return {"status": "error", "error": f"Invalid org data type: {type(org)}"}
                
                # Upsert company page first (without domain/website to avoid overwriting baseline job data)
                page_id = upsert_company_page(companies_db, {
                    "callsign": org.get("callsign") or cs,  # Use cs as fallback
                    "company":  str(org.get("dba") or "").strip() if org.get("dba") is not None else "",
                    "owners":   org.get("owners") or [],
                    "needs_dossier": False,
                })

                # LLM summary (optional)
                text_blob = "\n".join([f"{it.get('published_at','')} — {it.get('title','')} — {it.get('source','')} {it.get('url','')}" for it in intel_items])
                summary = _openai_summarize(text_blob) or f"{len(intel_items)} new items."

                # Set Latest Intel + update Intel archive with new system
                today_iso = datetime.utcnow().date().isoformat()
                
                # Keep slim "Latest Intel" on Companies DB
                try:
                    DEFAULT_RATE_LIMITER.wait_if_needed()
                    set_latest_intel(page_id, summary_text=summary, date_iso=today_iso, companies_db_id=companies_db)
                except Exception as e:
                    print(f"WARN set_latest_intel for {cs}: {e}")

                # Update Intel archive: latest summary only + dated timeline bullets
                try:
                    DEFAULT_RATE_LIMITER.wait_if_needed()
                    update_intel_archive_for_company(
                        intel_db_id=intel_db,
                        companies_db_id=companies_db,
                        company_page_id=page_id,
                        callsign=str(org.get("callsign") or cs),
                        date_iso=today_iso,
                        summary_text=summary,
                        items=intel_items,
                        summary_max_bytes=250_000,   # ~250 KB for property
                        timeline_max_bytes=800_000,  # ~800 KB for toggle groups
                        overwrite_summary_only=True,  # important: no summary history
                    )
                except Exception as e:
                    print(f"WARN intel archive for {cs}: {e}")
                
                return {"status": "success", "items": len(intel_items)}
                
            except Exception as e:
                print(f"[NOTION ERROR] Failed to process {cs}: {e}")
                return {"status": "error", "error": str(e)}
        
        # Process Notion updates in parallel (with lower concurrency for API limits)
        notion_results = ParallelProcessor.process_dict_batch(
            roster,
            process_company_notion,
            max_workers=3,  # Conservative for Notion API limits
            timeout=300
        )
        
        notion_time = PERFORMANCE_MONITOR.end_timer("notion_updates")
        successful_updates = sum(1 for r in notion_results.values() if r and r.get("status") == "success")
        print(f"[PERFORMANCE] Notion updates completed in {notion_time:.2f}s ({successful_updates} successful)")

    # Send optional email digest
    digest_to = os.getenv("DIGEST_TO") or os.getenv("GMAIL_USER") or ""
    if not preview_only and digest_to:
        html = build_email_digest(intel_by_cs)
        try:
            send_html_email(
                build_service(
                    client_id=os.environ["GMAIL_CLIENT_ID"],
                    client_secret=os.environ["GMAIL_CLIENT_SECRET"],
                    refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
                ),
                os.getenv("GMAIL_USER") or "",
                digest_to,
                f"Weekly Intel — {datetime.utcnow().date()}",
                html
            )
            print("Digest emailed to", digest_to)
        except Exception as e:
            print("Email digest error:", repr(e))
    
    # Print performance statistics
    PERFORMANCE_MONITOR.print_stats()

if __name__ == "__main__":
    main()
