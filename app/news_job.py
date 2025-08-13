from __future__ import annotations
import os, io, hashlib, time
from datetime import datetime, timedelta
import pandas as pd
import requests
import feedparser
import tldextract

from app.gmail_client import build_service, search_messages, get_message, extract_csv_attachments, send_html_email
from app.digest_render import Template  # we'll embed a small intel template below

# ---------- Helpers ----------
def getenv(name: str, default=None):
    v = os.getenv(name)
    return default if v in (None, "") else v

def now_utc_date():
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
        if not k: continue
        h = url_hash(k)
        if h in seen: continue
        seen.add(h)
        out.append(it)
    return out

def within_days(dt: datetime, days: int) -> bool:
    try:
        return dt >= datetime.utcnow() - timedelta(days=days)
    except Exception:
        return True

def fetch_csv_by_subject(service, user, subject, attachment_regex=r".*\.csv$", max_results=5):
    q = f'subject:"{subject}" has:attachment filename:csv newer_than:10d'
    msgs = search_messages(service, user, q, max_results=max_results)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if atts:
            name, data = atts[0]
            return pd.read_csv(io.BytesIO(data))
    return None

# ---------- Query building ----------
def build_queries(
    dba: str,
    website: str,
    owners: list[str] | None,
    *,
    domain_root: str | None = None,
    aka_names: str | None = None,
    tags: str | None = None,
):
    """Construct focused queries for Google CSE."""
    # Names list: DBA + AKA (aliases)
    names = []
    if dba:
        names.append(dba)
    if aka_names:
        names.extend([a.strip() for a in aka_names.split(",") if a.strip()])
    # de-dupe, keep order
    seen = set(); names = [n for n in names if not (n in seen or seen.add(n))]

    # Get a clean host root
    root = (domain_root or (website or "")
            .lower().replace("https://","").replace("http://","")
            .replace("www.","").strip("/"))

    qs = []
    # Site-centric
    if root:
        qs.append(f'site:{root} (launch OR announces OR announcement OR product OR release OR funding OR raised OR partners OR integrates)')
        qs.append(f'site:{root} blog')
        qs.append(f'site:{root} press')

    # Name-centric
    for n in names[:2]:
        qs.append(f'"{n}" (launch OR product OR release OR funding OR partners OR integrates)')

    # Exec/owner mentions (lightly)
    for p in (owners or [])[:2]:
        if p:
            if names:
                qs.append(f'"{p}" "{names[0]}"')
            elif dba:
                qs.append(f'"{p}" "{dba}"')
            else:
                qs.append(f'"{p}"')

    # Industry tags (optional boost)
    if tags and (names or dba):
        pivot = names[0] if names else dba
        qs.append(f'{pivot} {tags} news')

    # Clean
    return [q for q in qs if q and len(q) > 3]

# ---------- Sources ----------
def try_rss_feeds(website: str):
    cand = []
    if not website: return cand
    w = website.rstrip("/")
    for path in ["", "/blog", "/news", "/press", "/updates", "/stories"]:
        for rss in ["/feed", "/rss", "/rss.xml", "/index.xml", "/atom.xml"]:
            cand.append(w + path + rss)
    out = []
    for url in cand:
        try:
            d = feedparser.parse(url)
            if d.bozo:  # not a feed
                continue
            for e in d.entries[:10]:
                title = getattr(e, "title", None)
                link = getattr(e, "link", None)
                if not link or not title: continue
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
                    "snippet": getattr(e, "summary", None)
                })
        except Exception:
            continue
    return out

def google_cse_search(api_key: str, cse_id: str, query: str, date_restrict: str = "d7", num: int = 5):
    # docs: Custom Search JSON API
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
        if not link or not title: continue
        out.append({
            "title": title,
            "url": link,
            "source": domain_from_url(link) or item.get("displayLink"),
            "published_at": datetime.utcnow(),  # CSE rarely gives exact pubdate; treat as current
            "snippet": snippet
        })
    return out

# ---------- (Optional) OpenAI summarizer ----------
def summarize_text(texts: list[str]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fallback: first 2 lines
        return "\n".join([t.strip() for t in texts if t][:2])[:600]
    try:
        import openai  # requires openai>=1.x if you add it to requirements
        client = openai.OpenAI(api_key=api_key)
        prompt = "Summarize the following updates into 2–4 short bullets with clear, plain language:\n\n" + "\n\n".join(texts[:8])
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            messages=[{"role":"user","content":prompt}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "\n".join([t.strip() for t in texts if t][:2])[:600]

# ---------- HTML (tiny inline template) ----------
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
      {% if org.items %}
        <table>
          <thead><tr><th>Title</th><th>Source</th></tr></thead>
          <tbody>
          {% for it in org.items %}
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
        <div style="margin-top:8px;"><strong>Summary</strong><div>{{ org.summary|replace('\n','<br>') }}</div></div>
      {% endif %}
    </div>
  {% endfor %}

  <div class="section muted">— End of report</div>
</body></html>
""")

def main():
    # --- Config / inputs ---
    lookback_days = int(getenv("INTEL_LOOKBACK_DAYS", "10"))
    max_per_org   = int(getenv("INTEL_MAX_PER_ORG", "5"))
    g_api_key = getenv("GOOGLE_API_KEY")
    g_cse_id  = getenv("GOOGLE_CSE_ID")
    profile_subject = getenv("PROFILE_SUBJECT", "Alert: Will Accounts Demographics has results")

    # Gmail service
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = getenv("GMAIL_USER") or os.environ["GMAIL_USER"]

    # --- Load latest weekly diff CSV (same flow you already use) ---
    msgs = search_messages(svc, user, getenv("GMAIL_QUERY"), max_results=5)
    df = None
    for m in msgs:
        msg = get_message(svc, user, m["id"])
        atts = extract_csv_attachments(svc, user, msg, getenv("ATTACHMENT_REGEX", r".*\.csv$"))
        if not atts:
            continue
        name, data = atts[0]
        import io, pandas as pd
        df = pd.read_csv(io.BytesIO(data))
        break
    if df is None:
        raise SystemExit("No weekly CSV found via Gmail. Adjust GMAIL_QUERY or wait for the Metabase email.")

    # --- Load Org Profile CSV by subject (optional but recommended) ---
    df_profile = fetch_csv_by_subject(svc, user, profile_subject)

    # Build a profile lookup by callsign
    prof = {}
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

    # --- Build org list by merging weekly CSV rows with profile fields (by callsign) ---
    cols = {c.lower().strip(): c for c in df.columns}
    def col(k): return cols.get(k)

    orgs = []
    seen_cs = set()
    for _, r in df.iterrows():
        cs = (r.get(col("callsign")) or "").strip()
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
            # Overlay profile (prefer non-empty profile values)
            for k, v in prof[cs].items():
                if k == "beneficial_owners" and v:
                    base["owners"] = [s.strip() for s in str(v).split(",") if s.strip()]
                elif v not in (None, "", []):
                    base[k] = v
        orgs.append(base)

    # --- Fetch intel per org ---
    enriched = []
    for org in orgs:
        items = []

        # 1) Official RSS/blog: prefer explicit blog_url; else use website
        site_for_rss = org.get("blog_url") or org.get("website")
        if site_for_rss:
            items += try_rss_feeds(site_for_rss)

        # 2) Google CSE (if configured)
        if g_api_key and g_cse_id:
            queries = build_queries(
                org.get("dba"),
                org.get("website"),
                org.get("owners"),
                domain_root=org.get("domain_root"),
                aka_names=org.get("aka_names"),
                tags=org.get("industry_tags"),
            )
            for q in queries:
                try:
                    items += google_cse_search(
                        g_api_key, g_cse_id, q,
                        date_restrict=f"d{lookback_days}",
                        num=max_per_org
                    )
                except Exception:
                    # Skip on per-query errors and keep going
                    continue

        # De-duplicate and prune by lookback
        items = dedupe(items, key=lambda x: x["url"])
        items = [x for x in items if within_days(x.get("published_at", datetime.utcnow()), lookback_days)]
        items = items[:max_per_org]

        # Optional summary (OpenAI if key present; else simple fallback inside summarize_text)
        summary = ""
        if items:
            texts = [f"- {it['title']} — {it.get('source')}" for it in items]
            summary = summarize_text(texts)

        enriched.append({
            "callsign": org["callsign"],
            "dba": org.get("dba"),
            "items": items,
            "summary": summary
        })

    # --- Optional: filter to a few callsigns for testing
    only = getenv("FILTER_CALLSIGNS")
    if only:
        want = {c.strip().lower() for c in only.split(",") if c.strip()}
        orgs = [o for o in orgs if o.get("callsign","").lower() in want]

    # --- Render HTML and either email or just print (preview mode)
html = INTEL_TEMPLATE.render(
    today=now_utc_date(),
    lookback_days=lookback_days,
    orgs=enriched,
)

if getenv("PREVIEW_ONLY", "false").lower() in ("1","true","yes","y"):
    # print first 50 lines to logs so you can eyeball it in Actions
    print("\n--- HTML PREVIEW (truncated) ---")
    print("\n".join(html.splitlines()[:200]))
    print("\n--- END PREVIEW ---")
    return
    
    # --- Render HTML and send email ---
    html = INTEL_TEMPLATE.render(
        today=now_utc_date(),
        lookback_days=lookback_days,
        orgs=enriched,
    )

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
