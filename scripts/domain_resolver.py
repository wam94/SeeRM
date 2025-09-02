# scripts/domain_resolver.py
from __future__ import annotations
import os, re, sys, json, argparse, requests, tldextract
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
from difflib import SequenceMatcher

try:
    # Optional: richer text extraction if available
    from trafilatura import fetch_url, extract as trafi_extract
except Exception:
    fetch_url = None
    trafi_extract = None

# -------------------- Config / Constants --------------------

BLOCKED_HOSTS = {
    "linkedin.com","x.com","twitter.com","facebook.com","instagram.com","youtube.com",
    "github.com","medium.com","substack.com","notion.so","notion.site",
    "docs.google.com","wikipedia.org","angel.co","bloomberg.com","crunchbase.com","pitchbook.com",
    "owler.com","zoominfo.com","rocketreach.co","builtwith.com"
}
TRUST_TLDS = {"com","io","ai","co","net","org","app","dev","tech"}

STOPWORDS = {
    "inc","inc.","corp","corporation","co","co.","llc","l.l.c","ltd","ltd.","plc","plc.",
    "holdings","group","labs","systems","technologies","technology","solutions","pbc","pc",
    "gmbh","s.a.","s.a","s.l.","pty","pty.","saas"
}

DEFAULT_MAX_CANDIDATES = 8
REQ_TIMEOUT = 8

# -------------------- Debug helper --------------------

def logd(enabled: bool, *args):
    if enabled:
        print("[domain]", *args, file=sys.stderr)

# -------------------- URL / Domain helpers --------------------

def _registered_domain(u: str | None) -> Optional[str]:
    if not u: return None
    try:
        s = u.strip()
        if not s: return None
        if not s.startswith(("http://","https://")):
            s = "http://" + s
        host = urlparse(s).netloc or urlparse(s).path
        ext = tldextract.extract(host)
        return ext.registered_domain.lower() if ext.registered_domain else None
    except Exception:
        return None

def _head_ok(url: str, timeout: int = REQ_TIMEOUT) -> bool:
    """Try HEAD, then a small GET if HEAD isn’t allowed."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            return True
    except Exception:
        pass
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
        # read a tiny amount to confirm it’s alive
        for _ in r.iter_content(chunk_size=1024):
            break
        return r.status_code < 400
    except Exception:
        return False

def _pick_home(rd: str) -> str:
    for u in (f"https://{rd}", f"https://www.{rd}", f"http://{rd}"):
        if _head_ok(u):
            return u
    return f"https://{rd}"  # fallback even if not head-ok

def _homepage_text(url: str, timeout: int = REQ_TIMEOUT) -> str:
    """Get visible text for validation; trafilatura if present, else basic fallback."""
    try:
        if fetch_url and trafi_extract:
            html = fetch_url(url, timeout=timeout)
            if not html: return ""
            txt = trafi_extract(html, include_comments=False, include_tables=False) or ""
            return txt[:25000]
        # Fallback: very light GET and regex strip (crude but avoids new deps)
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400 or not r.text:
            return ""
        # strip tags roughly
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text)[:25000]
    except Exception:
        return ""

def _norm_name(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (s or "").lower()).strip()

def _brand_guess(company_name: str) -> str:
    """Heuristic: first non-stopword token is the brand core ('Aalo' from 'Aalo Holdings Inc.')."""
    tokens = [t for t in _norm_name(company_name).split() if t and t not in STOPWORDS]
    return tokens[0] if tokens else _norm_name(company_name).split()[0] if company_name else ""

def _host_label(rd: str) -> str:
    # second-level label (e.g., "aalo" from "aalo.com")
    return rd.split(".")[0] if rd else ""

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _name_close_match(company_name: str, rd: str, akas: List[str], threshold: float = 0.82) -> bool:
    # try full name (no spaces) and brand guess and any aka tokens
    candidates = []
    full = _norm_name(company_name).replace(" ", "")
    if full: candidates.append(full)
    brand = _brand_guess(company_name)
    if brand: candidates.append(brand)
    for aka in akas:
        aka_norm = _norm_name(aka).replace(" ", "")
        if aka_norm:
            candidates.append(aka_norm)
    host = _host_label(rd)
    return any(_similar(c, host) >= threshold for c in candidates)

def _text_validates(text: str, company_name: str, owners: List[str], akas: List[str]) -> bool:
    if not text: return False
    t = text.lower()
    # company tokens
    cname = _norm_name(company_name)
    if cname and any(tok and tok in t for tok in cname.split()):
        return True
    # brand core
    brand = _brand_guess(company_name)
    if brand and brand in t:
        return True
    # akas
    for a in akas:
        a_norm = _norm_name(a)
        if a_norm and any(tok and tok in t for tok in a_norm.split()):
            return True
    # owner last names
    for full in owners or []:
        parts = _norm_name(full).split()
        if parts:
            last = parts[-1]
            if last and last in t:
                return True
    # generic site signals
    if any(w in t for w in ("about", "careers", "contact", "our team", "press")):
        return True
    return False

# -------------------- Scoring & search --------------------

def _score_candidate(company_name: str, rd: str, title: str, url: str, owners: List[str], akas: List[str]) -> Tuple[int,str]:
    if any(rd.endswith(bh) for bh in BLOCKED_HOSTS):
        return (-999, "blocked-host")
    score = 0
    why = []

    tld = rd.split(".")[-1] if "." in rd else ""
    if tld in TRUST_TLDS: score += 8;  why.append("trust-tld")
    if _name_close_match(company_name, rd, akas): score += 30; why.append("name-match")

    path = (urlparse(url).path or "").lower()
    if any(p in path for p in ("/about","/careers","/press","/contact","/team")):
        score += 16; why.append("about/careers/press/contact")

    home = _pick_home(rd)
    if _head_ok(home):
        score += 14; why.append("head-ok")
    txt = _homepage_text(home)
    if _text_validates(txt, company_name, owners, akas):
        score += 22; why.append("text-match")
    return score, ",".join(why)

def _cse(google_api_key: Optional[str], google_cse_id: Optional[str], q: str, num: int = 6) -> List[Dict[str,Any]]:
    if not (google_api_key and google_cse_id): return []
    try:
        # reuse your existing helper to keep behavior consistent
        from app.news_job import google_cse_search
        return google_cse_search(google_api_key, google_cse_id, q, num=num)
    except Exception:
        return []

def _top_hit_close_match(company_name: str, items: List[Dict[str,Any]], akas: List[str]) -> Optional[Dict[str,Any]]:
    if not items: return None
    first = items[0]
    rd = _registered_domain(first.get("url"))
    if not rd: return None
    if any(rd.endswith(bh) for bh in BLOCKED_HOSTS): return None
    if _name_close_match(company_name, rd, akas):
        home = _pick_home(rd)
        if _head_ok(home):
            return {"domain_root": rd, "homepage_url": home, "score": 999, "why": "name-serp-top"}
    return None

# -------------------- Public resolver --------------------

def resolve_domain_waterfall(
    company_name: str,
    owners: List[str] | None,
    google_api_key: Optional[str],
    google_cse_id: Optional[str],
    hints: Optional[Dict[str, Any]] = None,
    akas: Optional[List[str]] = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    debug: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Waterfall resolution:
    0) If hints contain a domain, validate live + text; accept if ok (or keep as soft fallback if just live).
    1) CSE on company/DBA/aka; if top hit close-matches domain label, accept.
    2) Owner queries:
       - "<owner>" startup | "<owner>" company | "<owner>" "<company>"
       - If validates by text, accept.
    3) Score all residual candidates and pick the best (scored fallback).
    Returns {'domain_root','homepage_url','score','why'} or None.
    """
    owners = owners or []
    akas = [a for a in (akas or []) if a]  # list[str]
    hints = hints or {}

    # 0) Hints
    soft = None
    for key in ("domain","domain_root","website"):
        rd = _registered_domain(hints.get(key))
        if not rd: continue
        home = _pick_home(rd)
        txt = _homepage_text(home)
        if _text_validates(txt, company_name, owners, akas):
            return {"domain_root": rd, "homepage_url": home, "score": 1000, "why": "hint-validated"}
        if _head_ok(home):
            soft = {"domain_root": rd, "homepage_url": home, "score": 500, "why": "hint-live"}

    # 1) Company/DBA/AKA query (prefer top-hit when name ≈ label)
    base_q = f'(official site OR homepage) -site:linkedin.com -site:crunchbase.com -site:pitchbook.com -site:twitter.com -site:x.com'
    q1 = f'"{company_name}" {base_q}'
    items = _cse(google_api_key, google_cse_id, q1, num=max_candidates)
    top = _top_hit_close_match(company_name, items, akas)
    if debug: logd(debug, "q1 top", bool(top), "items", len(items))
    if top:
        return top
    # try AKA tokens if given
    for aka in akas:
        qaka = f'"{aka}" {base_q}'
        items2 = _cse(google_api_key, google_cse_id, qaka, num=max_candidates)
        top2 = _top_hit_close_match(aka, items2, akas)
        if debug: logd(debug, "qaka top", aka, bool(top2), "items", len(items2))
        if top2:
            return top2

    # 2) Owner queries
    owner_qs: List[str] = []
    for o in owners[:3]:
        o = o.strip()
        if not o: continue
        owner_qs += [f'"{o}" startup', f'"{o}" company', f'"{o}" "{company_name}"']
        for aka in akas:
            owner_qs += [f'"{o}" "{aka}"']
    if len(owners) >= 2:
        o1, o2 = owners[0].strip(), owners[1].strip()
        if o1 and o2:
            owner_qs += [f'"{o1}" "{o2}" startup', f'"{o1}" "{o2}" company']

    owner_candidates: Dict[str, Dict[str,Any]] = {}
    for q in owner_qs[:6]:
        its = _cse(google_api_key, google_cse_id, q, num=4)
        if debug: logd(debug, "owner q", q, "hits", len(its))
        for it in its:
            url = (it.get("url") or "").strip()
            rd = _registered_domain(url)
            if not rd or any(rd.endswith(bh) for bh in BLOCKED_HOSTS): 
                continue
            owner_candidates.setdefault(rd, {"url": url, "title": it.get("title","")})
    for rd, meta in owner_candidates.items():
        home = _pick_home(rd)
        txt = _homepage_text(home)
        if _text_validates(txt, company_name, owners, akas):
            return {"domain_root": rd, "homepage_url": home, "score": 700, "why": "owner-serp"}

    # 3) Score residuals
    pool: Dict[str, Dict[str,Any]] = {}
    for it in (items or []):
        url = (it.get("url") or "").strip()
        rd = _registered_domain(url)
        if not rd or any(rd.endswith(bh) for bh in BLOCKED_HOSTS): continue
        pool.setdefault(rd, {"url": url, "title": it.get("title", "")})
    pool.update(owner_candidates)

    scored: List[Tuple[int, Dict[str,Any]]] = []
    for rd, meta in pool.items():
        s, why = _score_candidate(company_name, rd, meta.get("title",""), meta.get("url",""), owners, akas)
        if s > 0:
            home = _pick_home(rd)
            scored.append((s, {"domain_root": rd, "homepage_url": home, "score": s, "why": f"scored:{why}"}))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    # Fallback to soft live hint if present
    return soft

# -------------------- CLI --------------------

def parse_args():
    ap = argparse.ArgumentParser(description="Resolve a company's domain (waterfall strategy).")
    ap.add_argument("company", help="Company/DBA name")
    ap.add_argument("owners", nargs="?", default="", help='Comma-separated owners (e.g. "Jane Doe, John Roe")')
    ap.add_argument("--aka", action="append", default=[], help="Also-known-as names (repeatable)")
    ap.add_argument("--hint-domain", default="", help="Hint: domain_root or website")
    ap.add_argument("--debug", action="store_true", help="Verbose logging to stderr")
    return ap.parse_args()

def main():
    args = parse_args()
    company = args.company.strip()
    owners = [o.strip() for o in (args.owners or "").split(",") if o.strip()]
    akas = [a.strip() for a in args.aka if a.strip()]
    hints = {}
    if args.hint_domain:
        hints["domain"] = args.hint_domain

    google_api_key = os.getenv("GOOGLE_API_KEY")
    google_cse_id = os.getenv("GOOGLE_CSE_ID")

    res = resolve_domain_waterfall(
        company_name=company,
        owners=owners,
        google_api_key=google_api_key,
        google_cse_id=google_cse_id,
        hints=hints,
        akas=akas,
        debug=args.debug or (os.getenv("BASELINE_DEBUG","").lower() in ("1","true","yes")),
    )

    if res:
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0)
    else:
        print(json.dumps({"error":"NO_RESULT","company":company}), ensure_ascii=False)
        sys.exit(2)

if __name__ == "__main__":
    main()