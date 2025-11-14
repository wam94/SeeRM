#!/usr/bin/env python3
# scripts/probe_funding.py
"""
Probe likely funding events for a company using Google CSE + (optional) page fetch + heuristics.

Strategy (high level)
---------------------
1) Build a diverse set of search queries:
   - Company name (+ AKA) + ("raises"|"funding"|"seed"|"series" etc.)
   - site:company-domain (press|news|blog) + ("raises"|"funding"|…)
   - Founder names + ("raises"|"funding"|…)
2) Use Google Programmable Search (CSE) to fetch top results (date-restrict by lookback).
3) Optionally fetch pages and extract full text (trafilatura).
4) Extract normalized funding facts (amount, round, date, investors) via regex + heuristics.
5) Score each candidate (prefer official domain or trusted outlets, strong funding verbs, recency).
6) Emit best n events with a confidence score and the top evidence URLs.

Environment
-----------
GOOGLE_API_KEY       (required)
GOOGLE_CSE_ID        (required)
OPENAI_API_KEY       (optional; not used by default here)
CRUNCHBASE_API_KEY   (optional; if present, we add CB snapshot as a hint)

Install
-------
pip install -r requirements.txt   # you already have requests, trafilatura, dateparser, pandas

Usage
-----
python scripts/probe_funding.py \
  --name "Aalo Atomics" \
  --domain "aalo.com" \
  --owners "Matt Loszak" \
  --lookback-days 730 \
  --max-results 8 \
  --fetch-pages true \
  --out-json /tmp/aalo_funding.json

Outputs
-------
- Prints a concise summary to stdout
- Optionally writes JSON and/or CSV with structured funding candidates

Notes
-----
- Works fine without Crunchbase; if CRUNCHBASE_API_KEY is missing, that path is skipped.
- If you use this inside GH Actions, remember to set GOOGLE_API_KEY/GOOGLE_CSE_ID as secrets.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from dateparser import parse as dateparse
from trafilatura import extract as trafi_extract
from trafilatura import fetch_url

# ------------------------- Config / Constants -------------------------

TRUSTED_SOURCES = {
    "techcrunch.com",
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
    "news.yahoo.com",
    "venturebeat.com",
    "crunchbase.com",
    "crunchbase.com",
    "medium.com",
    "blog.google",
    "substack.com",
    "pitchbook.com",
    "tech.eu",
    "sifted.eu",
}

# Verbs/phrases that usually indicate a funding announcement
FUNDING_VERBS = [
    "raises",
    "raised",
    "announces funding",
    "announced funding",
    "closes",
    "closed",
    "secur(es|ed)",
    "lands",
    "snags",
    "bags",
    "series",
    "seed round",
    "pre-seed",
    "angel round",
    "venture round",
    "financing",
    "round",
]

# Regex for money + common units
AMOUNT_RE = re.compile(
    r"(?<![\d$])(?:USD\s*)?\$?\s*([0-9][\d,\.]*)\s*(billion|bn|million|mm|m|thousand|k)?", re.I
)

# Round types
ROUND_RE = re.compile(
    r"\b(Pre-Seed|Seed|Angel|Series\s+[A-L]|Series\s+[A-L]\s+extension|Bridge|Convertible\s+Note|SAFE|Debt|Venture\s+Round|Equity\s+Round)\b",
    re.I,
)

# Date hints (we also parse with dateparser downstream)
DATE_PATH_RE = re.compile(r"/((19|20)\d{2})/(\d{1,2})/(\d{1,2})/")

LEAD_RE = re.compile(r"\b(led by|co-led by)\s+([^.;,\n]+)", re.I)
PARTICIP_RE = re.compile(r"\b(with participation from|including)\s+([^.;\n]+)", re.I)

DEFAULT_TIMEOUT = 30

# ------------------------- Helpers -------------------------


def getenv(n: str, d: Optional[str] = None) -> Optional[str]:
    v = os.getenv(n)
    return d if v in (None, "") else v


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def registered_domain(host: str) -> str:
    host = host.lower()
    # simple fallback if tldextract isn’t guaranteed installed here
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def to_usd(n_raw: str, unit: Optional[str]) -> Optional[float]:
    try:
        n = float(n_raw.replace(",", ""))
    except Exception:
        return None
    u = (unit or "").lower()
    if u in ("billion", "bn"):
        n *= 1_000_000_000
    elif u in ("million", "mm", "m"):
        n *= 1_000_000
    elif u in ("thousand", "k"):
        n *= 1_000
    return n


def parse_date_from_text(s: str) -> Optional[str]:
    # Try path-style dates first
    m = DATE_PATH_RE.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(3)), int(m.group(4))
        try:
            return f"{y:04d}-{mo:02d}-{d:02d}"
        except Exception:
            pass
    # Fallback to dateparser
    dt = dateparse(s, settings={"RETURN_AS_TIMEZONE_AWARE": False})
    if dt:
        try:
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    return None


def extract_investors(text: str) -> List[str]:
    investors: List[str] = []
    for rx in (LEAD_RE, PARTICIP_RE):
        m = rx.search(text)
        if m:
            chunk = m.group(2)
            # split on commas or " and "
            for p in re.split(r",| and ", chunk):
                p = p.strip(" .;:()[]")
                if p and len(p) <= 100:
                    investors.append(p)
    # dedupe while preserving order
    seen = set()
    out = []
    for x in investors:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def extract_funding_facts(raw_text: str) -> Dict[str, Any]:
    """
    Pull amount, round, and date from a blob of text (title/snippet/body).
    """
    t = raw_text or ""
    res: Dict[str, Any] = {}

    m = ROUND_RE.search(t)
    if m:
        res["round_type"] = m.group(1).title()

    m = AMOUNT_RE.search(t)
    if m:
        amt = to_usd(m.group(1), m.group(2))
        if amt and math.isfinite(amt):
            res["amount_usd"] = int(round(amt))

    d = parse_date_from_text(t)
    if d:
        res["announced_on"] = d

    inv = extract_investors(t)
    if inv:
        res["investors"] = inv

    return res


def google_cse_search(
    query: str, api_key: str, cse_id: str, num: int = 10, date_restrict: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Minimal Google CSE client. Returns list of items with url, title, snippet, source(host).
    """
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": max(1, min(num, 10)),
    }
    if date_restrict:
        params["dateRestrict"] = date_restrict  # e.g., d365, m6, y1

    r = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    items = []
    for it in data.get("items", []):
        link = it.get("link") or it.get("formattedUrl") or ""
        title = it.get("title") or ""
        snippet = it.get("snippet") or ""
        host = registered_domain(domain_of(link))
        items.append(
            {
                "url": link,
                "title": title,
                "snippet": snippet,
                "source": host,
            }
        )
    return items


def fetch_text(url: str) -> str:
    try:
        raw = fetch_url(url)
        if not raw:
            return ""
        txt = trafi_extract(raw, include_links=False, include_formatting=False) or ""
        return txt.strip()
    except Exception:
        return ""


def contains_funding_verb(s: str) -> bool:
    s = (s or "").lower()
    return any(w in s for w in FUNDING_VERBS)


def score_candidate(
    item: Dict[str, Any], company_domain: Optional[str], lookback_days: int
) -> float:
    """
    Heuristic scoring:
      +0.35 if official company domain
      +0.25 if trusted source
      +0.20 if has funding verbs in title/snippet/text
      +0.10 if amount detected
      +0.05 if round detected
      +0.05 if investors detected
    """
    score = 0.0
    host = item.get("source") or ""
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    text = item.get("text") or ""

    facts = item.get("facts") or {}

    if company_domain and host.endswith(company_domain):
        score += 0.35
    elif host in TRUSTED_SOURCES:
        score += 0.25

    if (
        contains_funding_verb(title)
        or contains_funding_verb(snippet)
        or contains_funding_verb(text)
    ):
        score += 0.20

    if "amount_usd" in facts:
        score += 0.10
    if "round_type" in facts:
        score += 0.05
    if "investors" in facts and facts["investors"]:
        score += 0.05

    # Cap to [0,1]
    return max(0.0, min(1.0, score))


def dedupe_urls(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for it in items:
        u = (it.get("url") or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(it)
    return out


def crunchbase_enrich(name: Optional[str], domain_root: Optional[str]) -> Dict[str, Any]:
    key = getenv("CRUNCHBASE_API_KEY")
    if not key:
        return {}
    try:
        H = {"X-cb-user-key": key, "Content-Type": "application/json"}
        BASE = "https://api.crunchbase.com/api/v4"

        # First: search by website; fallback name
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
            r = requests.post(
                f"{BASE}/searches/organizations", headers=H, json=body, timeout=DEFAULT_TIMEOUT
            )
            if r.status_code != 200:
                continue
            ents = r.json().get("entities") or []
            if ents:
                org_id = ents[0]["identifier"].get("uuid") or ents[0]["identifier"].get("permalink")
                break
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
        r = requests.post(
            f"{BASE}/entities/organizations/{org_id}", headers=H, json=body, timeout=DEFAULT_TIMEOUT
        )
        if r.status_code != 200:
            return {}
        ent = r.json().get("properties", {})
        out: Dict[str, Any] = {}
        out["total_funding_usd"] = ent.get("funding_total_usd")
        out["last_round_type"] = ent.get("last_funding_type")
        out["last_round_date"] = ent.get("last_funding_at") or ent.get("announced_on")
        out["last_round_amount_usd"] = ent.get("last_funding_total_usd")
        inv = ent.get("investors_names") or ent.get("investors")
        if isinstance(inv, list):
            out["investors"] = inv[:10]
        elif isinstance(inv, str):
            out["investors"] = [s.strip() for s in inv.split(",") if s.strip()][:10]
        out["source_cb"] = True
        # prune empties
        return {k: v for k, v in out.items() if v not in (None, "", [], 0)}
    except Exception:
        return {}


# ------------------------- Query generation -------------------------


def build_queries(
    name: str,
    domain: Optional[str],
    owners: Optional[List[str]] = None,
    aka: Optional[List[str]] = None,
) -> List[str]:
    qs = []

    main_names = [name] + (aka or [])
    main_names = [n for n in [x.strip() for x in main_names if x] if n]

    # Core funding phrases
    funding_terms = [
        "raises",
        "raised",
        "fundraise",
        "funding",
        '"funding round"',
        "seed round",
        '"pre-seed"',
        '"Series A"',
        '"Series B"',
        '"Series C"',
        "venture round",
        "financing",
        '"led by"',
        '"co-led by"',
        '"participation from"',
    ]

    for nm in main_names:
        qs.append(f'{nm} {" OR ".join(funding_terms)}')
        qs.append(f"{nm} announces funding OR raises OR financing")

    if domain:
        # official site press/news/blog
        for section in ("press", "news", "blog"):
            qs.append(f"site:{domain} {section} raises OR funding OR seed OR series")

    for person in owners or []:
        if person:
            qs.append(f'"{person}" {name} raises OR funding OR seed OR series')
            qs.append(f'"{person}" {name} announces funding OR financing')

    # Small hygiene variants
    if domain:
        qs.append(f'site:{domain} "funding"')
        qs.append(f'site:{domain} "raises"')

    # Dedup
    seen = set()
    out = []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


# ------------------------- Core probe -------------------------


def probe_funding(
    name: str,
    domain: Optional[str],
    owners: Optional[List[str]],
    aka: Optional[List[str]],
    lookback_days: int = 365,
    max_results: int = 8,
    fetch_pages: bool = True,
) -> Dict[str, Any]:

    api_key = os.getenv("GOOGLE_API_KEY")
    cse_id = os.getenv("GOOGLE_CSE_ID")
    if not (api_key and cse_id):
        raise SystemExit("Set GOOGLE_API_KEY and GOOGLE_CSE_ID in the environment.")

    queries = build_queries(name, domain, owners, aka)
    date_restrict = f"d{max(1, lookback_days)}"

    # Gather CSE hits - CONCURRENT
    try:
        from app.performance_utils import ConcurrentAPIClient, SmartRateLimiter

        # Create API call functions for concurrent execution
        api_calls = []
        for q in queries:
            api_calls.append(
                lambda query=q: google_cse_search(
                    query, api_key, cse_id, num=5, date_restrict=date_restrict
                )
            )

        # Execute queries concurrently with rate limiting
        rate_limiter = SmartRateLimiter(calls_per_second=2.5, burst_size=5)
        api_client = ConcurrentAPIClient(rate_limiter)
        concurrent_results = api_client.batch_api_calls(api_calls, max_workers=4, timeout=60)

        # Flatten results
        hits: List[Dict[str, Any]] = []
        for result in concurrent_results:
            if result:
                hits.extend(result)

    except ImportError:
        # Fallback to sequential processing if performance_utils not available
        hits: List[Dict[str, Any]] = []
        for q in queries:
            try:
                items = google_cse_search(q, api_key, cse_id, num=5, date_restrict=date_restrict)
                hits.extend(items)
                time.sleep(0.3)  # polite
            except Exception as e:
                print("[CSE] query error:", q, repr(e))

    # Deduplicate by url
    hits = dedupe_urls(hits)

    # Fetch pages (optional) and extract facts - PARALLEL
    if fetch_pages:
        try:
            from app.performance_utils import ParallelProcessor

            def fetch_and_process_page(item):
                text = fetch_text(item["url"])
                # very short pages aren't helpful
                if len(text) < 400:
                    text = f"{item.get('title','')}\n{item.get('snippet','')}"

                item["text"] = text or ""
                item["facts"] = extract_funding_facts(
                    " ".join([item.get("title", ""), item.get("snippet", ""), item.get("text", "")])
                )
                item["published_at"] = (
                    parse_date_from_text(" ".join([item["url"], item["text"]])) or None
                )
                return item

            # Process pages in parallel
            limited_hits = hits[:20]  # Reduce fanout for performance
            processed_results = ParallelProcessor.process_batch(
                limited_hits, fetch_and_process_page, max_workers=8, timeout=120
            )

            # Update hits with processed results
            for i, item in enumerate(limited_hits):
                if item in processed_results and processed_results[item]:
                    limited_hits[i] = processed_results[item]

            hits = limited_hits

        except ImportError:
            # Fallback to sequential processing
            for it in hits[:30]:  # limit fetch fanout
                text = fetch_text(it["url"])
                if len(text) < 400:
                    text = f"{it.get('title','')}\n{it.get('snippet','')}"

                it["text"] = text or ""
                it["facts"] = extract_funding_facts(
                    " ".join([it.get("title", ""), it.get("snippet", ""), it.get("text", "")])
                )
                it["published_at"] = parse_date_from_text(" ".join([it["url"], it["text"]])) or None
    else:
        # No page fetching - just process titles and snippets
        for it in hits[:30]:
            text = f"{it.get('title','')}\n{it.get('snippet','')}"
            it["text"] = text
            it["facts"] = extract_funding_facts(text)
            it["published_at"] = parse_date_from_text(" ".join([it["url"], text])) or None

        # Score
        comp_dom = registered_domain(domain) if domain else None
        it["score"] = score_candidate(it, comp_dom, lookback_days)

    # Rank and take top N
    ranked = sorted(hits, key=lambda x: x.get("score", 0.0), reverse=True)
    top = ranked[: max(1, max_results)]

    # Optional: Crunchbase snapshot (if key present)
    cb = {}
    try:
        cb = crunchbase_enrich(name, domain)
    except Exception:
        cb = {}

    # Compose final
    result = {
        "query": {
            "name": name,
            "domain": domain,
            "owners": owners or [],
            "aka": aka or [],
            "lookback_days": lookback_days,
            "max_results": max_results,
            "fetch_pages": fetch_pages,
        },
        "best_guess": None,  # single best item (dict)
        "candidates": top,  # list
        "crunchbase_hint": cb or {},  # optional
    }

    if top:
        result["best_guess"] = top[0]

    return result


# ------------------------- CLI & I/O -------------------------


def write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_csv(path: str, candidates: List[Dict[str, Any]]) -> None:
    fields = [
        "score",
        "source",
        "url",
        "title",
        "published_at",
        "amount_usd",
        "round_type",
        "announced_on",
        "investors",
        "snippet",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for it in candidates:
            facts = it.get("facts") or {}
            w.writerow(
                {
                    "score": it.get("score"),
                    "source": it.get("source"),
                    "url": it.get("url"),
                    "title": it.get("title"),
                    "published_at": it.get("published_at") or "",
                    "amount_usd": facts.get("amount_usd") or "",
                    "round_type": facts.get("round_type") or "",
                    "announced_on": facts.get("announced_on") or "",
                    "investors": ", ".join(facts.get("investors") or []),
                    "snippet": (it.get("snippet") or "").replace("\n", " ").strip(),
                }
            )


def pretty_print(result: Dict[str, Any]) -> None:
    print("\n=== Funding probe ===")
    q = result.get("query", {})
    print(f"Company: {q.get('name')} | Domain: {q.get('domain') or '—'}")
    print(
        f"Owners: {', '.join(q.get('owners') or []) or '—'} | AKA: {', '.join(q.get('aka') or []) or '—'}"
    )
    print(
        f"Lookback: {q.get('lookback_days')} days | Candidates returned: {len(result.get('candidates') or [])}"
    )

    best = result.get("best_guess")
    if best:
        facts = best.get("facts") or {}
        print("\nBest guess")
        print(f"  Score  : {best.get('score'):.2f}")
        print(
            f"  Source : {best.get('source')}  |  Date: {best.get('published_at') or facts.get('announced_on') or '—'}"
        )
        print(f"  Title  : {best.get('title')}")
        if "amount_usd" in facts:
            print(f"  Amount : ${facts['amount_usd']:,}")
        if "round_type" in facts:
            print(f"  Round  : {facts['round_type']}")
        if facts.get("investors"):
            print(f"  Investors: {', '.join(facts['investors'])}")
        print(f"  URL    : {best.get('url')}")
    else:
        print("\nNo strong candidates found.")

    if result.get("crunchbase_hint"):
        cb = result["crunchbase_hint"]
        print("\nCrunchbase (hint):")
        if (
            cb.get("last_round_type")
            or cb.get("last_round_amount_usd")
            or cb.get("last_round_date")
        ):
            print(
                "  Last round:",
                cb.get("last_round_type") or "?",
                "|",
                f"${cb.get('last_round_amount_usd'):,}" if cb.get("last_round_amount_usd") else "?",
                "|",
                cb.get("last_round_date") or "?",
            )
        if cb.get("total_funding_usd"):
            print("  Total funding:", f"${cb['total_funding_usd']:,}")
        if cb.get("investors"):
            print("  Investors (CB):", ", ".join(cb["investors"][:6]))


def main():
    p = argparse.ArgumentParser(description="Probe likely funding events for a company.")
    p.add_argument("--name", required=True, help="Company name / brand")
    p.add_argument("--domain", help="Root domain (example.com)", default=None)
    p.add_argument("--owners", help="Comma-separated founder/exec names", default=None)
    p.add_argument("--aka", help="Comma-separated AKA names", default=None)
    p.add_argument("--lookback-days", type=int, default=365)
    p.add_argument("--max-results", type=int, default=8)
    p.add_argument(
        "--fetch-pages", type=lambda s: str(s).lower() in ("1", "true", "yes", "y"), default=True
    )
    p.add_argument("--out-json", help="Path to write JSON (optional)")
    p.add_argument("--out-csv", help="Path to write CSV (optional)")
    args = p.parse_args()

    owners = [s.strip() for s in (args.owners or "").split(",") if s.strip()]
    aka = [s.strip() for s in (args.aka or "").split(",") if s.strip()]

    result = probe_funding(
        name=args.name,
        domain=args.domain,
        owners=owners,
        aka=aka,
        lookback_days=args.lookback_days,
        max_results=args.max_results,
        fetch_pages=args.fetch_pages,
    )

    pretty_print(result)

    if args.out_json:
        write_json(args.out_json, result)
        print(f"\nWrote JSON → {args.out_json}")
    if args.out_csv:
        write_csv(args.out_csv, result.get("candidates") or [])
        print(f"Wrote CSV  → {args.out_csv}")


if __name__ == "__main__":
    main()
