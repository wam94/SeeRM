# Weekly Digest Automation — Starter Repo

This repository pulls your **Metabase weekly diff email** from Gmail, renders a polished HTML digest, and sends it back to you. It uses:
- **GitHub Actions** (cron) for scheduling
- **Gmail API** for fetching the CSV and sending the digest
- **Pandas + Jinja2** for parsing and rendering

## Quick start

1. **Create a Google Cloud OAuth Client (Desktop or Web)** for Gmail API.
   - Enable Gmail API.
   - Get **CLIENT_ID** and **CLIENT_SECRET**.
   - Perform a one-time OAuth flow to obtain a **REFRESH_TOKEN** for your mailbox.
   - Required scopes: `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/gmail.send`.

2. **Add GitHub Secrets** to your repo:
   - `GMAIL_CLIENT_ID`
   - `GMAIL_CLIENT_SECRET`
   - `GMAIL_REFRESH_TOKEN`
   - `GMAIL_USER` (your email address)
   - Optional: `DIGEST_TO` (override recipient), `DIGEST_CC`, `DIGEST_BCC`

3. **Metabase**: Schedule your SQL Question (the weekly diff that returns ALL orgs + `any_change` flag) to email **CSV** to your mailbox.
   - Use a tight subject, e.g. `Weekly Diff — Will Mitchell`
   - From address: the Metabase sender in your org.

4. **Configure app/config.yaml** (see defaults) or rely on env vars.

5. The workflow runs every Monday 9am ET (13:00 UTC). You can also **Run workflow** manually.

## Notes
- The program never writes customer data to the repo; it processes in memory.
- If you want to cache last week's snapshot instead of Metabase diffs, swap to the `pipeline_mode: external_diff` later.
