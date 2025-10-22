# app/news_job.py
"""Legacy news collection CLI and workflow entry-point."""

from __future__ import annotations

import functools
import io
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import feedparser
import pandas as pd
import requests
import tldextract

from app.core.config import IntelligenceConfig, NotionConfig
from app.core.models import Company
from app.data.csv_parser import filter_dataframe_by_relationship_manager
from app.data.notion_client import EnhancedNotionClient
from app.gmail_client import (
    build_service,
    extract_csv_attachments,
    get_message,
    search_messages,
    send_html_email,
)
from app.intelligence.models import NewsItem, NewsType
from app.intelligence.news_quality import NewsQualityScorer
from app.intelligence.news_verifier import LLMNewsVerifier
from app.intelligence.seen_store import NotionNewsSeenStore
from app.notion_client import (
    get_all_companies_domain_data,
    get_dossier_text,
    set_latest_intel,
    upsert_company_page,
)
from app.performance_utils import (
    DEFAULT_RATE_LIMITER,
    PERFORMANCE_MONITOR,
    ConcurrentAPIClient,
    ParallelProcessor,
    should_skip_processing,
)

# ---------------- Notion helpers for new company detection ----------------

NOTION_API = "https://api.notion.com/v1"
logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_quality_scorer() -> NewsQualityScorer:
    """Return shared quality scorer for news weighting."""
    return NewsQualityScorer(IntelligenceConfig())


@functools.lru_cache(maxsize=1)
def get_news_verifier() -> LLMNewsVerifier:
    """Return shared LLM verifier instance."""
    return LLMNewsVerifier()


@functools.lru_cache(maxsize=256)
def get_cached_dossier(page_id: str) -> Optional[str]:
    """Return cached dossier text for a Notion page."""
    if not page_id:
        return None
    try:
        return get_dossier_text(page_id) or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to load dossier text", page_id=page_id, error=str(exc))
        return None


_CORPORATE_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "l.l.c.",
    "corp",
    "corp.",
    "co",
    "co.",
    "company",
    "ltd",
    "ltd.",
    "incorporated",
    "corporation",
}


def get_notion_headers():
    """Get Notion API headers with current environment configuration."""
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": os.getenv("NOTION_VERSION", "2022-06-28"),
        "Content-Type": "application/json",
    }


def _dash32(x: str) -> str:
    return re.sub(r"^(.{8})(.{4})(.{4})(.{4})(.{12})$", r"\1-\2-\3-\4-\5", re.sub(r"-", "", x))


def _companies_title_prop(companies_db_id: str) -> str:
    r = requests.get(
        f"{NOTION_API}/databases/{_dash32(companies_db_id)}",
        headers=get_notion_headers(),
        timeout=30,
    )
    r.raise_for_status()
    props = r.json().get("properties", {})
    for k, v in props.items():
        if v.get("type") == "title":
            return k
    return "Name"


def _find_company_page(companies_db_id: str, title_prop: str, callsign: str) -> Optional[str]:
    q = {"filter": {"property": title_prop, "title": {"equals": callsign}}}
    r = requests.post(
        f"{NOTION_API}/databases/{_dash32(companies_db_id)}/query",
        headers=get_notion_headers(),
        json=q,
        timeout=30,
    )
    r.raise_for_status()
    res = r.json().get("results", [])
    return res[0]["id"] if res else None


def _set_needs_dossier(page_id: str, needs: bool = True):
    """Set the 'Needs Dossier' checkbox property for a company page."""
    props = {"Needs Dossier": {"checkbox": needs}}
    r = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=get_notion_headers(),
        json={"properties": props},
        timeout=30,
    )
    r.raise_for_status()


def ensure_company_page(
    companies_db_id: str,
    callsign: str,
    website: Optional[str] = None,
    domain: Optional[str] = None,
    company: Optional[str] = None,
) -> tuple[str, bool]:
    """Return `(page_id, created_flag)` after ensuring a company page exists."""
    title_prop = _companies_title_prop(companies_db_id)
    pid = _find_company_page(companies_db_id, title_prop, callsign)
    created = False

    props = {title_prop: {"title": [{"type": "text", "text": {"content": callsign[:200]}}]}}
    # We write extra props only if they exist in schema (URL or rich_text)
    schema = (
        requests.get(
            f"{NOTION_API}/databases/{_dash32(companies_db_id)}",
            headers=get_notion_headers(),
            timeout=30,
        )
        .json()
        .get("properties", {})
    )

    if company and schema.get("Company", {}).get("type") == "rich_text":
        props["Company"] = {"rich_text": [{"type": "text", "text": {"content": company[:1000]}}]}
    if website and schema.get("Website", {}).get("type") == "url":
        props["Website"] = {"url": website}
    if domain:
        if schema.get("Domain", {}).get("type") == "url":
            props["Domain"] = {"url": f"https://{domain}"}
        elif schema.get("Domain", {}).get("type") == "rich_text":
            props["Domain"] = {"rich_text": [{"type": "text", "text": {"content": domain}}]}

    if pid:
        requests.patch(
            f"{NOTION_API}/pages/{pid}",
            headers=get_notion_headers(),
            json={"properties": props},
            timeout=30,
        ).raise_for_status()
    else:
        r = requests.post(
            f"{NOTION_API}/pages",
            headers=get_notion_headers(),
            json={
                "parent": {"database_id": _dash32(companies_db_id)},
                "properties": props,
            },
            timeout=30,
        )
        r.raise_for_status()
        pid = r.json()["id"]
        created = True

    return pid, created


# ---------------- Gmail CSV fetch ----------------


def fetch_csv_by_subject(service, user: str, subject: str) -> Optional[pd.DataFrame]:
    """Find the newest email with subject containing <subject> and a CSV attachment."""
    if not subject:
        return None
    msgs = search_messages(
        service, user, f'subject:"{subject}" has:attachment filename:csv', max_results=5
    )
    relationship_manager = os.getenv("RELATIONSHIP_MANAGER_NAME", "Will Mitchell")

    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, r".*\.csv$")
        if atts:
            _, data = atts[0]
            try:
                df = pd.read_csv(io.BytesIO(data))
                df = filter_dataframe_by_relationship_manager(df, relationship_manager)
                if df.empty:
                    logger.warning(
                        "CSV attachment contained no rows after relationship manager filter",
                        message_id=m["id"],
                        relationship_manager=relationship_manager,
                    )
                    continue
                return df
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse CSV attachment", error=str(exc))
                continue
    return None


# ---------------- Query building ----------------


def _prepare_query_sets(company: Company) -> Dict[str, Any]:
    """Return grouped query variants plus metadata for a company."""
    scorer = get_quality_scorer()

    def strip_suffix(name: str) -> str:
        tokens = [t for t in re.split(r"\s+", name.strip()) if t]
        while tokens:
            suffix = tokens[-1].rstrip(",.").lower()
            if suffix in _CORPORATE_SUFFIXES:
                tokens.pop()
                continue
            break
        return " ".join(tokens)

    raw_names: List[str] = []
    if company.callsign:
        raw_names.append(company.callsign)
    if company.dba:
        raw_names.append(company.dba)
    if company.callsign:
        raw_names.append(company.callsign.upper())
    if company.aka_names:
        raw_names.extend([n.strip() for n in company.aka_names.split(",") if n.strip()])

    names: List[str] = []
    seen_names = set()
    for value in raw_names:
        if not value:
            continue
        cleaned = strip_suffix(value)
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        names.append(cleaned)

    if not names and company.callsign:
        names.append(company.callsign)

    domains: List[str] = []
    if company.domain_root:
        domains.append(company.domain_root)
    if company.website:
        w = re.sub(r"^https?://", "", company.website.strip().lower())
        w = re.sub(r"^www\.", "", w).split("/")[0]
        ext = tldextract.extract(w)
        if ext.registered_domain:
            domains.append(ext.registered_domain)

    domains = [d.lower() for d in domains if d]
    site_scopes = scorer.company_site_scopes(company)
    all_queries = scorer.build_query_variants(company, domains, names, site_scopes)

    scope_tokens = {scope.lower() for scope in site_scopes}
    scope_hosts = set()
    for scope in site_scopes:
        host = scope.split("/", 1)[0].lower()
        if host.startswith("www."):
            host = host[4:]
        scope_hosts.add(host)

    domain_set = set(domains)

    external_queries: List[str] = []
    owned_queries: List[str] = []
    for query in all_queries:
        q = query.strip()
        is_owned = False
        if q.lower().startswith("site:"):
            token = q[5:].split()[0].lower()
            host = token.split("/", 1)[0]
            if host.startswith("www."):
                host = host[4:]
            if token in scope_tokens or host in scope_hosts or host in domain_set:
                is_owned = True
        if is_owned:
            owned_queries.append(q)
        else:
            external_queries.append(q)

    return {
        "all": all_queries,
        "external": external_queries,
        "owned": owned_queries,
        "domains": domain_set,
        "scope_hosts": scope_hosts,
    }


def build_queries(
    callsign: str,
    dba: Optional[str],
    website: Optional[str],
    owners: Optional[List[str]],
    domain_root: Optional[str] = None,
    aka_names: Optional[str] = None,
    tags: Optional[str] = None,
    blog_url: Optional[str] = None,
    include_owned: bool = True,
) -> List[str]:
    """Construct search queries tailored to a company.

    Args:
        include_owned: When False, omit site-scoped queries for company-controlled domains.
    """
    company = Company(
        callsign=(callsign or dba or "unknown"),
        dba=dba,
        website=website,
        domain_root=domain_root,
        blog_url=blog_url,
        beneficial_owners=owners or [],
        aka_names=aka_names,
        industry_tags=tags,
    )

    query_sets = _prepare_query_sets(company)
    external_set = set(query_sets["external"])
    owned_set = set(query_sets["owned"])

    ordered: List[str] = []
    for q in query_sets["all"]:
        if q in external_set:
            ordered.append(q)
        elif include_owned and q in owned_set:
            ordered.append(q)
    return ordered


# ---------------- RSS / Feeds ----------------


def _try_feed(url: str) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Try to fetch RSS feed and return items plus successful feed URL."""
    out: List[Dict[str, Any]] = []
    fp = feedparser.parse(url)
    successful_feed_url = None

    if fp.entries:  # Only consider successful if we got entries
        successful_feed_url = url
        for e in fp.entries[:10]:
            title = getattr(e, "title", "") or ""
            link = getattr(e, "link", "") or ""
            date = ""
            for key in ("published", "updated"):
                if hasattr(e, key):
                    date = getattr(e, key) or ""
                    break
            summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
            out.append(
                {
                    "title": title,
                    "url": link,
                    "source": "",
                    "published_at": date,
                    "summary": summary,
                }
            )

    return out, successful_feed_url


def try_rss_feeds(site: Optional[str]) -> tuple[List[Dict[str, Any]], List[str]]:
    """Try RSS feeds and return items plus list of successful feed URLs."""
    if not site:
        return [], []
    site = str(site).strip()
    cand = []
    if site.startswith("http"):
        base = site.rstrip("/")
        cand = [f"{base}/feed", f"{base}/rss", base]
    else:
        cand = [f"https://{site}/feed", f"https://{site}/rss", f"https://{site}"]

    items: List[Dict[str, Any]] = []
    successful_feeds: List[str] = []

    for u in cand:
        try:
            feed_items, successful_url = _try_feed(u)
            items.extend(feed_items)
            if successful_url:
                successful_feeds.append(successful_url)
                # Stop after first successful feed to avoid duplicates
                break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch RSS feed", url=u, error=str(exc))
            continue
    return items, successful_feeds


# ---------------- Google CSE ----------------


def google_cse_search(
    api_key: str,
    cse_id: str,
    q: str,
    date_restrict: Optional[str] = None,
    num: int = 5,
    exclude_domains: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Query Google CSE and return simplified result dictionaries."""
    if not (api_key and cse_id and q):
        return []
    query = q
    if exclude_domains:
        exclusions = []
        for domain in exclude_domains:
            token = (domain or "").strip()
            if not token:
                continue
            clause = f"-site:{token}"
            if clause in query:
                continue
            exclusions.append(clause)
        if exclusions:
            query = f"{query} {' '.join(exclusions)}".strip()

    params = {"key": api_key, "cx": cse_id, "q": query, "num": min(10, max(1, num))}
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
        out.append(
            {
                "title": title,
                "url": link,
                "source": "",  # normalized later
                "published_at": dt or snippet,  # fallback: snippet may contain date-ish text
                "summary": snippet,
            }
        )
    return out


# ---------------- Utilities ----------------


def dedupe(
    items: List[Dict[str, Any]], key: Callable[[Dict[str, Any]], Any]
) -> List[Dict[str, Any]]:
    """Deduplicate items based on the value returned by `key`."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        k = key(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _dict_to_news_item(data: Dict[str, Any], callsign: str) -> NewsItem:
    """Convert stored dictionary data into a `NewsItem`."""
    url = (data.get("url") or "").strip()
    source = (data.get("source") or "").strip()
    if not source and url:
        ext = tldextract.extract(url)
        source = ext.registered_domain or ext.domain or ""

    news_type_value = data.get("news_type") or data.get("type")
    try:
        news_type = NewsType(news_type_value)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Falling back to default news type", value=news_type_value, error=str(exc))
        news_type = NewsType.OTHER_NOTABLE

    summary = data.get("summary")
    if isinstance(summary, dict):
        summary = summary.get("text") or summary.get("content")

    return NewsItem(
        title=(data.get("title") or "").strip() or url,
        url=url,
        source=source,
        published_at=(data.get("published_at") or "").strip(),
        summary=summary,
        news_type=news_type,
        relevance_score=float(data.get("relevance_score") or 0.0),
        sentiment=(data.get("sentiment") or None),
        company_mentions=[callsign.upper()],
        llm_verdict=data.get("llm_verdict") or None,
    )


def _news_item_to_link(item: NewsItem) -> str:
    """Return a markdown bullet linking to the news item."""
    return f"• [{item.title}]({item.url})" if item.url else f"• {item.title}"


def _news_item_to_dict(item: NewsItem) -> Dict[str, Any]:
    """Convert a `NewsItem` back into the dictionary format used in caches."""
    return {
        "title": item.title,
        "url": item.url,
        "source": item.source,
        "published_at": item.published_at,
        "news_type": item.news_type.value,
        "relevance_score": item.relevance_score,
        "sentiment": item.sentiment,
        "summary": item.summary,
        "llm_verdict": item.llm_verdict,
    }


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
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Failed to parse date for recency comparison",
            value=published_at,
            error=str(exc),
        )
        return True


# ---------- Normalization helpers (date, source, url, title) ----------


def _source_from_url(url: str | None) -> str:
    """Extract a reasonable source name from a URL."""
    if not url:
        return ""
    ext = tldextract.extract(url)
    return (
        ext.registered_domain
        or (f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else "")
        or ""
    )


def _iso_date(dt_or_str) -> str:
    """Normalize various date formats to YYYY-MM-DD strings."""
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
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to normalise date", value=s, error=str(exc))
    return s


def normalize_news_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize raw search results into consistent dictionaries."""
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
                "summary": (it.get("summary") or "").strip(),
            }
        )
    return out


# ---------------- Collection ----------------


def collect_recent_news(
    org: Dict[str, Any],
    lookback_days: int,
    g_api_key: Optional[str],
    g_cse_id: Optional[str],
    max_items: int = 6,
    max_queries: int = 5,
) -> tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """Collect news items and return tuple of (items, source_metadata).

    source_metadata format:
    {
        "rss_feeds": ["https://company.com/feed", "https://blog.company.com/rss"],
        "search_queries": ["Company Name partnership"],
        "owned_queries": ["site:company.com/news"]
    }
    """
    source_metadata: Dict[str, List[str]] = {
        "rss_feeds": [],
        "search_queries": [],
        "owned_queries": [],
    }

    company_model = Company(
        callsign=(org.get("callsign") or org.get("dba") or "unknown"),
        dba=org.get("dba"),
        website=org.get("website"),
        domain_root=org.get("domain_root"),
        blog_url=org.get("blog_url"),
        beneficial_owners=org.get("owners") or [],
        aka_names=org.get("aka_names"),
        industry_tags=org.get("industry_tags"),
    )

    owned_items: List[Dict[str, Any]] = []
    external_items: List[Dict[str, Any]] = []

    # RSS/blog (prefer explicit blog_url; else website)
    site_for_rss = org.get("blog_url") or org.get("website")
    if site_for_rss:
        try:
            rss_items, successful_feeds = try_rss_feeds(site_for_rss)
            owned_items += rss_items
            source_metadata["rss_feeds"].extend(successful_feeds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RSS collection failed", site=site_for_rss, error=str(exc))

    # Google CSE (site + name queries + optional owners) - CONCURRENT
    disable_cse = str(os.getenv("CSE_DISABLE", "")).lower() in ("1", "true", "yes")
    if (g_api_key and g_cse_id) and not disable_cse:
        query_sets = _prepare_query_sets(company_model)

        limit = int(os.getenv("CSE_MAX_QUERIES_PER_ORG", str(max_queries)) or max_queries)
        executed_external = query_sets["external"][:limit]
        source_metadata["search_queries"] = executed_external

        owned_limit = int(os.getenv("CSE_MAX_OWNED_QUERIES_PER_ORG", "3") or "3")
        executed_owned = query_sets["owned"][:owned_limit]
        if executed_owned:
            source_metadata["owned_queries"] = executed_owned

        # Determine company-controlled domains for exclusion in external searches
        exclude_domains = set(query_sets["domains"])
        for host in query_sets["scope_hosts"]:
            if any(host == dom or host.endswith(f".{dom}") for dom in query_sets["domains"]):
                exclude_domains.add(host)

        api_client = None

        if executed_external:
            api_client = api_client or ConcurrentAPIClient(DEFAULT_RATE_LIMITER)
            external_calls = [
                lambda query=q: google_cse_search(
                    g_api_key,
                    g_cse_id,
                    query,
                    date_restrict=f"d{lookback_days}",
                    num=5,
                    exclude_domains=sorted(exclude_domains) if exclude_domains else None,
                )
                for q in executed_external
            ]
            results = api_client.batch_api_calls(external_calls, max_workers=4, timeout=30)
            for result in results:
                if result:
                    external_items += result

        if executed_owned:
            api_client = api_client or ConcurrentAPIClient(DEFAULT_RATE_LIMITER)
            owned_calls = [
                lambda query=q: google_cse_search(
                    g_api_key,
                    g_cse_id,
                    query,
                    date_restrict=f"d{lookback_days}",
                    num=5,
                )
                for q in executed_owned
            ]
            results = api_client.batch_api_calls(owned_calls, max_workers=4, timeout=30)
            for result in results:
                if result:
                    owned_items += result

    # Score and select external items
    external_items = dedupe(external_items, key=lambda x: x.get("url"))
    external_items = [
        x
        for x in external_items
        if within_days(x.get("published_at", datetime.utcnow()), lookback_days)
    ]
    external_normalized = normalize_news_items(external_items)

    max_candidates = max(max_items * 4, max_items)
    external_filtered = [
        {
            "url": item.url,
            "title": item.title,
            "source": item.source,
            "published_at": item.published_at,
            "summary": item.summary,
        }
        for item in [
            NewsItem(
                title=entry["title"] or entry["url"],
                url=entry["url"],
                source=entry["source"],
                published_at=entry.get("published_at"),
                news_type=NewsType.OTHER_NOTABLE,
                relevance_score=0.0,
                sentiment=None,
                company_mentions=[company_model.callsign.upper()],
                summary=entry.get("summary") or None,
            )
            for entry in external_normalized
        ][:max_candidates]
    ]

    # Always include owned-domain items that are within the lookback window
    owned_items = dedupe(owned_items, key=lambda x: x.get("url"))
    owned_items = [
        x
        for x in owned_items
        if within_days(x.get("published_at", datetime.utcnow()), lookback_days)
    ]
    owned_normalized = normalize_news_items(owned_items)

    existing_urls = {item.get("url") for item in external_filtered if item.get("url")}
    owned_filtered: List[Dict[str, Any]] = []
    for item in owned_normalized:
        url = item.get("url")
        if url and url in existing_urls:
            continue
        owned_filtered.append(item)
        if url:
            existing_urls.add(url)

    owned_filtered = [
        {
            "url": item.get("url"),
            "title": item.get("title"),
            "source": item.get("source"),
            "published_at": item.get("published_at"),
            "summary": item.get("summary"),
        }
        for item in owned_filtered
    ]

    combined_filtered = (external_filtered + owned_filtered)[:max_candidates]

    return combined_filtered, source_metadata


# ---------------- LLM summary (optional) ----------------


def _openai_summarize(text: str) -> Optional[str]:
    """Generate a short summary using OpenAI if credentials are available."""
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
            is_gpt5 = model.lower().startswith("gpt-5")
            if is_gpt5:
                tools = [{"type": "web_search"}]
                kwargs = {
                    "model": model,
                    "input": prompt,
                    "tools": tools,
                    "tool_choice": {"type": "allowed_tools", "mode": "required", "tools": tools},
                }
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
        except Exception as exc:  # noqa: BLE001
            logger.debug("OpenAI summary call failed, retrying", error=str(exc))
            out = try_call(send_temperature=False)
            return (out or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("OpenAI summarisation unavailable", error=str(exc))
        return None


# ---------------- Email digest ----------------


def build_email_digest(intel: Dict[str, List[Dict[str, Any]]]) -> str:
    """Render a simple HTML digest for legacy email workflows."""
    parts = ["<html><body><h2>Weekly Intel</h2>"]
    for cs, items in intel.items():
        parts.append(f"<h3>{cs}</h3><ul>")
        for it in items:
            published = it.get("published_at", "")
            url = it.get("url", "")
            title = it.get("title") or url
            source = it.get("source", "")
            line = f"- {published} — " f'<a href="{url}">{title}</a> — {source}'
            parts.append(f"<li>{line}</li>")
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------- Notion processing ----------------


def process_company_notion_with_data(
    intel_by_cs: Dict[str, List[Dict[str, Any]]],
    source_metadata_by_cs: Dict[str, Dict[str, List[str]]],
    filtered_news_by_cs: Dict[str, List[NewsItem]],
    lookback_days: int,
    companies_db: str,
    intel_db: str,
    news_store: Optional[NotionNewsSeenStore],
    new_company_callsigns: Set[str],
    cs: str,
    org: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Wrap partial binding so the parallel processor can call the function."""
    return process_company_notion(
        cs=cs,
        org=org,
        intel_by_cs=intel_by_cs,
        source_metadata_by_cs=source_metadata_by_cs,
        filtered_news_by_cs=filtered_news_by_cs,
        companies_db=companies_db,
        intel_db=intel_db,
        news_store=news_store,
        lookback_days=lookback_days,
        new_company_callsigns=new_company_callsigns,
    )


def process_company_notion(
    cs: str,
    org: Dict[str, Any],
    intel_by_cs: Dict[str, List[Dict[str, Any]]],
    source_metadata_by_cs: Dict[str, Dict[str, List[str]]],
    filtered_news_by_cs: Dict[str, List[NewsItem]],
    companies_db: str,
    intel_db: str,
    news_store: Optional[NotionNewsSeenStore],
    lookback_days: int,
    new_company_callsigns: Set[str],
) -> Optional[Dict[str, Any]]:
    """Process Notion updates for a single company."""
    intel_items = intel_by_cs.get(cs, [])
    prefiltered_items = filtered_news_by_cs.get(cs)

    if not intel_items and prefiltered_items is None:
        return None

    try:
        # Ensure org is a dict (safety check)
        if not isinstance(org, dict):
            return {"status": "error", "error": f"Invalid org data type: {type(org)}"}

        # Upsert company page first (without domain/website to avoid overwriting baseline job data)
        needs_dossier_flag = cs.lower() in new_company_callsigns
        page_id = upsert_company_page(
            companies_db,
            {
                "callsign": org.get("callsign") or cs,  # Use cs as fallback
                "company": (
                    str(org.get("dba") or "").strip() if org.get("dba") is not None else ""
                ),
                "owners": org.get("owners") or [],
                "needs_dossier": needs_dossier_flag,
            },
        )

        # LLM summary (optional)
        company_callsign = str(org.get("callsign") or cs)
        if prefiltered_items is not None:
            collected_items = prefiltered_items
        else:
            collected_items = [_dict_to_news_item(item, company_callsign) for item in intel_items]

        if news_store:
            if collected_items:
                summary_items, _ = news_store.ingest(
                    company_callsign,
                    page_id,
                    collected_items,
                )
            else:
                print(f"[NO-UPDATE] {cs} no new intel items; skipping archive")
                summary_items = []
        else:
            summary_items = collected_items

        if summary_items:
            text_blob = "\n".join(
                f"{item.published_at} — {item.title} — {item.source} {item.url}"
                for item in summary_items
            )
            ai_summary = _openai_summarize(text_blob) or f"{len(summary_items)} new items."
            source_links = [
                _news_item_to_link(item) for item in summary_items if item.title and item.url
            ]
            if source_links:
                summary = ai_summary + "\n\nSources:\n" + "\n".join(source_links)
            else:
                summary = ai_summary
            should_update_latest_intel = True
        else:
            summary = ""
            should_update_latest_intel = False

        # Set Latest Intel + update Intel archive with new system
        if should_update_latest_intel:
            today_iso = datetime.utcnow().date().isoformat()
            latest_intel_date = today_iso if summary else None

            # Keep slim "Latest Intel" on Companies DB
            try:
                DEFAULT_RATE_LIMITER.wait_if_needed()
                set_latest_intel(
                    page_id,
                    summary_text=summary,
                    date_iso=latest_intel_date,
                    companies_db_id=companies_db,
                )
            except Exception as e:
                print(f"WARN set_latest_intel for {cs}: {e}")
        else:
            print(f"[NO-UPDATE] {cs} leaving Latest Intel unchanged (no new items)")

        return {"status": "success", "items": len(intel_items)}

    except Exception as e:
        print(f"[NOTION ERROR] Failed to process {cs}: {e}")
        return {"status": "error", "error": str(e)}


# ---------------- Main job ----------------


def main():
    """Run the legacy news workflow using environment configuration."""
    # Gmail
    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = os.getenv("GMAIL_USER") or ""

    # Inputs / knobs
    filter_callsigns = [
        c.strip().lower() for c in (os.getenv("FILTER_CALLSIGNS") or "").split(",") if c.strip()
    ]
    lookback_days = int(os.getenv("INTEL_LOOKBACK_DAYS", "10") or "10")
    max_per_org = int(os.getenv("INTEL_MAX_PER_ORG", "5") or "5")
    preview_only = str(os.getenv("PREVIEW_ONLY", "true")).lower() in (
        "1",
        "true",
        "yes",
        "y",
    )

    g_api_key = os.getenv("GOOGLE_API_KEY")
    g_cse_id = os.getenv("GOOGLE_CSE_ID")

    companies_db = os.getenv("NOTION_COMPANIES_DB_ID")
    token = os.getenv("NOTION_API_KEY")
    intel_db = os.getenv("NOTION_INTEL_DB_ID")
    notion_client: Optional[EnhancedNotionClient] = None
    news_store: Optional[NotionNewsSeenStore] = None
    if token and companies_db and intel_db:
        try:
            notion_config = NotionConfig()
            notion_client = EnhancedNotionClient(notion_config)
            news_store = NotionNewsSeenStore(notion_client, intel_db, companies_db)
        except Exception as exc:
            print(f"[WARN] Failed to initialize Notion news store: {exc}")
            news_store = None
            notion_client = None

    # Pull roster CSV (subject configured via env)
    profile_subject = os.getenv("NEWS_PROFILE_SUBJECT") or "Org Profile — Will Mitchell"
    df = fetch_csv_by_subject(svc, user, profile_subject)
    if df is None:
        print("No profile CSV found by subject; exiting.")
        return

    # Build roster dict
    def lower_cols(df):
        return {c.lower().strip(): c for c in df.columns}

    def safe_str(val):
        """Safely convert value to string, handling NaN and None."""
        if (
            val is None
            or (hasattr(val, "__name__") and val.__name__ == "nan")
            or str(val).lower() == "nan"
        ):
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
            "callsign": (safe_str(r[cols.get("callsign")]) if cols.get("callsign") in r else cs),
            "dba": (
                safe_str(r[cols.get("dba")])
                if cols.get("dba") in r and r[cols.get("dba")] is not None
                else None
            ),
            "website": (
                safe_str(r[cols.get("website")])
                if cols.get("website") in r and r[cols.get("website")] is not None
                else None
            ),
            "domain_root": (
                safe_str(r[cols.get("domain_root")])
                if cols.get("domain_root") in r and r[cols.get("domain_root")] is not None
                else None
            ),
            "owners": [
                s.strip()
                for s in safe_str(
                    r[cols.get("beneficial_owners")] if cols.get("beneficial_owners") in r else ""
                ).split(",")
                if s.strip()
            ],
            "aka_names": (
                safe_str(r[cols.get("aka_names")])
                if cols.get("aka_names") in r and r[cols.get("aka_names")] is not None
                else None
            ),
            "industry_tags": (
                safe_str(r[cols.get("industry_tags")])
                if cols.get("industry_tags") in r and r[cols.get("industry_tags")] is not None
                else None
            ),
            "blog_url": (
                safe_str(r[cols.get("blog_url")])
                if cols.get("blog_url") in r and r[cols.get("blog_url")] is not None
                else None
            ),
        }

    # Detect new companies and flag for baseline generation
    new_callsigns: List[str] = []
    new_callsigns_set: Set[str] = set()

    if companies_db:
        for cs, org in roster.items():
            callsign = str(org.get("callsign") or "").strip()
            dba = str(org.get("dba") or "").strip() or None
            website = str(org.get("website") or "").strip() or None
            domain = str(org.get("domain_root") or "").strip() or None

            if callsign:
                try:
                    page_id, created = ensure_company_page(
                        companies_db,
                        callsign,
                        website=website,
                        domain=domain,
                        company=dba,
                    )
                    if created:
                        new_callsigns.append(callsign)
                        new_callsigns_set.add(callsign.lower())
                        # Set needs_dossier flag for new companies
                        try:
                            _set_needs_dossier(page_id, True)
                            print(f"[NEW COMPANY] Created and flagged for baseline: {callsign}")
                        except Exception as e:
                            print(f"[ERROR] Failed to set needs_dossier for {callsign}: {e}")
                except Exception as e:
                    print(f"[ERROR] Failed to ensure company page for {callsign}: {e}")

    if new_callsigns:
        displayed = ", ".join(new_callsigns[:8])
        if len(new_callsigns) > 8:
            displayed += " ..."
        print(f"[NEW COMPANIES] Created {len(new_callsigns)} new company pages: {displayed}")

        # Write new callsigns to trigger baseline generation
        trigger_path = Path(tempfile.gettempdir()) / "new_callsigns.txt"
        try:
            trigger_path.write_text(",".join(new_callsigns), encoding="utf-8")
            trigger_msg = (
                "[TRIGGER] Wrote "
                f"{len(new_callsigns)} new callsigns to {trigger_path} "
                "for baseline generation"
            )
            print(trigger_msg)
        except Exception as e:
            print(f"[ERROR] Failed to write new callsigns trigger file: {e}")
    else:
        # Ensure set is initialised even when Notion is unavailable
        new_callsigns_set = set()

    # Fetch canonical domain data from Notion for all companies (batched for efficiency)
    PERFORMANCE_MONITOR.start_timer("notion_domain_fetch")
    notion_domain_data = {}
    if companies_db:
        print(f"[NOTION] Fetching canonical domain data for {len(roster)} companies...")
        try:
            callsigns = list(roster.keys())
            notion_domain_data = get_all_companies_domain_data(companies_db, callsigns)
            found_domains = sum(
                1
                for data in notion_domain_data.values()
                if data.get("domain") or data.get("website")
            )
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

        verified_domain = notion_domains.get("verified_domain")
        if verified_domain:
            if enhanced_org.get("domain_root") != verified_domain:
                domain_sources.append(
                    "domain: CSV '{csv}' → Notion verified '{notion}'".format(
                        csv=enhanced_org.get("domain_root"), notion=verified_domain
                    )
                )
            enhanced_org["domain_root"] = verified_domain

        elif notion_domains.get("domain"):
            if enhanced_org.get("domain_root") != notion_domains["domain"]:
                domain_sources.append(
                    "domain: CSV '{csv}' → Notion '{notion}'".format(
                        csv=enhanced_org.get("domain_root"),
                        notion=notion_domains["domain"],
                    )
                )
            enhanced_org["domain_root"] = notion_domains["domain"]

        if notion_domains.get("website"):
            if enhanced_org.get("website") != notion_domains["website"]:
                domain_sources.append(
                    "website: CSV '{csv}' → Notion '{notion}'".format(
                        csv=enhanced_org.get("website"),
                        notion=notion_domains["website"],
                    )
                )
            enhanced_org["website"] = notion_domains["website"]

        if domain_sources and os.getenv("DEBUG"):
            print(f"[DOMAIN] {cs}: {'; '.join(domain_sources)}")

        items, source_metadata = collect_recent_news(
            enhanced_org, lookback_days, g_api_key, g_cse_id, max_items=max_per_org
        )
        return cs, {"items": items, "source_metadata": source_metadata}

    # Process companies in parallel
    print(f"[PARALLEL] Processing {len(roster)} companies for news collection...")

    results = ParallelProcessor.process_dict_batch(
        roster,
        collect_news_for_company,
        max_workers=6,  # Conservative for API rate limits
        timeout=300,  # 5 minutes total
    )

    intel_by_cs: Dict[str, List[Dict[str, Any]]] = {}
    source_metadata_by_cs: Dict[str, Dict[str, List[str]]] = {}
    filtered_news_by_cs: Dict[str, List[NewsItem]] = {}

    for cs in roster.keys():
        result = results.get(cs, {"items": [], "source_metadata": {}})

        items_list: List[Dict[str, Any]]
        source_metadata: Dict[str, List[str]]

        if isinstance(result, tuple) and len(result) == 2:
            # Handle tuple return format (cs, data)
            returned_cs, data = result
            if isinstance(data, dict):
                items_list = data.get("items", []) or []
                source_metadata = data.get("source_metadata", {}) or {}
            else:
                # Data is a list (old cached format)
                items_list = data if isinstance(data, list) else []
                source_metadata = {}
        elif isinstance(result, dict):
            items_list = result.get("items", []) or []
            source_metadata = result.get("source_metadata", {}) or {}
        else:
            # Backwards compatibility: if result is just a list (old format)
            items_list = result if isinstance(result, list) else []
            source_metadata = {}

        source_metadata_by_cs[cs] = source_metadata

        org = roster.get(cs, {})
        company_callsign = str(org.get("callsign") or cs or "").strip() or cs
        candidate_items = [_dict_to_news_item(item, company_callsign) for item in items_list]

        notion_meta = notion_domain_data.get(cs) or {}
        page_id = notion_meta.get("page_id")
        dossier_text = get_cached_dossier(page_id) if page_id else None

        company_context = {
            "dba": org.get("dba"),
            "company": org.get("company"),
            "aka_names": org.get("aka_names"),
            "owners": org.get("owners"),
            "website": org.get("website"),
            "domain_root": org.get("domain_root"),
            "tags": org.get("industry_tags"),
        }

        if notion_meta.get("verified_domain"):
            company_context["domain_root"] = notion_meta.get("verified_domain")
        elif notion_meta.get("domain"):
            company_context["domain_root"] = notion_meta.get("domain")
        if notion_meta.get("website"):
            company_context["website"] = notion_meta.get("website")

        verifier = get_news_verifier()
        accepted_items, rejected_items = verifier.filter_items(
            company_callsign=company_callsign,
            items=candidate_items,
            dossier_text=dossier_text,
            company_context=company_context,
        )

        if rejected_items:
            logger.info(
                "LLM rejected news items",
                callsign=company_callsign,
                rejected=len(rejected_items),
            )

        if len(accepted_items) > max_per_org:
            accepted_items = accepted_items[:max_per_org]

        if news_store:
            new_items, existing_items = news_store.filter_new_items(
                company_callsign,
                accepted_items,
            )
            filtered_news_by_cs[cs] = new_items
            if existing_items:
                print(f"[DEDUP] {cs} skipped {len(existing_items)} archived items")
            intel_by_cs[cs] = [_news_item_to_dict(item) for item in new_items]
        else:
            intel_by_cs[cs] = [_news_item_to_dict(item) for item in accepted_items]

    collection_time = PERFORMANCE_MONITOR.end_timer("intel_collection")
    print(f"[PERFORMANCE] Intel collection completed in {collection_time:.2f}s")

    # Notion (optional) - PARALLEL PROCESSING
    if token and companies_db and intel_db:
        PERFORMANCE_MONITOR.start_timer("notion_updates")

        if news_store is None:
            try:
                notion_config = NotionConfig()
                notion_client = EnhancedNotionClient(notion_config)
                news_store = NotionNewsSeenStore(notion_client, intel_db, companies_db)
            except Exception as exc:
                print(f"[WARN] Failed to initialize Notion news store: {exc}")
                news_store = None

        # Create bound function with all data - NO nested functions!
        print("[DEBUG MAIN] Creating bound function with:")
        print(f"  intel_by_cs: {type(intel_by_cs)} with {len(intel_by_cs)} companies")
        print(f"  companies_db: {type(companies_db)} = {companies_db}")
        print(f"  intel_db: {type(intel_db)} = {intel_db}")

        bound_processor = functools.partial(
            process_company_notion_with_data,
            intel_by_cs,
            source_metadata_by_cs,
            filtered_news_by_cs,
            lookback_days,
            companies_db,
            intel_db,
            news_store,
            new_callsigns_set,
        )

        # Process Notion updates in parallel (with lower concurrency for API limits)
        notion_results = ParallelProcessor.process_dict_batch(
            roster,
            bound_processor,
            max_workers=3,  # Conservative for Notion API limits
            timeout=300,
        )

        notion_time = PERFORMANCE_MONITOR.end_timer("notion_updates")
        successful_updates = sum(
            1 for r in notion_results.values() if r and r.get("status") == "success"
        )
        perf_msg = (
            "[PERFORMANCE] Notion updates completed in "
            f"{notion_time:.2f}s ({successful_updates} successful)"
        )
        print(perf_msg)

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
                html,
            )
            print("Digest emailed to", digest_to)
        except Exception as e:
            print("Email digest error:", repr(e))

    # Print performance statistics
    PERFORMANCE_MONITOR.print_stats()


if __name__ == "__main__":
    main()
