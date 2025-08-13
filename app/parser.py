from __future__ import annotations
import json
import pandas as pd
from typing import Dict, Any, List

def parse_csv_to_context(df: pd.DataFrame, top_n: int = 15) -> Dict[str, Any]:
    cols = {c.lower().strip(): c for c in df.columns}
    get = lambda k: df[cols[k]] if k in cols else None

    # any_change or infer
    any_change = get("any_change")
    base_flags = {"is_new_account","is_removed_account","dba_changed","website_changed","owners_changed","balance_changed"}
    if any_change is None and base_flags.issubset(set(cols.keys())):
        any_change = (get("is_new_account") | get("is_removed_account") | get("dba_changed") | get("website_changed") | get("owners_changed") | get("balance_changed"))

    callsign = get("callsign") if "callsign" in cols else None

    # Balance movers
    movers = []
    if callsign is not None and "balance_delta" in cols:
        bd = pd.to_numeric(df[cols["balance_delta"]], errors="coerce")
        pct = pd.to_numeric(df[cols.get("balance_pct_delta_pct","balance_pct_delta_pct")], errors="coerce") if "balance_pct_delta_pct" in cols else None
        tmp = pd.DataFrame({"callsign": callsign, "balance_delta": bd, "balance_pct_delta_pct": pct})
        tmp = tmp.dropna(subset=["balance_delta"]).reindex(bd.abs().sort_values(ascending=False).index)
        movers = tmp.head(top_n).to_dict(orient="records")

    # Product flips JSON
    flips_col_name = None
    for candidate in ["product_flips_json", "product_flips", "flips_json"]:
        if candidate in cols:
            flips_col_name = cols[candidate]
            break

    starts, stops = [], []
    if flips_col_name:
        for _, row in df.iterrows():
            cs = row[cols.get("callsign","callsign")] if callsign is not None else None
            raw = row[flips_col_name]
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

    total_accounts = int(len(df))
    changed_accounts = int(any_change.sum()) if any_change is not None else len(movers)
    new_accounts = int(df[cols["is_new_account"]].sum()) if "is_new_account" in cols else 0
    removed_accounts = int(df[cols["is_removed_account"]].sum()) if "is_removed_account" in cols else 0
    unchanged = []
    if callsign is not None and any_change is not None:
        unchanged = df.loc[~any_change, cols["callsign"]].tolist()

    return {
        "stats": {
            "total_accounts": total_accounts,
            "changed_accounts": changed_accounts,
            "new_accounts": new_accounts,
            "removed_accounts": removed_accounts,
            "total_product_flips": len(starts) + len(stops),
        },
        "top_movers": movers,
        "product_starts": starts,
        "product_stops": stops,
        "unchanged": unchanged,
    }
