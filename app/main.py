from __future__ import annotations
import os, io
import pandas as pd
from datetime import datetime
from app.gmail_client import build_service, search_messages, get_message, extract_csv_attachments, send_html_email
from app.parser import parse_csv_to_context
from app.digest_render import render_digest

def getenv(name: str, default=None):
    val = os.getenv(name)
    return default if val is None or val == "" else val

def extract_new_callsigns(df: pd.DataFrame) -> None:
    """
    Extract callsigns of new accounts and write to /tmp/new_callsigns.txt
    for the news workflow to trigger baseline generation.
    """
    # Normalize column names (case/space-insensitive)
    cols = {c.lower().strip(): c for c in df.columns}
    
    if "is_new_account" not in cols or "callsign" not in cols:
        print("Missing required columns for new account detection (is_new_account, callsign)")
        return
    
    # Filter for new accounts
    new_accounts = df[df[cols["is_new_account"]].astype(bool)]
    
    if len(new_accounts) == 0:
        print("No new accounts found in this batch")
        return
    
    # Extract callsigns
    new_callsigns = new_accounts[cols["callsign"]].astype(str).str.strip().str.lower()
    new_callsigns = new_callsigns[new_callsigns != ""].tolist()
    
    if not new_callsigns:
        print("No valid callsigns found for new accounts")
        return
    
    # Write to trigger file for news workflow
    trigger_file = "/tmp/new_callsigns.txt"
    try:
        with open(trigger_file, "w") as f:
            f.write(",".join(new_callsigns))
        print(f"Found {len(new_callsigns)} new accounts: {', '.join(new_callsigns)}")
        print(f"Wrote new callsigns to {trigger_file}")
    except Exception as e:
        print(f"Failed to write new callsigns file: {e}")

def load_config():
    return {
        "gmail": {
            "query": getenv("GMAIL_QUERY", "from:metabase@mercury.com subject:\"Alert: SeeRM Master Query has results\" has:attachment filename:csv newer_than:10d"),
            "attachment_regex": getenv("ATTACHMENT_REGEX", r".*\.csv$"),
            "user": getenv("GMAIL_USER"),
        },
        "digest": {
            "to": getenv("DIGEST_TO"),
            "cc": getenv("DIGEST_CC"),
            "bcc": getenv("DIGEST_BCC"),
            "subject": getenv("DIGEST_SUBJECT", None),
            "top_movers": int(getenv("TOP_MOVERS", "15")),
        }
    }

def main():
    cfg = load_config()
    client_id = os.environ["GMAIL_CLIENT_ID"]
    client_secret = os.environ["GMAIL_CLIENT_SECRET"]
    refresh_token = os.environ["GMAIL_REFRESH_TOKEN"]
    user = cfg["gmail"]["user"] or os.environ["GMAIL_USER"]
    query = cfg["gmail"]["query"]
    attachment_regex = cfg["gmail"]["attachment_regex"]

    service = build_service(client_id, client_secret, refresh_token)

    msgs = search_messages(service, user, query, max_results=5)
    if not msgs:
        raise SystemExit("No messages found for query. Check GMAIL_QUERY or wait for the Metabase email.")

    df = None
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if not atts:
            continue
        name, data = atts[0]
        df = pd.read_csv(io.BytesIO(data))
        break

    if df is None:
        raise SystemExit("Found messages but no CSV attachment matched ATTACHMENT_REGEX.")

    context = parse_csv_to_context(df, top_n=cfg["digest"]["top_movers"])
    if cfg["digest"]["subject"]:
        context["subject"] = cfg["digest"]["subject"]

    # Extract new callsigns for baseline generation
    extract_new_callsigns(df)

    html = render_digest(context)

    to = cfg["digest"]["to"] or user
    cc = cfg["digest"]["cc"]
    bcc = cfg["digest"]["bcc"]
    subject = context.get("subject", f"Client Weekly Digest — {datetime.utcnow().date()}")

    send_html_email(service, user, to, subject, html, cc=cc, bcc=bcc)
    print("Digest sent to", to)

if __name__ == "__main__":
    main()
