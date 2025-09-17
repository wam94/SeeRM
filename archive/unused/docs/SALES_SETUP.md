# Sales User Setup (One-Pager)

Use this if you just want the Weekly News Digest without messing with code.

## 1) Install

Option A (recommended): pipx

```
pipx install seerm        # if published, or
pipx install /path/to/dist/seerm-<version>-py3-none-any.whl
```

Option B (local)

```
pip install -r requirements.txt
pip install -e .
```

## 2) Run the Setup Wizard

```
seerm setup run
```

What you’ll need:
- Your Gmail address. Wizard can help you get the refresh token (one-time login).
- The subject/query used for the weekly CSV email (your admin will share this).
- Optional: Notion API key + database IDs to save reports in Notion.
- Optional: OpenAI/Google keys for enhanced summaries.

The wizard saves your settings to `~/.seerm/.env`.

## 3) Generate the Weekly News Digest

```
seerm reports weekly-news            # default 7 days
seerm reports weekly-news --days 14  # custom lookback
```

If email sending fails, an HTML file is saved to `./reports/email_fallbacks/`.

## 4) Diagnostics (if something looks off)

```
seerm doctor
```

Shows config, Gmail/Notion health, CSV availability, and fallback files.

## 5) Update

```
seerm update-check
pipx upgrade seerm        # if using pipx
```

## Notes
- If you don’t use Gmail for ingestion, provide a local file path when the wizard asks for CSV.
- If Notion is configured, a Notion page is created for each report and the ID is printed after run.

