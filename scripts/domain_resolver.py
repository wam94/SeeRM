# app/domain_resolver.py
from __future__ import annotations
import os, re, requests, tldextract
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
from trafilatura import fetch_url, extract as trafi_extract

BLOCKED_HOSTS = {
    "linkedin.com","x.com","twitter.com","facebook.com","instagram.com","youtube.com",
    "github.com","medium.com","substack.com","notion.so","notion.site",
    "docs.google.com","wikipedia.org","angel.co","bloomberg.com","crunchbase.com","pitchbook.com",
    "owler.com","zoominfo.com","rocketreach.co","builtwith.com"
}
TRUST_TLDS = {"com","io","ai","co","net","org","app","dev","tech"}

def _registered_domain(u: str) -> Optional[str]:
    try:
        if not u: return None
        s = u.strip()
        if not s.startswith(("http://","https://")):
            s = "http://" + s
        host = urlparse(s).netloc or urlparse(s).path
        ext = tldextract.extract(host)
        return ext.registered_domain.lower() if ext.registered_domain else None
    except Exception:
        return None

def _head_ok(url: str, timeout: int = 6) -> bool:
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

def _homepage_text(url: str, timeout: int = 12) -> str:
    try:
        html = fetch_url(url, timeout=timeout)
        if not html: return ""
        txt = trafi_extract(html, include_comments=False, include_tables=False) or ""
        return txt[:25000]
    except Exception:
        return ""

def _score_candidate(company_name: str, url: str, title: str, display_link: str) -> Tuple[int, str]:
    rd = _registered_domain(url) or ""
    host = rd
    score = 0
    why = []

    # Block obvious non-official
    if any(host.endswith(bh) for bh in BLOCKED_HOSTS):
        score -= 30; why.append("blocked-host")

    # Name match
    cname = re.sub(r'[^a-z0-9]+',' ', company_name.lower()).strip()
    tnorm = re.sub(r'[^a-z0-9]+',' ', (title or "").lower())
    if cname and (cname in tnorm or host.split(".")[0] in cname.replace(" ", "")):
        score += 35; why.append("name-match")

    # Trust TLD
    tld = host.split(".")[-1] if "." in host else ""
    if tld in TRUST_TLDS:
        score += 10; why.append("trust-tld")

    # Path semantics
    path = urlparse(url).path.lower()
    if any(p in path for p in ("/about","/careers","/press","/contact","/team")):
        score += 25; why.append("about/careers/press/contact")

    # HEAD ok?
    home = f"https://{host}"
    if _head_ok(home) or _head_ok("https://www."+host) or _head_ok("http://"+host):
        score += 20; why.append("head-ok")

    # Body contains company name?
    text = _homepage_text(home) or _homepage_text("https://www."+host)
    if cname and text and cname.split(" ")[0] in text.lower():
        score += 20; why.append("name-in-body")

    return score, ",".join(why)

def resolve_domain(company_name: str,
                   google_api_key: Optional[str],
                   google_cse_id: Optional[str],
                   hints: Optional[Dict[str, Any]] = None,
                   max_candidates: int = 8) -> Optional[Dict[str, Any]]:
    """
    Returns {'domain_root': 'example.com', 'homepage_url': 'https://example.com', 'score': int, 'why': str}
    or None if not found.
    """
    hints = hints or {}
    # 1) Prefer hints already provided
    for k in ("domain","domain_root","website"):
        v = (hints.get(k) or "").strip()
        rd = _registered_domain(v)
        if rd:
            # validate and return
            home = f"https://{rd}"
            if not _head_ok(home):
                home = f"https://www.{rd}" if _head_ok(f"https://www.{rd}") else f"http://{rd}"
            return {"domain_root": rd, "homepage_url": home, "score": 100, "why": "hint"}

    # 2) If we can't search, bail
    if not (google_api_key and google_cse_id) or not company_name:
        return None

    # 3) Search via existing helper
    try:
        from app.news_job import google_cse_search
        q = f'"{company_name}" (official site OR homepage) -site:linkedin.com -site:crunchbase.com -site:pitchbook.com -site:twitter.com -site:x.com'
        items = google_cse_search(google_api_key, google_cse_id, q, num=max_candidates)
    except Exception:
        items = []

    # 4) Score candidates
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for it in items:
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        display_link = (it.get("displayLink") or "").strip()
        rd = _registered_domain(url)
        if not rd: 
            continue
        if any(rd.endswith(bh) for bh in BLOCKED_HOSTS):
            continue
        s, why = _score_candidate(company_name, url, title, display_link)
        if s > 0:
            home = f"https://{rd}"
            if not _head_ok(home):
                home = f"https://www.{rd}" if _head_ok(f"https://www.{rd}") else f"http://{rd}"
            scored.append((s, {"domain_root": rd, "homepage_url": home, "score": s, "why": why}))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def probe(name: str, owners_csv: str = ""):
    """CLI interface for domain resolution testing."""
    owners = [s.strip() for s in owners_csv.split(",") if s.strip()] if owners_csv else []
    g_key = os.getenv("GOOGLE_API_KEY")
    g_cse = os.getenv("GOOGLE_CSE_ID")
    
    # Use existing hints structure (empty for testing)
    hints = {"owners": owners} if owners else {}
    
    result = resolve_domain(name, g_key, g_cse, hints)
    
    print(f"Name: {name}")
    print(f"Owners: {owners}")
    if result:
        print(f"Chosen domain: {result['domain_root']}")
        print(f"Validated URL: {result['homepage_url']}")
        print(f"Score: {result['score']}")
        print(f"Why: {result['why']}")
    else:
        print("Chosen domain: None")
        print("Validated URL: None")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.domain_resolver \"Company Name\" [\"Owner One,Owner Two\"]")
        raise SystemExit(2)
    name = sys.argv[1]
    owners = sys.argv[2] if len(sys.argv) > 2 else ""
    probe(name, owners)