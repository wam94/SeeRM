from __future__ import annotations
import json
import pandas as pd
from typing import Dict, Any, List

def parse_csv_to_context(df: pd.DataFrame, top_n: int = 15) -> Dict[str, Any]:
    # Normalize column names
    cols = {c.lower().strip(): c for c in df.columns}
    get = lambda k: df[cols[k]] if k in cols else None

    # any_change (or infer)
    base_flags = {"is_new_account","is_removed_account","dba_changed","website_changed","owners_changed","balance_changed"}
    any_change = get("any_change")
    if any_change is None and base_flags.issubset(set(cols.keys())):
        any_change = (get("is_new_account") | get("is_removed_account") |
                      get("dba_changed") | get("website_changed") |
                      get("owners_changed") | get("balance_changed"))

    # Core series
    callsign = get("callsign")
    bal_delta = pd.to_numeric(get("balance_delta"), errors="coerce") if "balance_delta" in cols else None
    pct_col = "balance_pct_delta_pct"
    bal_pct = pd.to_numeric(get(pct_col), errors="coerce") if pct_col in cols else None

    # ---- NEW: percent-based movers (most positive & most negative) ----
    top_pct_gainers, top_pct_losers = [], []
    if callsign is not None and bal_pct is not None:
        base = pd.DataFrame({
            "callsign": callsign,
            "pct": bal_pct,                   # already in percentage units (e.g., 2.34 means 2.34%)
            "balance_delta": bal_delta
        })
        # biggest increases
        inc = base[base["pct"] > 0].sort_values("pct", ascending=False).head(top_n)
        # biggest decreases
        dec = base[base["pct"] < 0].sort_values("pct", ascending=True).head(top_n)
        top_pct_gainers = inc.to_dict(orient="records")
        top_pct_losers  = dec.to_dict(orient="records")

    # (Optional) still compute $-based movers if you like that view too
    top_movers_dollars = []
    if callsign is not None and bal_delta is not None:
        tmp = pd.DataFrame({"callsign": callsign, "balance_delta": bal_delta, "balance_pct_delta_pct": bal_pct})
        tmp = tmp.dropna(subset=["balance_delta"]).reindex(tmp["balance_delta"].abs().sort_values(ascending=False).index)
        top_movers_dollars = tmp.head(top_n).to_dict(orient="records")

    # Product flips (unchanged)
    flips_col = None
    for candidate in ["product_flips_json", "product_flips", "flips_json"]:
        if candidate in cols:
            flips_col = df[cols[candidate]]
            break
    starts, stops = [], []
    if flips_col is not None:
        for _, row in df.iterrows():
            cs = row[cols.get("callsign", "callsign")]
            raw = row[cols[candidate]]
            if pd.isna(raw):
                continue
            try:
                arr = raw if isinstance(raw, list) else json.loads(raw)
            except Exception:
                continue
            for obj in arr:
                prod = obj.get("product")
                from_v = obj.get("from")
                to_v = obj.get("to")
                if from_v == 0 and to_v == 1:
                    starts.append({"callsign": cs, "product": prod})
                elif from_v == 1 and to_v == 0:
                    stops.append({"callsign": cs, "product": prod})

    # Stats
    total_accounts = int(len(df))
    changed_accounts = int(any_change.sum()) if any_change is not None else len(top_movers_dollars)
    new_accounts = int(df[cols["is_new_account"]].sum()) if "is_new_account" in cols else 0
    removed_accounts = int(df[cols["is_removed_account"]].sum()) if "is_removed_account" in cols else 0
    unchanged = df.loc[~any_change, cols["callsign"]].tolist() if (callsign is not None and any_change is not None) else []

    return {
        "stats": {
            "total_accounts": total_accounts,
            "changed_accounts": changed_accounts,
            "new_accounts": new_accounts,
            "removed_accounts": removed_accounts,
            "total_product_flips": len(starts) + len(stops),
        },
        # NEW percent-based sections:
        "top_pct_gainers": top_pct_gainers,
        "top_pct_losers": top_pct_losers,
        # Keep $-view if you still want it:
        "top_movers": top_movers_dollars,
        "product_starts": starts,
        "product_stops": stops,
        "unchanged": unchanged,
    }
