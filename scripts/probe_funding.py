# scripts/probe_funding.py
import os, re
from typing import List, Dict, Any, Optional
import requests
from trafilatura import fetch_url as _t_fetch, extract as _t_extract

from app.news_job import google_cse_search
from app.enrich_funding import extract_funding_from_text  # uses your current heuristics

def _safe_text(url: str, timeout: int = 6) -> str:
    try:
        html = _t_fetch(url, timeout=timeout)
        if not html:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code < 400:
                html = r.text
        if not html:
            return ""
        txt = _t_extract(html, include_links=False, include_comments=False, favor_recall=True) or ""
        return txt[:25000]
    except Exception:
        return ""

def probe(name: str, owners_csv: str = "") -> None:
    owners = [s.strip() for s in owners_csv.split(",") if s.strip()]
    g_key = os.getenv("GOOGLE_API_KEY")
    g_cse = os.getenv("GOOGLE_CSE_ID")
    if not (g_key and g_cse):
        print("Set GOOGLE_API_KEY and GOOGLE_CSE_ID in your environment.")
        return

    base_queries = [
        f"{name} funding",
        f"{name} raises",
        f"{name} fundraise",
        f"{name} seed funding",
        f"{name} series funding",
    ]
    if owners:
        lead = owners[0]
        base_queries += [
            f"{name} {lead} raises",
            f"{name} {lead} funding",
        ]

    seen = set()
    pages: List[Dict[str, Any]] = []
    for q in base_queries:
        try:
            items = google_cse_search(g_key, g_cse, q, num=4)
        except Exception as e:
            print("CSE error:", e)
            continue
        for it in items or []:
            url = (it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            txt = _safe_text(url)
            pages.append({"url": url, "text": txt})

    print(f"\nFetched {len(pages)} pages. Extracting funding signals…")
    best: Dict[str, Any] = {}
    sources: List[str] = []
    for p in pages:
        found = extract_funding_from_text(p.get("text") or "")
        if found:
            # keep first few sources
            if p.get("url"):
                sources.append(p["url"])
            # merge preference: keep previously set fields
            for k, v in found.items():
                if k not in best or best[k] in (None, "", [], 0):
                    best[k] = v

    if sources:
        best["funding_sources"] = sources[:6]

    print("\nResult:")
    if not best:
        print("(no clear funding extracted)")
    else:
        for k, v in best.items():
            print(f"- {k}: {v}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.probe_funding \"Company Name\" [\"Owner One,Owner Two\"]")
        raise SystemExit(2)
    name = sys.argv[1]
    owners = sys.argv[2] if len(sys.argv) > 2 else ""
    probe(name, owners)
