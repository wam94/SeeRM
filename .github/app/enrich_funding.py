# app/enrich_funding.py
from __future__ import annotations
import os, re, math, requests
from datetime import datetime
from typing import Dict, Any, List, Optional

AMOUNT_RE = re.compile(r'(?<![\d$])(?:USD\s*)?\$?\s*([0-9][\d,\.]*)\s*(billion|bn|million|mm|m|thousand|k)?', re.I)
ROUND_RE  = re.compile(r'\b(Pre-Seed|Seed|Angel|Series\s+[A-K]|Series\s+[A-K]\s+extension|Bridge|Convertible\s+Note|SAFE|Debt|Venture\s+Round|Equity\s+Round)\b', re.I)
DATE_RE   = re.compile(r'\b(20\d{2}|19\d{2})[-/\.](\d{1,2})[-/\.](\d{1,2})\b')
LED_BY_RE = re.compile(r'\b(led by|co-led by)\s+([^.;,\n]+)', re.I)
WITH_PARTICIPATION_RE = re.compile(r'\b(with participation from|including)\s+([^.;\n]+)', re.I)

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
    if not m: return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except Exception:
        return None

def extract_funding_from_text(text: str) -> Dict[str, Any]:
    """Heuristic extraction from a page body."""
    if not text:
        return {}
    res: Dict[str, Any] = {}
    # round type
    m = ROUND_RE.search(text)
    if m:
        res["last_round_type"] = m.group(1).title().replace("Series ", "Series ")
    # amount
    m = AMOUNT_RE.search(text)
    if m:
        amt = _to_usd(m.group(1), m.group(2))
        if amt and math.isfinite(amt):
            res["last_round_amount_usd"] = int(round(amt))
    # date
    date = _norm_date(text)
    if date:
        res["last_round_date"] = date
    # investors
    investors: List[str] = []
    for rx in (LED_BY_RE, WITH_PARTICIPATION_RE):
        mm = rx.search(text)
        if mm:
            investors += [p.strip(" .") for p in re.split(r',| and ', mm.group(2)) if p.strip()]
    if investors:
        # basic cleanup: remove trailing qualifiers
        investors = [re.sub(r'\(.*?\)$', '', i).strip() for i in investors]
        res["investors"] = sorted(set(investors))
    return res

# ---------- Crunchbase (optional) ----------

# def crunchbase_enrich(domain_root: Optional[str], name: Optional[str]) -> Dict[str, Any]:
#     key = os.getenv("CRUNCHBASE_API_KEY")
#     if not key:
#         return {}
#     H = {"X-cb-user-key": key, "Content-Type": "application/json"}
#     BASE = "https://api.crunchbase.com/api/v4"
#     # 1) search by domain first, then name
#     payloads = []
#     if domain_root:
#         payloads.append({
#             "field_ids": ["identifier","name","website","short_description"],
#             "query": [{"type":"predicate","field_id":"website","operator_id":"contains","values":[domain_root]}],
#             "limit": 1
#         })
#     if name:
#         payloads.append({
#             "field_ids": ["identifier","name","website","short_description"],
#             "query": [{"type":"predicate","field_id":"name","operator_id":"contains","values":[name]}],
#             "limit": 1
#         })

#     org_id = None
#     for body in payloads:
#         try:
#             r = requests.post(f"{BASE}/searches/organizations", headers=H, json=body, timeout=30)
#             if r.status_code != 200: 
#                 continue
#             data = r.json()
#             ents = (data.get("entities") or [])
#             if ents:
#                 org_id = ents[0]["identifier"].get("uuid") or ents[0]["identifier"].get("permalink")
#                 break
#         except Exception:
#             continue
#     if not org_id:
#         return {}

#     # 2) fetch entity fields
#     # NOTE: field_ids vary by plan; include several common ones. Missing fields are ignored.
#     body = {
#         "field_ids": [
#             "name","identifier","website","short_description",
#             "last_funding_type","last_funding_at","last_funding_total_usd",
#             "num_funding_rounds","founded_on",
#             "rank_org_company","rank_org","announced_on",
#             "funding_total_usd","investors","investors_names"
#         ]
#     }
#     try:
#         r = requests.post(f"{BASE}/entities/organizations/{org_id}", headers=H, json=body, timeout=30)
#         if r.status_code != 200:
#             return {}
#         ent = r.json().get("properties", {})
#         out: Dict[str, Any] = {}
#         # Normalize fields if present
#         def get(*keys):
#             for k in keys:
#                 if k in ent:
#                     return ent.get(k)
#             return None
#         out["total_funding_usd"]  = get("funding_total_usd")
#         out["last_round_type"]    = get("last_funding_type")
#         out["last_round_date"]    = get("last_funding_at") or get("announced_on")
#         out["last_round_amount_usd"] = get("last_funding_total_usd")
#         inv = get("investors_names") or get("investors")
#         if isinstance(inv, list):
#             out["investors"] = inv[:10]
#         elif isinstance(inv, str):
#             out["investors"] = [s.strip() for s in inv.split(",") if s.strip()][:10]
#         out["source_cb"] = True
#         return {k:v for k,v in out.items() if v not in (None, "", [])}
#     except Exception:
#         return {}

# ---------- Merger ----------

def merge_funding(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """Favor primary keys; fill gaps from secondary."""
    out = dict(primary or {})
    for k, v in (secondary or {}).items():
        if k not in out or out[k] in (None, "", [], 0):
            out[k] = v
    return out

def best_funding(org: Dict[str, Any], fetched_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    fetched_pages: list of { 'url', 'text', ... } where 'text' is page content.
    Returns normalized funding dict + sources.
    """
    # Heuristic from pages
    heur: Dict[str, Any] = {}
    sources: List[str] = []

    for p in fetched_pages:
        text = p.get("text") or ""
        if not text: 
            continue
        cand = extract_funding_from_text(text)
        if cand:
            heur = merge_funding(cand, heur)  # prefer first strong page (often company blog)
            sources.append(p.get("url",""))

    # Crunchbase (optional)
    cb = crunchbase_enrich(org.get("domain_root"), org.get("dba"))
    out = merge_funding(cb, heur) if cb else heur

    if sources:
        out["funding_sources"] = list(dict.fromkeys(sources))[:5]
    if out:
        out["funding_present"] = True
    return out
