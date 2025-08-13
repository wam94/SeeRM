from __future__ import annotations
import os, io
from datetime import datetime
import pandas as pd

from app.gmail_client import build_service, search_messages, get_message, extract_csv_attachments, send_html_email
from app.news_job import fetch_csv_by_subject  # reuse helper from news_job.py

def getenv(n, d=None):
    v = os.getenv(n)
    return d if v in (None, "") else v

BASE_PROMPT = """You are an analyst creating a concise company dossier for internal use.
Summarize in crisp, factual language. Avoid hype. Use bullet points when helpful.

Company: {dba} ({callsign})
Website: {website}
Domain: {domain_root}
AKA: {aka_names}
Owners: {owners}
Industry tags: {industry_tags}
HQ: {hq_city}, {hq_region}, {hq_country}
Links: LinkedIn={linkedin_url}, Twitter/X={twitter_handle}, Crunchbase={crunchbase_url}

Recent items:
{recent_items}

Write a baseline with sections:
- What they do (2–3 sentences)
- Product(s) and target users (bullets)
- Go-to-market notes (bullets)
- Notable news (bullets, 3–6 items)
- Risks or unknowns (bullets)
Finish with a short "How to engage next" suggestion (1–2 bullets).
"""

def openai_summarize(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # fallback: just return the prompt tail (recent items) trimmed
        tail = prompt.split("Recent items:", 1)[-1].strip()
        return "Baseline (no LLM key configured yet):\n" + "\n".join(tail.splitlines()[:12])
    try:
        import openai  # pip install openai>=1.0.0 if you plan to use this locally
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_CHAT_MODEL","gpt-4o-mini"),
            messages=[{"role":"user","content":prompt}],
            temperature=0.25,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(LLM error: {e})"

def load_latest_weekly_csv(service, user, q, attachment_regex):
    msgs = search_messages(service, user, q, max_results=5)
    for m in msgs:
        msg = get_message(service, user, m["id"])
        atts = extract_csv_attachments(service, user, msg, attachment_regex)
        if atts:
            name, data = atts[0]
            return pd.read_csv(io.BytesIO(data))
    return None

def make_baseline(org: dict, recent_items: list[dict]) -> str:
    items_text = "\n".join([f"- {i.get('title')} — {i.get('source')} ({i.get('url')})" for i in recent_items[:8]])
    prompt = BASE_PROMPT.format(
        dba=org.get("dba") or "",
        callsign=org.get("callsign") or "",
        website=org.get("website") or "",
        domain_root=org.get("domain_root") or "",
        aka_names=org.get("aka_names") or "",
        owners=", ".join(org.get("owners") or []),
        industry_tags=org.get("industry_tags") or "",
        hq_city=org.get("hq_city") or "",
        hq_region=org.get("hq_region") or "",
        hq_country=org.get("hq_country") or "",
        linkedin_url=org.get("linkedin_url") or "",
        twitter_handle=org.get("twitter_handle") or "",
        crunchbase_url=org.get("crunchbase_url") or "",
        recent_items=items_text or "(no recent items available)",
    )
    return openai_summarize(prompt)

def main():
    # Inputs
    callsigns = [c.strip().lower() for c in getenv("BASELINE_CALLSIGNS","").split(",") if c.strip()]
    if not callsigns:
        raise SystemExit("Set BASELINE_CALLSIGNS to a comma-separated list of callsigns.")

    svc = build_service(
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
    )
    user = getenv("GMAIL_USER") or os.environ["GMAIL_USER"]

    # Load profile + weekly CSVs
    profile_subject = getenv("NEWS_PROFILE_SUBJECT","Org Profile — Will Mitchell")
    df_profile = fetch_csv_by_subject(svc, user, profile_subject)
    weekly = load_latest_weekly_csv(svc, user, getenv("NEWS_GMAIL_QUERY",""), getenv("ATTACHMENT_REGEX", r".*\.csv$"))
    if df_profile is None and weekly is None:
        raise SystemExit("Need at least one CSV (profile or weekly) to build a baseline.")

    # Build simple lookup from profile (preferred) or weekly
    prof = {}
    def lower_cols(df): return {c.lower().strip(): c for c in df.columns}
    if df_profile is not None:
        pcols = lower_cols(df_profile)
        for _, r in df_profile.iterrows():
            cs = str(r[pcols.get("callsign")]).strip().lower()
            if cs:
                prof[cs] = {k: r[pcols.get(k)] for k in pcols}

    # Pull recent items from latest intel email? For now, use none (or you can paste from prior run)
    # (Alternatively, you can read cached items if you decide to persist them.)
    recent_items = []

    # Prepare and optionally email each baseline
    out_html = ["<html><body><h2>Baselines</h2>"]
    for cs in callsigns:
        # merge row from profile or weekly
        row = {}
        if cs in prof:
            row = {
                "callsign": prof[cs].get("callsign"),
                "dba": prof[cs].get("dba"),
                "website": prof[cs].get("website"),
                "domain_root": prof[cs].get("domain_root"),
                "aka_names": prof[cs].get("aka_names"),
                "owners": (prof[cs].get("beneficial_owners") or "").split(", "),
                "linkedin_url": prof[cs].get("linkedin_url"),
                "twitter_handle": prof[cs].get("twitter_handle"),
                "crunchbase_url": prof[cs].get("crunchbase_url"),
                "industry_tags": prof[cs].get("industry_tags"),
                "hq_city": prof[cs].get("hq_city"),
                "hq_region": prof[cs].get("hq_region"),
                "hq_country": prof[cs].get("hq_country"),
            }
        baseline = make_baseline(row, recent_items)
        out_html.append(f"<h3>{row.get('callsign') or cs}</h3><pre>{baseline}</pre><hr/>")
    out_html.append("</body></html>")
    html = "\n".join(out_html)

    if getenv("PREVIEW_ONLY","true").lower() in ("1","true","yes"):
        print(html[:2000])
        return

    to = getenv("DIGEST_TO") or user
    send_html_email(svc, user, to, f"Baselines — {datetime.utcnow().date()}", html)
    print("Baselines emailed to", to)

if __name__ == "__main__":
    main()
