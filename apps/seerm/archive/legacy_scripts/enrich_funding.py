# app/enrich_funding.py
from __future__ import annotations

import math
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
import tldextract
from trafilatura import extract as trafi_extract

# ------------- Env / knobs -------------
DEBUG = os.getenv("BASELINE_DEBUG", "").lower() in ("1", "true", "yes", "y")
FUNDING_FETCH_TIMEOUT = float(os.getenv("FUNDING_FETCH_TIMEOUT_SEC", "5") or "5")
CB_HTTP_TIMEOUT = float(os.getenv("CB_HTTP_TIMEOUT_SEC", "10") or "10")

UA = {"User-Agent": "Mozilla/5.0 (compatible; SeeRM/1.0; +https://example.invalid/bot)"}


def _logd(*parts: Any) -> None:
    if DEBUG:
        print(*parts)


# ------------- Regex heuristics -------------
AMOUNT_RE = re.compile(
    r"(?<![\d$])(?:USD\s*)?\$?\s*([0-9][\d,\.]*)\s*(billion|bn|million|mm|m|thousand|k)?",
    re.I,
)
ROUND_RE = re.compile(
    r"\b(Pre-Seed|Seed|Angel|Series\s+[A-K]|Series\s+[A-K]\s+extension|Bridge|Convertible\s+Note|SAFE|Debt|Venture\s+Round|Equity\s+Round)\b",
    re.I,
)
DATE_RE = re.compile(r"\b(20\d{2}|19\d{2})[-/\.](\d{1,2})[-/\.](\d{1,2})\b")
LED_BY_RE = re.compile(r"\b(led by|co-led by)\s+([^.;,\n]+)", re.I)
WITH_PARTICIPATION_RE = re.compile(r"\b(with participation from|including)\s+([^.;\n]+)", re.I)


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
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except Exception:
        return None


def extract_funding_from_text(text: str) -> Dict[str, Any]:
    """Parse human text for round type/amount/date/investors."""
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
            investors += [p.strip(" .") for p in re.split(r",| and ", mm.group(2)) if p.strip()]
    if investors:
        investors = [re.sub(r"\(.*?\)$", "", i).strip() for i in investors]
        res["investors"] = sorted(set(investors))

    return res


# ------------- Fetching helpers (fast timeouts) -------------


def fetch_page_text(url: Optional[str], timeout: float = FUNDING_FETCH_TIMEOUT) -> Optional[str]:
    if not url:
        return None
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        # Let trafilatura do the cleaning from the HTML string
        txt = trafi_extract(r.text, output="txt", include_comments=False, favor_precision=True)
        return txt
    except Exception as e:
        _logd("[fetch_page_text] error:", repr(e), "url=", url)
        return None


# ------------- Optional: Crunchbase API -------------
def crunchbase_enrich(domain_root: Optional[str], name: Optional[str]) -> Dict[str, Any]:
    """
    Best-effort enrichment via Crunchbase. Skips cleanly if CRUNCHBASE_API_KEY unset.
    """
    key = os.getenv("CRUNCHBASE_API_KEY")
    if not key:
        return {}

    H = {"X-cb-user-key": key, "Content-Type": "application/json"}
    BASE = "https://api.crunchbase.com/api/v4"

    # Search by domain, then by name
    payloads = []
    if domain_root:
        payloads.append(
            {
                "field_ids": ["identifier", "name", "website", "short_description"],
                "query": [
                    {
                        "type": "predicate",
                        "field_id": "website",
                        "operator_id": "contains",
                        "values": [domain_root],
                    }
                ],
                "limit": 1,
            }
        )
    if name:
        payloads.append(
            {
                "field_ids": ["identifier", "name", "website", "short_description"],
                "query": [
                    {
                        "type": "predicate",
                        "field_id": "name",
                        "operator_id": "contains",
                        "values": [name],
                    }
                ],
                "limit": 1,
            }
        )

    org_id = None
    for body in payloads:
        try:
            r = requests.post(
                f"{BASE}/searches/organizations", headers=H, json=body, timeout=CB_HTTP_TIMEOUT
            )
            if r.status_code != 200:
                continue
            ents = r.json().get("entities") or []
            if ents:
                org_id = ents[0]["identifier"].get("uuid") or ents[0]["identifier"].get("permalink")
                break
        except Exception as e:
            _logd("[crunchbase] search error:", repr(e))

    if not org_id:
        return {}

    body = {
        "field_ids": [
            "name",
            "identifier",
            "website",
            "last_funding_type",
            "last_funding_at",
            "last_funding_total_usd",
            "funding_total_usd",
            "investors",
            "investors_names",
            "announced_on",
        ]
    }
    try:
        r = requests.post(
            f"{BASE}/entities/organizations/{org_id}", headers=H, json=body, timeout=CB_HTTP_TIMEOUT
        )
        if r.status_code != 200:
            return {}
        ent = r.json().get("properties", {})
        out: Dict[str, Any] = {}

        def get(*keys):
            for k in keys:
                if k in ent:
                    return ent.get(k)
            return None

        out["total_funding_usd"] = get("funding_total_usd")
        out["last_round_type"] = get("last_funding_type")
        out["last_round_date"] = get("last_funding_at") or get("announced_on")
        out["last_round_amount_usd"] = get("last_funding_total_usd")

        inv = get("investors_names") or get("investors")
        if isinstance(inv, list):
            out["investors"] = inv[:10]
        elif isinstance(inv, str):
            out["investors"] = [s.strip() for s in inv.split(",") if s.strip()][:10]

        out["source_cb"] = True
        return {k: v for k, v in out.items() if v not in (None, "", [], 0)}
    except Exception as e:
        _logd("[crunchbase] entity error:", repr(e))
        return {}


# ------------- Combine/score -------------
def merge_funding(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """Prefer 'primary', use 'secondary' when primary is empty or missing."""
    out = dict(primary or {})
    for k, v in (secondary or {}).items():
        if k not in out or out[k] in (None, "", [], 0):
            out[k] = v
    return out


def best_funding_from_pages(
    org: Dict[str, Any], fetched_pages: List[Dict[str, Any]]
) -> Dict[str, Any]:
    heur: Dict[str, Any] = {}
    sources: List[str] = []
    for p in fetched_pages or []:
        text = p.get("text") or ""
        if not text:
            continue
        cand = extract_funding_from_text(text)
        if cand:
            heur = merge_funding(cand, heur)
            if p.get("url"):
                sources.append(p["url"])
    if sources:
        heur["funding_sources"] = list(dict.fromkeys(sources))[:5]
    if heur:
        heur["funding_present"] = True
    return heur


# ------------- Optional discovery via Google CSE -------------
# We import lazily to avoid circular deps when this module is used standalone.
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


def _registered_domain(url: str) -> Optional[str]:
    ext = tldextract.extract(url or "")
    rd = (ext.registered_domain or "").lower()
    return rd or None


def discover_candidate_urls(
    org: Dict[str, Any], g_api_key: Optional[str], g_cse_id: Optional[str], max_pages: int = 6
) -> List[str]:
    if not (g_api_key and g_cse_id):
        return []
    try:
        from app.news_job import google_cse_search
    except Exception:
        return []

    name = org.get("dba") or org.get("callsign") or ""
    domain = org.get("domain") or org.get("domain_root") or ""

    queries = []
    if domain:
        queries.append(
            f'site:{domain} (funding OR raised OR financing OR "Series A" OR "Series B" OR seed)'
        )
        queries.append(f"site:{domain} (press release OR newsroom) (funding OR raised)")
    if name:
        queries.append(f'"{name}" funding')
        queries.append(f'"{name}" raised')
        queries.append(f'"{name}" financing press release')

    urls: List[str] = []
    for q in queries:
        try:
            items = google_cse_search(g_api_key, g_cse_id, q, num=5)
        except Exception as e:
            _logd("[discover_candidate_urls] CSE error:", repr(e), "q=", q)
            continue
        for it in items or []:
            u = (it.get("url") or "").strip()
            if not u:
                continue
            rd = _registered_domain(u)
            if not rd or any(rd.endswith(b) for b in _BLOCKED_SITES):
                continue
            urls.append(u)
        if len(urls) >= max_pages:
            break

    # de-dup in order
    seen = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
        if len(out) >= max_pages:
            break
    return out


def discover_and_fetch_funding_pages(
    org: Dict[str, Any], g_api_key: Optional[str], g_cse_id: Optional[str], max_pages: int = 6
) -> List[Dict[str, Any]]:
    urls = discover_candidate_urls(org, g_api_key, g_cse_id, max_pages=max_pages)
    pages: List[Dict[str, Any]] = []
    for u in urls:
        txt = fetch_page_text(u)
        if txt:
            pages.append({"url": u, "text": txt})
    return pages


# ------------- Public API -------------
def best_funding(
    org: Dict[str, Any],
    fetched_pages: List[Dict[str, Any]],
    g_api_key: Optional[str] = None,
    g_cse_id: Optional[str] = None,
    discover: bool = False,
    max_discovery_pages: int = 6,
) -> Dict[str, Any]:
    """
    Combine:
      - heuristics from already-fetched pages (fetched_pages = [{url, text}])
      - OPTIONAL discovery via Google CSE (discover=True) with its own quick fetches
      - OPTIONAL Crunchbase enrichment if CRUNCHBASE_API_KEY is set
    Returns a dict with keys like:
      {funding_present, last_round_type, last_round_date, last_round_amount_usd,
       total_funding_usd, investors, funding_sources, source_cb}
    """
    heur_from_input = best_funding_from_pages(org, fetched_pages)

    pages_more: List[Dict[str, Any]] = []
    if discover and g_api_key and g_cse_id:
        try:
            pages_more = discover_and_fetch_funding_pages(
                org, g_api_key, g_cse_id, max_pages=max_discovery_pages
            )
        except Exception as e:
            _logd("[best_funding] discovery error:", repr(e))

    heur_from_discovery = best_funding_from_pages(org, pages_more)

    heur = merge_funding(heur_from_input, heur_from_discovery)

    # Optional Crunchbase overlay
    cb = crunchbase_enrich(org.get("domain_root") or org.get("domain"), org.get("dba"))
    out = merge_funding(cb, heur) if cb else heur

    if out:
        out["funding_present"] = True
    return out


__all__ = [
    "extract_funding_from_text",
    "crunchbase_enrich",
    "merge_funding",
    "best_funding_from_pages",
    "discover_candidate_urls",
    "discover_and_fetch_funding_pages",
    "best_funding",
]
