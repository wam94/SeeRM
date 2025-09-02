# scripts/domain_resolver.py
import os
from typing import Dict, Any, Optional, List
import tldextract

from app.news_job import google_cse_search
from app.dossier_baseline import (
    compute_domain_root, validate_domain_to_url,
    discover_domain_by_search, _BLOCKED_SITES, logd
)

def discover_domain_smart(name: str, owners: List[str], g_api_key: Optional[str], g_cse_id: Optional[str]) -> Optional[str]:
    """
    Enhanced domain discovery that tries multiple search strategies:
    1. Standard company name search
    2. Company + founder search
    3. Founder + company search  
    4. Just founder search (if owners provided)
    """
    if not (g_api_key and g_cse_id and name):
        return None
    
    candidates = []
    
    # Strategy 1: Standard company search
    logd(f"[SMART] Strategy 1: Standard company search for '{name}'")
    domain1 = discover_domain_by_search(name, g_api_key, g_cse_id)
    if domain1:
        candidates.append(("company_search", domain1))
        logd(f"[SMART] Found via company search: {domain1}")
    
    # Strategy 2-4: Owner-enhanced searches (if owners provided)
    if owners:
        for i, owner in enumerate(owners[:2]):  # Limit to first 2 owners to avoid too many API calls
            # Strategy 2: Company + founder
            logd(f"[SMART] Strategy 2.{i+1}: Company + founder search for '{name} {owner}'")
            domain2 = discover_domain_by_search(f'{name} {owner}', g_api_key, g_cse_id)
            if domain2:
                candidates.append(("company_founder_search", domain2))
                logd(f"[SMART] Found via company+founder search: {domain2}")
            
            # Strategy 3: Founder + company  
            logd(f"[SMART] Strategy 3.{i+1}: Founder + company search for '{owner} {name}'")
            domain3 = discover_domain_by_search(f'{owner} {name}', g_api_key, g_cse_id)
            if domain3:
                candidates.append(("founder_company_search", domain3))
                logd(f"[SMART] Found via founder+company search: {domain3}")
            
            # Strategy 4: Just founder
            logd(f"[SMART] Strategy 4.{i+1}: Founder-only search for '{owner}'")
            domain4 = discover_domain_by_search(f'{owner} (founder OR CEO OR CTO)', g_api_key, g_cse_id)
            if domain4:
                candidates.append(("founder_search", domain4))
                logd(f"[SMART] Found via founder search: {domain4}")
    
    if not candidates:
        logd("[SMART] No domains found via any strategy")
        return None
    
    # Scoring and selection logic
    # Prefer company-focused searches, but if multiple candidates, pick most common
    domain_counts = {}
    for strategy, domain in candidates:
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    
    # Sort by frequency first, then by strategy preference
    strategy_priority = {
        "company_search": 4,
        "company_founder_search": 3, 
        "founder_company_search": 2,
        "founder_search": 1
    }
    
    best_domain = None
    best_score = 0
    
    for strategy, domain in candidates:
        frequency = domain_counts[domain]
        priority = strategy_priority[strategy]
        score = frequency * 10 + priority  # Weight frequency more heavily
        
        logd(f"[SMART] Scoring {domain}: frequency={frequency}, priority={priority}, score={score}")
        
        if score > best_score:
            best_score = score
            best_domain = domain
    
    logd(f"[SMART] Selected domain: {best_domain} (score: {best_score})")
    return best_domain

def probe(name: str, owners_csv: str = ""):
    owners = [s.strip() for s in owners_csv.split(",") if s.strip()]
    g_key = os.getenv("GOOGLE_API_KEY")
    g_cse = os.getenv("GOOGLE_CSE_ID")

    # smart discovery
    root = discover_domain_smart(name, owners, g_key, g_cse)
    url  = validate_domain_to_url(root) if root else None

    print(f"Name: {name}")
    print(f"Owners: {owners}")
    print(f"Chosen domain: {root}")
    print(f"Validated URL: {url}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.domain_resolver \"Company Name\" [\"Owner One,Owner Two\"]")
        raise SystemExit(2)
    name = sys.argv[1]
    owners = sys.argv[2] if len(sys.argv) > 2 else ""
    probe(name, owners)
