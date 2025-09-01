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
    upsert_company_page, set_latest_intel, append_intel_log
)

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

    # Google CSE (site + name queries + optional owners)
    disable_cse = str(os.getenv("CSE_DISABLE", "")).lower() in ("1","true","yes")
    if (g_api_key and g_cse_id) and not disable_cse:
        queries = build_queries(
            org.get("dba"), org.get("website"), org.get("owners"),
            domain_root=org.get("domain_root"),
            aka_names=org.get("aka_names"),
            tags=org.get("industry_tags"),
        )
        limit = int(os.getenv("CSE_MAX_QUERIES_PER_ORG", str(max_queries)) or max_queries)
        for q in queries[:limit]:
            try:
                items += google_cse_search(g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=5)
            except Exception:
                continue

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
    cols = lower_cols(df)
    roster: Dict[str, Dict[str, Any]] = {}
    for _, r in df.iterrows():
        cs = str(r[cols.get("callsign")]).strip().lower() if cols.get("callsign") in r else ""
        if not cs:
            continue
        if filter_callsigns and cs not in filter_callsigns:
            continue
        roster[cs] = {
            "callsign": r[cols.get("callsign")],
            "dba": r[cols.get("dba")] if cols.get("dba") in r else None,
            "website": r[cols.get("website")] if cols.get("website") in r else None,
            "domain_root": r[cols.get("domain_root")] if cols.get("domain_root") in r else None,
            "owners": [s.strip() for s in str(r[cols.get("beneficial_owners")] if cols.get("beneficial_owners") in r else "").split(",") if s.strip()],
            "aka_names": r[cols.get("aka_names")] if cols.get("aka_names") in r else None,
            "industry_tags": r[cols.get("industry_tags")] if cols.get("industry_tags") in r else None,
            "blog_url": r[cols.get("blog_url")] if cols.get("blog_url") in r else None,
        }

    # Collect intel
    intel_by_cs: Dict[str, List[Dict[str, Any]]] = {}
    for cs, org in roster.items():
        items = collect_recent_news(org, lookback_days, g_api_key, g_cse_id, max_items=max_per_org)
        intel_by_cs[cs] = items

    # Notion (optional)
    token = os.getenv("NOTION_API_KEY")
    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    intel_db     = os.getenv("NOTION_INTEL_DB_ID")
    if token and companies_db and intel_db:
        for cs, org in roster.items():
            # Upsert company page first
            page_id = upsert_company_page(companies_db, {
                "callsign": org["callsign"],
                "company":  org.get("dba") or "",
                "website":  org.get("website") or "",
                "domain":   org.get("domain_root") or "",
                "owners":   org.get("owners") or [],
                "needs_dossier": False,
            })

            # LLM summary (optional)
            text_blob = "\n".join([f"{it.get('published_at','')} — {it.get('title','')} — {it.get('source','')} {it.get('url','')}" for it in intel_by_cs.get(cs, [])])
            summary = _openai_summarize(text_blob) or f"{len(intel_by_cs.get(cs, []))} new items."

            # Set Latest Intel + add archive bullets
            today_iso = datetime.utcnow().date().isoformat()
            try:
                set_latest_intel(page_id, summary_text=summary, date_iso=today_iso, companies_db_id=companies_db)
            except Exception as e:
                print("Notion set_latest_intel error:", repr(e))
            try:
                append_intel_log(intel_db, page_id, str(org["callsign"]), today_iso, summary, intel_by_cs.get(cs, []), org.get("dba", ""))
            except Exception as e:
                print("Notion append_intel_log error:", repr(e))

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

if __name__ == "__main__":
    main()
