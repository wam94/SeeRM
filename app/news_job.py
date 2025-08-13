# app/news_job.py
from __future__ import annotations
import os, io, hashlib, time
from datetime import datetime, timedelta
from typing import List, Dict, Any

import pandas as pd
import requests
import feedparser
import tldextract
from jinja2 import Template

from app.gmail_client import (
    build_service,
    search_messages,
    get_message,
    extract_csv_attachments,
    send_html_email,
)

# ------------------------ Utilities ------------------------

def getenv(name: str, default=None):
    v = os.getenv(name)
    return default if v in (None, "") else v

def now_utc_date() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def normalize_url(u: str) -> str:
    return (u or "").strip()

def url_hash(u: str) -> str:
    return hashlib.sha1(normalize_url(u).encode("utf-8")).hexdigest()

def domain_from_url(url: str) -> str:
    try:
        ext = tldextract.extract(url)
        return ".".join([p for p in [ext.domain, ext.suffix] if p])
    except Exception:
        return ""

def dedupe(items, key=lambda x: x["url"]):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if not k:
            continue
        h = url_hash(k)
        if h in seen:
            continue
        seen.add(h)
        out.append(it)
    return out

def within_days(dt: datetime, days: int) -> bool:
    try:
        return dt >= datetime.utcnow() - timedelta(days=days)
    except Exception:
        return True

# ------------------------ Query building ------------------------

def build_queries(
    dba: str,
    website: str,
    owners: List[str] | None,
    *,
    domain_root: str | None = None,
    aka_names: str | None = None,
    tags: str | None = None,
) -> List[str]:
    """Construct focused queries for Google CSE."""
    names: List[str] = []
    if dba:
        names.append(dba)
    if aka_names:
        names.extend([a.strip() for a in aka_names.split(",") if a.strip()])
    # de-dupe preserving order
    seen = set()
    names = [n for n in names if not (n in seen or seen.add(n))]

    # Clean host root
    root = (domain_root or (website or "")
            .lower().replace("https://", "").replace("http://", "")
            .replace("www.", "").strip("/"))

    qs: List[str] = []
    # Site-centric
    if root:
        qs.append(f'site:{root} (launch OR announces OR announcement OR product OR release OR funding OR raised OR partners OR integrates)')
        qs.append(f'site:{root} blog')
        qs.append(f'site:{root} press')

    # Name-centric
    for n in names[:2]:
        qs.append(f'"{n}" (launch OR product OR release OR funding OR partners OR integrates)')

    # Exec/owner
    for p in (owners or [])[:2]:
        if p:
            if names:
                qs.append(f'"{p}" "{names[0]}"')
            elif dba:
                qs.append(f'"{p}" "{dba}"')
            else:
                qs.append(f'"{p}"')

    # Industry tags
    if tags and (names or dba):
        pivot = names[0] if names else dba
        qs.append(f'{pivot} {tags} news')

    return [q for q in qs if q and len(q) > 3]

# ------------------------ Sources ------------------------

def try_rss_feeds(website: str) -> List[Dict[str, Any]]:
    cand = []
    if not website:
        return cand
    w = website.rstrip("/")
    for path in ["", "/blog", "/news", "/press", "/updates", "/stories"]:
        for rss in ["/feed", "/rss", "/rss.xml", "/index.xml", "/atom.xml"]:
            cand.append(w + path + rss)
    out = []
    for url in cand:
        try:
            d = feedparser.parse(url)
            if d.bozo:  # not a proper feed
                continue
            for e in d.entries[:10]:
                title = getattr(e, "title", None)
                link = getattr(e, "link", None)
                if not link or not title:
                    continue
                published = None
                for fld in ["published_parsed", "updated_parsed"]:
                    if getattr(e, fld, None):
                        ts = time.mktime(getattr(e, fld))
                        published = datetime.utcfromtimestamp(ts)
                        break
                out.append({
                    "title": title,
                    "url": link,
                    "source": domain_from_url(link) or domain_from_url(url) or "rss",
                    "published_at": published or datetime.utcnow(),
                    "snippet": getattr(e, "summary", None),
                })
        except Exception:
            continue
    return out

def google_cse_search(api_key: str, cse_id: str, query: str, date_restrict: str = "d7", num: int = 5) -> List[Dict[str, Any]]:
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": num,
        "dateRestrict": date_restrict,
        "safe": "off",
    }
    r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    out = []
    for item in data.get("items", []):
        link = item.get("link")
        title = item.get("title")
        snippet = item.get("snippet")
        if not link or not title:
            continue
        out.append({
            "title": title,
            "url": link,
            "source": domain_from_url(link) or item.get("displayLink"),
            "published_at": datetime.utcnow(),  # CSE seldom gives reliable pubdate
            "snippet": snippet,
        })
    return out

# ------------------------ Full-article fetching & LLM summaries ------------------------

def extract_article_text(url: str, timeout: int = 15, max_bytes: int = 400_000) -> str | None:
    """
    Try to download and extract main text from an article URL.
    1) trafilatura (best) via fetch_url + extract
    2) requests + BeautifulSoup (fallback)
    Returns plain text or None.
    """
    if not url:
        return None
    # Trafilatura path
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                favor_recall=True,
                include_comments=False,
                include_tables=False
            )
            if text:
                return text[:max_bytes]
    except Exception:
        pass

    # Fallback: requests + bs4
    try:
        from bs4 import BeautifulSoup
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (GitHubActions; ExternalIntel)"}
        )
        r.raise_for_status()
        html = r.content[:max_bytes]
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()
        txt = soup.get_text("\n", strip=True)
        return txt[:max_bytes] if txt and len(txt) > 200 else None
    except Exception:
        return None

def enrich_with_fulltext(items, per_org_cap: int, timeout: int, max_bytes: int):
    """
    For up to 'per_org_cap' items, fetch article text and attach as item['fulltext'].
    """
    out = []
    fetched = 0
    for it in items:
        it = dict(it)  # shallow copy
        if fetched < per_org_cap:
            txt = extract_article_text(it.get("url"), timeout=timeout, max_bytes=max_bytes)
            if txt:
                it["fulltext"] = txt
                fetched += 1
        out.append(it)
    return out

def summarize_items_with_llm(items, org_label: str) -> str:
    """
    Summarize using fulltext when available; otherwise fall back to title/snippet.
    Produces 2–4 crisp bullets.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fallback summary from titles only
        lines = []
        for it in items[:5]:
            src = it.get("source", "")
            lines.append(f"- {it.get('title','')}{' — ' + src if src else ''}")
        return "\n".join(lines)[:800]

    # Build a compact evidence block
    chunks = []
    for it in items[:5]:
        title = it.get("title") or ""
        src   = it.get("source") or ""
        url   = it.get("url") or ""
        body  = it.get("fulltext") or it.get("snippet") or ""
        body  = body.strip().replace("\r", " ").replace("\n", " ")
        if len(body) > 2000:
            body = body[:2000] + " …"
        chunks.append(f"TITLE: {title}\nSOURCE: {src}\nURL: {url}\nTEXT: {body}\n---")

    prompt = (
        "You are writing a brief external intel note for an account manager.\n"
        f"Company context: {org_label}\n\n"
        "Below are recent items (titles + article text when available). "
        "Synthesize the most relevant developments into 2–4 crisp, factual bullets. "
        "Prefer product launches, funding, partnerships, and material changes over generic thought leadership. "
        "Avoid fluff and duplication. Include company/product names when needed for clarity.\n\n"
        + "\n".join(chunks)
    )
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        # Fall back to titles if the API fails for any reason
        lines = []
        for it in items[:5]:
            src = it.get("source", "")
            lines.append(f"- {it.get('title','')}{' — ' + src if src else ''}")
        return "\n".join(lines)[:800]

# ------------------------ HTML template ------------------------

INTEL_TEMPLATE = Template("""
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; line-height:1.45; color:#111; }
  .h1 { font-size:20px; font-weight:700; margin:0 0 8px; }
  .muted{ color:#555; } .section{ margin:16px 0 24px; }
  table{ border-collapse:collapse; width:100%; } th,td{ padding:6px 8px; border-bottom:1px solid #eee; text-align:left; }
  .mono{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
  .src{ color:#555; font-size:12px; }
</style></head>
<body>
  <div class="section">
    <div class="h1">External Intel — {{ today }}</div>
    <div class="muted">Automatic scan of last {{ lookback_days }} days for your book.</div>
  </div>

  {% for org in orgs %}
    <div class="section">
      <div class="h1">{{ org.callsign }}{% if org.dba %} — {{ org.dba }}{% endif %}</div>
      {% if org["items"] %}
        <table>
          <thead><tr><th>Title</th><th>Source</th></tr></thead>
          <tbody>
          {% for it in org["items"] %}
            <tr>
              <td><a href="{{ it.url }}">{{ it.title }}</a><div class="src">{{ it.snippet }}</div></td>
              <td class="src">{{ it.source }}</td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      {% else %}
        <div class="muted">No notable items this week.</div>
      {% endif %}
      {% if org.summary %}
        <div style="margin-top:8px;"><strong>Summary</strong><div>{{ org.summary|replace('\\n','<br>') }}</div></div>
      {% endif %}
    </div>
  {% endfor %}

  <div class="section muted">— End of report</div>
</body></html>
""")

# ------------------------ Gmail CSV helper ------------------------

def fetch_csv_by_subject(service, user, subject, attachment_regex=r".*\.csv$", max_results=5):
    """Return a pandas DataFrame for the newest email with this subject containing a CSV attachment."""
    q = f'subject:"{subject}" has:attachment filename:csv newer_than:30d'
    msgs = search_messages(service, user, q, max_results=max_results)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if atts:
            name, data = atts[0]
            return pd.read_csv(io.BytesIO(data))
    return None

# ------------------------ Main ------------------------

def main():
    # Config
    lookback_days = int(getenv("INTEL_LOOKBACK_DAYS", "10"))
    max_per_org   = int(getenv("INTEL_MAX_PER_ORG", "5"))
    g_api_key = getenv("GOOGLE_API_KEY")
    g_cse_id  = getenv("GOOGLE_CSE_ID")

    # Use news-specific variables; fall back to generic names or defaults
    weekly_query = getenv("NEWS_GMAIL_QUERY")
    profile_subject = getenv("NEWS_PROFILE_SUBJECT")

    # Cost-control knobs for CSE
    only_if_rss_below = int(getenv("CSE_ONLY_IF_RSS_BELOW", "999"))
    max_q_per_org     = int(getenv("CSE_MAX_QUERIES_PER_ORG", "999"))
    disable_owner     = getenv("CSE_DISABLE_OWNER_QUERIES", "false").lower() in ("1","true","yes")
    disable_tag       = getenv("CSE_DISABLE_TAG_QUERIES", "false").lower() in ("1","true","yes")

    # Article fetching knobs
    fetch_fulltext    = getenv("FETCH_ARTICLE_CONTENT", "true").lower() in ("1","true","yes","y")
    fetch_max_per_org = int(getenv("FETCH_MAX_PER_ORG", "3"))
    article_timeout   = int(getenv("ARTICLE_READ_TIMEOUT", "15"))
    article_max_bytes = int(getenv("ARTICLE_MAX_BYTES", "400000"))

    # Gmail service
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = getenv("GMAIL_USER") or os.environ["GMAIL_USER"]

    # Load latest weekly CSV via Gmail
    msgs = search_messages(svc, user, weekly_query, max_results=5)
    df = None
    for m in msgs:
        msg = get_message(svc, user, m["id"])
        atts = extract_csv_attachments(svc, user, msg, getenv("ATTACHMENT_REGEX", r".*\.csv$"))
        if not atts:
            continue
        name, data = atts[0]
        df = pd.read_csv(io.BytesIO(data))
        break
    if df is None:
        raise SystemExit("No weekly CSV found via Gmail. Adjust NEWS_GMAIL_QUERY (or GMAIL_QUERY) or wait for the Metabase email.")

    # Load Org Profile CSV
    df_profile = fetch_csv_by_subject(svc, user, profile_subject)

    # Build profile lookup
    prof: Dict[str, Dict[str, Any]] = {}
    if df_profile is not None:
        pcols = {c.lower().strip(): c for c in df_profile.columns}
        def pget(row, key): return row[pcols[key]] if key in pcols else None
        for _, r in df_profile.iterrows():
            cs = (pget(r, "callsign") or "").strip()
            if not cs:
                continue
            prof[cs] = {
                "dba": pget(r, "dba"),
                "website": pget(r, "website"),
                "domain_root": pget(r, "domain_root"),
                "aka_names": pget(r, "aka_names"),
                "blog_url": pget(r, "blog_url"),
                "rss_feeds": pget(r, "rss_feeds"),
                "linkedin_url": pget(r, "linkedin_url"),
                "twitter_handle": pget(r, "twitter_handle"),
                "crunchbase_url": pget(r, "crunchbase_url"),
                "industry_tags": pget(r, "industry_tags"),
                "hq_city": pget(r, "hq_city"),
                "hq_region": pget(r, "hq_region"),
                "hq_country": pget(r, "hq_country"),
                "beneficial_owners": pget(r, "beneficial_owners"),
            }

    # Merge weekly rows with profile (by callsign)
    cols = {c.lower().strip(): c for c in df.columns}
    def col(k): return cols.get(k)

    orgs = []
    seen_cs = set()
    for _, r in df.iterrows():
        cs = (str(r.get(col("callsign")) or "")).strip()
        if not cs or cs in seen_cs:
            continue
        seen_cs.add(cs)
        base = {
            "callsign": cs,
            "dba": r.get(col("dba")),
            "website": r.get(col("website")),
            "owners": (r.get(col("beneficial_owners")) or "").split(", "),
        }
        if cs in prof:
            for k, v in prof[cs].items():
                if k == "beneficial_owners" and v:
                    base["owners"] = [s.strip() for s in str(v).split(",") if s.strip()]
                elif v not in (None, "", []):
                    base[k] = v
        orgs.append(base)

    # Optional: filter to specific callsigns (for testing)
    only = getenv("FILTER_CALLSIGNS")
    if only:
        want = {c.strip().lower() for c in only.split(",") if c.strip()}
        orgs = [o for o in orgs if o.get("callsign", "").lower() in want]

    # Fetch intel
    enriched = []
    for org in orgs:
        items: List[Dict[str, Any]] = []

        # Official RSS/blog
        site_for_rss = org.get("blog_url") or org.get("website")
        if site_for_rss:
            items += try_rss_feeds(site_for_rss)

        # Google CSE (with cost-control knobs)
        if g_api_key and g_cse_id:
            # Skip CSE entirely if RSS already produced enough items
            should_use_cse = len(items) < only_if_rss_below
            if should_use_cse:
                queries = build_queries(
                    org.get("dba"),
                    org.get("website"),
                    [] if disable_owner else org.get("owners"),
                    domain_root=org.get("domain_root"),
                    aka_names=org.get("aka_names"),
                    tags=None if disable_tag else org.get("industry_tags"),
                )
                for q in queries[:max_q_per_org]:
                    try:
                        items += google_cse_search(g_api_key, g_cse_id, q, date_restrict=f"d{lookback_days}", num=max_per_org)
                    except Exception:
                        continue

        # Clean & limit
        items = dedupe(items, key=lambda x: x["url"])
        items = [x for x in items if within_days(x.get("published_at", datetime.utcnow()), lookback_days)]
        items = items[:max_per_org]

        # Optional: fetch & extract article content (capped)
        if fetch_fulltext and items:
            items = enrich_with_fulltext(items, fetch_max_per_org, article_timeout, article_max_bytes)

        # Summarize (prefers fulltext when available)
        summary = ""
        if items:
            org_label = (org.get("dba") or org.get("domain_root") or org.get("callsign") or "").strip()
            summary = summarize_items_with_llm(items, org_label)

        enriched.append({
            "callsign": org["callsign"],
            "dba": org.get("dba"),
            "items": items,
            "summary": summary,
        })

    # Render
    html = INTEL_TEMPLATE.render(
        today=now_utc_date(),
        lookback_days=lookback_days,
        orgs=enriched,
    )

    # Preview mode: print and exit early
    if getenv("PREVIEW_ONLY", "false").lower() in ("1", "true", "yes", "y"):
        print("\n--- HTML PREVIEW (truncated) ---")
        print("\n".join(html.splitlines()[:200]))
        print("\n--- END PREVIEW ---")
        return  # safely inside main()

    # Send email
    to = getenv("DIGEST_TO") or user
    send_html_email(
        svc, user, to,
        subject=f"External Intel — {now_utc_date()}",
        html=html,
        cc=getenv("DIGEST_CC"),
        bcc=getenv("DIGEST_BCC"),
    )
    print("External intel sent to", to)

if __name__ == "__main__":
    main()
