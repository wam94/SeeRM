from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd


def _num(series: Optional[pd.Series]) -> Optional[pd.Series]:
    """
    Coerce numeric from strings like '1,234' or '$1,234.56'.
    Returns None if input is None.
    """
    if series is None:
        return None
    s = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(s, errors="coerce")


def _pct(series: Optional[pd.Series]) -> Optional[pd.Series]:
    """
    Coerce percentage numbers; strips '%' if present.
    Note: our SQL exports 'balance_pct_delta_pct' in percentage points (e.g., 2.34 means 2.34%).
    """
    if series is None:
        return None
    s = series.astype(str).str.replace("%", "", regex=False).str.strip()
    return pd.to_numeric(s, errors="coerce")


def parse_csv_to_context(df: pd.DataFrame, top_n: int = 15) -> Dict[str, Any]:
    # Normalize column names (case/space-insensitive)
    cols = {c.lower().strip(): c for c in df.columns}

    def get(k):
        """Get column data if the key exists, otherwise return None."""
        return df[cols[k]] if k in cols else None

    # --- base columns ---
    callsign = get("callsign")

    # balance pct (already in percentage points from SQL; may have '%' chars in CSV)
    bal_pct = _pct(get("balance_pct_delta_pct"))

    # balance delta; fallback to curr - prev if needed
    bal_delta = _num(get("balance_delta"))
    if bal_delta is None or bal_delta.isna().all():
        curr = _num(get("curr_balance"))
        prev = _num(get("prev_balance"))
        if curr is not None and prev is not None:
            bal_delta = curr - prev

    # any_change (or infer from base flags)
    base_flags = {
        "is_new_account",
        "is_removed_account",
        "dba_changed",
        "website_changed",
        "owners_changed",
        "balance_changed",
    }
    any_change = get("any_change")
    if any_change is None and base_flags.issubset(set(cols.keys())):
        any_change = (
            get("is_new_account")
            | get("is_removed_account")
            | get("dba_changed")
            | get("website_changed")
            | get("owners_changed")
            | get("balance_changed")
        )

    # --- percent-based movers (gainers/losers) ---
    top_pct_gainers, top_pct_losers = [], []
    if callsign is not None and bal_pct is not None:
        base = pd.DataFrame(
            {
                "callsign": callsign,
                "pct": bal_pct,  # percentage points (e.g., 2.34 == 2.34%)
                "balance_delta": bal_delta,
            }
        )
        # Filter out rows where pct is NaN
        base = base.dropna(subset=["pct"])
        # Biggest increases (pct > 0)
        inc = base[base["pct"] > 0].sort_values("pct", ascending=False).head(top_n)
        # Biggest decreases (pct < 0)
        dec = base[base["pct"] < 0].sort_values("pct", ascending=True).head(top_n)
        top_pct_gainers = inc.to_dict(orient="records")
        top_pct_losers = dec.to_dict(orient="records")

    # --- product flips (JSON array per row) ---
    flips_key = None
    for candidate in ["product_flips_json", "product_flips", "flips_json"]:
        if candidate in cols:
            flips_key = cols[candidate]
            break
    starts, stops = [], []
    if flips_key:
        for _, row in df.iterrows():
            cs = row[cols.get("callsign", "callsign")] if callsign is not None else None
            raw = row[flips_key]
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

    # --- stats ---
    total_accounts = int(len(df))
    changed_accounts = (
        int(any_change.sum())
        if any_change is not None
        else len(top_pct_gainers) + len(top_pct_losers)
    )
    new_accounts = int(df[cols["is_new_account"]].sum()) if "is_new_account" in cols else 0
    removed_accounts = (
        int(df[cols["is_removed_account"]].sum()) if "is_removed_account" in cols else 0
    )

    return {
        "stats": {
            "total_accounts": total_accounts,
            "changed_accounts": changed_accounts,
            "new_accounts": new_accounts,
            "removed_accounts": removed_accounts,
            "total_product_flips": len(starts) + len(stops),
        },
        "top_pct_gainers": top_pct_gainers,
        "top_pct_losers": top_pct_losers,
        "product_starts": starts,
        "product_stops": stops,
        # intentionally no "stable accounts" list
    }
