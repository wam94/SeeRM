# scripts/domain_resolver.py
from __future__ import annotations
import argparse, re, time, json, math
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import requests
import tldextract
from difflib import SequenceMatcher

# We reuse your CSE client
from app.news_job import google_cse_search

DEBUG = (str.__contains__( (".").join([]), "never"))  # always False unless --debug
def logd(*a): 
    if DEBUG: print(*a)

# --- Hard filters ------------------------------------------------------------

BLOCK_HOSTS = {
    # Social / directories / aggregators
    "linkedin.com","x.com","twitter.com","facebook.com","instagram.com","youtube.com",
    "github.com","medium.com","substack.com","notion.so","notion.site","angel.co",
    "wikipedia.org","crunchbase.com","pitchbook.com","tracxn.com","cbinsights.com",
    "producthunt.com","read.cv","about.me","glassdoor.com","indeed.com",
    # Big media / local media
    "bloomberg.com","wsj.com","ft.com","reuters.com","apnews.com","yahoo.com",
    "techcrunch.com","theverge.com","forbes.com","fortune.com","businessinsider.com",
    "nytimes.com","washingtonpost.com","latimes.com","cnbc.com","cnn.com","bbc.com",
    "bizjournals.com","prnewswire.com","businesswire.com","globenewswire.com",
    "medium.com","substack.com",
}

GOOD_TLDS = { "com","ai","io","co","dev","net","app","org" }

# --- Utilities ---------------------------------------------------------------

def normalized_domain(url: str) -> Optional[str]:
    try:
        ext = tldextract.extract(url)
        if not ext.registered_domain:
            return None
        return ext.registered_domain.lower()
    except Exception:
        return None

def sld(domain_root: str) -> str:
    ext = tldextract.extract(domain_root)
    return (ext.domain or "").lower()

def head_ok(url: str, timeout: float = 6.0) -> bool:
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

def get_title(url: str, timeout: float = 6.0) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code >= 400:
            return None
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text, flags=re.I|re.S)
        if not m:
            return None
        return re.sub(r"\s+", " ", m.group(1)).strip()
    except Exception:
        return None

def seq_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

# --- Brand tokens ------------------------------------------------------------

REMOVE_WORDS = {
    "inc","inc.","llc","l.l.c.","corp","co","co.","company","holdings","group",
    "technologies","technology","systems","labs","ai","software","the"
}

def brand_tokens(company_name: str) -> List[str]:
    s = re.sub(r"[^a-z0-9\s\-&]+", " ", company_name.lower())
    words = [w for w in re.split(r"[\s\-&]+", s) if w]
    words = [w for w in words if w not in REMOVE_WORDS and len(w) >= 3]
    # If we ended with nothing (e.g., "The AI Company, Inc.") keep the biggest word
    if not words and company_name:
        letters = re.findall(r"[a-zA-Z]+", company_name)
        if letters:
            words = [max(letters, key=len).lower()]
    return list(dict.fromkeys(words))[:4]  # preserve order, cap

HOMEPAGE_HINTS = ("official site","homepage","home page","welcome","about us","our mission")

@dataclass
class Hit:
    url: str
    title: str
    snippet: str
    domain: str
    family: str

# --- Candidate generation ----------------------------------------------------

def cse_hits(q: str, family: str, api_key: str, cse_id: str, num: int = 6) -> List[Hit]:
    items: List[Dict[str, Any]] = google_cse_search(api_key, cse_id, q, num=num) or []
    hits: List[Hit] = []
    for it in items:
        url = (it.get("url") or it.get("link") or "").strip()
        if not url:
            continue
        dom = normalized_domain(url)
        if not dom:
            continue
        hits.append(
            Hit(
                url=url,
                title=(it.get("title") or "").strip(),
                snippet=(it.get("snippet") or "").strip(),
                domain=dom,
                family=family,
            )
        )
    return hits

# --- Scoring -----------------------------------------------------------------

def domain_brand_score(domain: str, tokens: List[str]) -> float:
    d = sld(domain)
    if not d:
        return 0.0
    best = max((seq_ratio(d, t) for t in tokens), default=0.0)
    # Hard gate: must be at least 0.72 similarity OR exact containment either way
    if best >= 0.72 or any((t in d or d in t) for t in tokens):
        return 50.0 * best  # strong base
    return 0.0  # reject later unless forced fallback

def path_penalty(url: str) -> float:
    # Prefer homepage-ish URLs
    try:
        path = re.sub(r"^https?://[^/]+", "", url)
    except Exception:
        path = "/"
    depth = path.strip("/").count("/")
    return -5.0 * max(0, depth)

def tld_bonus(domain: str) -> float:
    ext = tldextract.extract(domain).suffix.lower()
    return 5.0 if ext in GOOD_TLDS else 0.0

def homepage_hint_bonus(title: str, snippet: str) -> float:
    text = f"{title} • {snippet}".lower()
    return 8.0 if any(h in text for h in HOMEPAGE_HINTS) else 0.0

# --- Resolver ----------------------------------------------------------------

def resolve_domain(company: str,
                   owners_csv: Optional[str],
                   api_key: str,
                   cse_id: str,
                   debug: bool = False) -> Dict[str, Any]:
    global DEBUG
    DEBUG = debug

    tokens = brand_tokens(company)
    # Also try to spot a likely alias (two-word tail) e.g., "Aalo Atomics"
    alias = None
    if len(tokens) >= 2:
        alias = " ".join(tokens[:2]).title()

    logd("[tokens]", tokens, "| alias:", alias)

    queries: List[Tuple[str,str]] = []
    # Company-centric
    cname = company.strip()
    queries += [
        (f'"{cname}" official site', "company"),
        (f'"{cname}" homepage', "company"),
        (f'{tokens[0]} company site', "brand") if tokens else ("", "brand"),
    ]
    if alias and alias.lower() != cname.lower():
        queries.append((f'"{alias}" official site', "alias"))

    # Owners (but always with brand to reduce noise)
    owners = [o.strip() for o in (owners_csv or "").split(",") if o.strip()]
    for o in owners[:3]:
        if tokens:
            queries.append((f'"{o}" "{tokens[0]}" website', "owner"))
            queries.append((f'"{o}" {tokens[0]} startup website', "owner"))

    # Run CSE
    hits: List[Hit] = []
    for q, fam in queries:
        if not q.strip():
            continue
        try:
            hs = cse_hits(q, fam, api_key, cse_id, num=6)
            logd(f"[cse] {fam} q={q!r} -> {len(hs)} hits")
            hits.extend(hs)
        except Exception as e:
            logd("[cse error]", e)

    # Consolidate / score
    by_domain: Dict[str, Dict[str, Any]] = {}
    for h in hits:
        if h.domain in BLOCK_HOSTS:
            continue
        # Build/accumulate
        slot = by_domain.setdefault(h.domain, {"hits": [], "families": set(), "consensus": 0})
        slot["hits"].append(h)
        slot["families"].add(h.family)

    # Consensus counts (# of distinct query families)
    for dom, slot in by_domain.items():
        slot["consensus"] = len(slot["families"])

    # Score
    best_dom = None
    best_score = -1e9
    why = ""
    for dom, slot in by_domain.items():
        # Base brand gate
        bscore = domain_brand_score(dom, tokens)
        if bscore <= 0:
            # keep as candidate only if absolutely nothing else matches; we’ll consider later
            continue
        # Take top-most hit for features
        top = slot["hits"][0]
        score = bscore
        score += 10.0 * min(3, slot["consensus"])   # consensus boost
        score += tld_bonus(dom)
        score += homepage_hint_bonus(top.title, top.snippet)
        score += max(path_penalty(h.url) for h in slot["hits"])  # prefer shallow paths
        if score > best_score:
            best_score = score
            best_dom = dom
            why = f"brand-match {bscore:.1f} + consensus {slot['consensus']} + tld + homepage/path"

    # If we found a plausible brand-matching domain, validate it
    if best_dom:
        candidates = [f"https://{best_dom}", f"https://www.{best_dom}", f"http://{best_dom}"]
        for u in candidates:
            if head_ok(u):
                title = get_title(u) or ""
                if tokens and tokens[0] not in sld(best_dom) and tokens[0] not in title.lower():
                    # title check failed; keep but note
                    logd("[warn] weak title brand signal:", title[:120])
                result = {"domain_root": best_dom, "homepage_url": u, "score": int(round(best_score)), "why": why}
                print(json.dumps(result))
                return result

    # Deterministic fallback: try <brand>.{com,ai,io} in order
    if tokens:
        base = tokens[0]
        for tld in ("com","ai","io"):
            guess = f"{base}.{tld}"
            if guess in BLOCK_HOSTS:
                continue
            for u in (f"https://{guess}", f"https://www.{guess}", f"http://{guess}"):
                if head_ok(u):
                    result = {"domain_root": guess, "homepage_url": u, "score": 40, "why": "fallback brand.tld + head-ok"}
                    print(json.dumps(result))
                    return result

    # Last resort: choose the first non-blocked domain that responded (but mark low confidence)
    for h in hits:
        if h.domain in BLOCK_HOSTS:
            continue
        u = re.sub(r"#.*$", "", h.url)
        root = normalized_domain(u)
        if not root:
            continue
        if head_ok(f"https://{root}"):
            result = {"domain_root": root, "homepage_url": f"https://{root}", "score": 10, "why": "last-resort: first live non-blocked"}
            print(json.dumps(result))
            return result

    # Give up
    result = {"domain_root": None, "homepage_url": None, "score": 0, "why": "no viable candidate"}
    print(json.dumps(result))
    return result

# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("owners", nargs="?", default="")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    api_key = (args and True) and ( (lambda k: k)(None) )  # placeholder to keep linter quiet
    api_key = (api_key or "")  # noqa

    key = (json and True)  # noqa

    g_api_key = (requests and True) and (  # noqa
        (lambda: (requests and None))()
    ) or None

    # Pull from env (same as the rest of your repo)
    import os
    g_api_key = os.getenv("GOOGLE_API_KEY")
    cse_id    = os.getenv("GOOGLE_CSE_ID")
    if not (g_api_key and cse_id):
        print(json.dumps({"domain_root": None, "homepage_url": None, "score": 0, "why": "missing GOOGLE_API_KEY/GOOGLE_CSE_ID"}))
        return

    resolve_domain(args.company, args.owners, g_api_key, cse_id, debug=args.debug)

if __name__ == "__main__":
    main()