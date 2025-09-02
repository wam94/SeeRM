# app/domain_resolver.py
from __future__ import annotations
import os, re, requests, tldextract
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
from difflib import SequenceMatcher
from trafilatura import fetch_url, extract as trafi_extract

BLOCKED_HOSTS = {
    "linkedin.com","x.com","twitter.com","facebook.com","instagram.com","youtube.com",
    "github.com","medium.com","substack.com","notion.so","notion.site",
    "docs.google.com","wikipedia.org","angel.co","bloomberg.com","crunchbase.com","pitchbook.com",
    "owler.com","zoominfo.com","rocketreach.co","builtwith.com"
}
TRUST_TLDS = {"com","io","ai","co","net","org","app","dev","tech"}

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

def _head_ok(url: str, timeout: int = 6) -> bool:
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

def _pick_home(rd: str) -> str:
    for u in (f"https://{rd}", f"https://www.{rd}", f"http://{rd}"):
        if _head_ok(u):
            return u
    return f"https://{rd}"  # fallback

def _homepage_text(url: str, timeout: int = 10) -> str:
    try:
        html = fetch_url(url, timeout=timeout)
        if not html: return ""
        txt = trafi_extract(html, include_comments=False, include_tables=False) or ""
        return txt[:25000]
    except Exception:
        return ""

def _norm_name(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (s or "").lower()).strip()

def _host_label(rd: str) -> str:
    # returns the second-level label (e.g., "aalo" from "aalo.com")
    return rd.split(".")[0] if rd else ""

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _name_close_match(company_name: str, rd: str, threshold: float = 0.82) -> bool:
    a = _norm_name(company_name).replace(" ", "")
    b = _host_label(rd)
    return _similar(a, b) >= threshold

def _text_validates(text: str, company_name: str, owners: List[str]) -> bool:
    if not text: return False
    t = text.lower()
    cname = _norm_name(company_name)
    if cname and all(tok in t for tok in cname.split()[:1]):  # at least first token
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

def _score_candidate(company_name: str, rd: str, title: str, url: str, owners: List[str]) -> Tuple[int,str]:
    score = 0
    why = []
    if any(rd.endswith(bh) for bh in BLOCKED_HOSTS):
        return (-999, "blocked-host")
    tld = rd.split(".")[-1] if "." in rd else ""
    if tld in TRUST_TLDS:
        score += 8;  why.append("trust-tld")

    # name similarity
    if _name_close_match(company_name, rd):
        score += 30; why.append("name-match")

    # path semantics
    path = (urlparse(url).path or "").lower()
    if any(p in path for p in ("/about","/careers","/press","/contact","/team")):
        score += 16; why.append("about/careers/press/contact")

    # homepage health + text validation
    home = _pick_home(rd)
    ok = _head_ok(home) or _head_ok("https://www."+rd) or _head_ok("http://"+rd)
    if ok:
        score += 14; why.append("head-ok")
    txt = _homepage_text(home)
    if _text_validates(txt, company_name, owners):
        score += 22; why.append("text-match")

    return score, ",".join(why)

def _cse(google_api_key: Optional[str], google_cse_id: Optional[str], q: str, num: int = 6) -> List[Dict[str,Any]]:
    if not (google_api_key and google_cse_id): return []
    try:
        from app.news_job import google_cse_search
        return google_cse_search(google_api_key, google_cse_id, q, num=num)
    except Exception:
        return []

def _top_hit_close_match(company_name: str, items: List[Dict[str,Any]]) -> Optional[Dict[str,Any]]:
    if not items: return None
    first = items[0]
    rd = _registered_domain(first.get("url"))
    if not rd: return None
    if any(rd.endswith(bh) for bh in BLOCKED_HOSTS): return None
    if _name_close_match(company_name, rd):
        home = _pick_home(rd)
        if _head_ok(home):
            return {"domain_root": rd, "homepage_url": home, "score": 999, "why": "name-serp-top"}
    return None

def resolve_domain_waterfall(
    company_name: str,
    owners: List[str] | None,
    google_api_key: Optional[str],
    google_cse_id: Optional[str],
    hints: Optional[Dict[str, Any]] = None,
    max_candidates: int = 8,
) -> Optional[Dict[str, Any]]:
    """
    Waterfall resolution:
    0) If hints contain a domain, validate live + text; accept if ok.
    1) CSE on company/DBA; if top hit close matches, accept.
    2) Owner queries (each): "<owner>" startup | "<owner>" company | "<owner>" "<company>"
       If a candidate validates, accept.
    3) Score all remaining candidates and pick the best.
    Returns {'domain_root','homepage_url','score','why'} or None.
    """
    owners = owners or []
    hints = hints or {}

    # 0) Hints
    for key in ("domain","domain_root","website"):
        rd = _registered_domain(hints.get(key))
        if not rd: continue
        home = _pick_home(rd)
        txt = _homepage_text(home)
        if _text_validates(txt, company_name, owners):
            return {"domain_root": rd, "homepage_url": home, "score": 1000, "why": "hint-validated"}
        # If hint is live but text is weak, keep as soft fallback
        if _head_ok(home):
            soft = {"domain_root": rd, "homepage_url": home, "score": 500, "why": "hint-live"}
        else:
            soft = None
        # don't early-return yet; try better evidence first
    # keep soft fallback if set
    soft_hint = locals().get("soft", None)

    # 1) Company/DBA query
    q1 = f'"{company_name}" (official site OR homepage) -site:linkedin.com -site:crunchbase.com -site:pitchbook.com -site:twitter.com -site:x.com'
    items = _cse(google_api_key, google_cse_id, q1, num=max_candidates)
    top = _top_hit_close_match(company_name, items)
    if top:
        return top

    # 2) Owner queries (individual and combined)
    owner_qs: List[str] = []
    for o in owners[:3]:
        o = o.strip()
        if not o: continue
        owner_qs += [f'"{o}" startup', f'"{o}" company', f'"{o}" "{company_name}"']
    if len(owners) >= 2:
        o1, o2 = owners[0].strip(), owners[1].strip()
        if o1 and o2:
            owner_qs += [f'"{o1}" "{o2}" startup', f'"{o1}" "{o2}" company']

    owner_candidates: Dict[str, Dict[str,Any]] = {}
    for q in owner_qs[:6]:
        for it in _cse(google_api_key, google_cse_id, q, num=4):
            url = (it.get("url") or "").strip()
            rd = _registered_domain(url)
            if not rd or any(rd.endswith(bh) for bh in BLOCKED_HOSTS): 
                continue
            if rd not in owner_candidates:
                owner_candidates[rd] = {"url": url, "title": it.get("title","")}
    for rd, meta in owner_candidates.items():
        home = _pick_home(rd)
        txt = _homepage_text(home)
        if _text_validates(txt, company_name, owners):
            return {"domain_root": rd, "homepage_url": home, "score": 700, "why": "owner-serp"}

    # 3) Score all residuals (company + owner pools)
    pool: Dict[str, Dict[str,Any]] = {}
    for it in (items or []):
        url = (it.get("url") or "").strip()
        rd = _registered_domain(url)
        if not rd or any(rd.endswith(bh) for bh in BLOCKED_HOSTS): continue
        pool.setdefault(rd, {"url": url, "title": it.get("title", "")})
    pool.update({rd:meta for rd,meta in owner_candidates.items()})

    scored: List[Tuple[int, Dict[str,Any]]] = []
    for rd, meta in pool.items():
        s, why = _score_candidate(company_name, rd, meta.get("title",""), meta.get("url",""), owners)
        if s > 0:
            home = _pick_home(rd)
            scored.append((s, {"domain_root": rd, "homepage_url": home, "score": s, "why": f"scored:{why}"}))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    # fallback: accept soft live hint if we had one
    return soft_hint