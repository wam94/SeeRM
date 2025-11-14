# Messaging Consumer App

Automation that validates SeeRM’s Weekly News Digest artifacts and prepares
outbound/inbound-ready messaging at scale. This folder is now part of the
monorepo (`apps/messaging_consumer`).

## Quick start
```bash
cd apps/messaging_consumer
uv venv                   # or python -m venv .venv
uv pip install -e .[dev]
cp ../../docs/messaging_consumer/examples/weekly_news_digest.json fixtures/sample_report.json
pytest
python -m messaging_consumer.cli greetings acme "ceo@acme.com" \
  --first-names "Alex" --gift-link "https://gift.mercury.com"
```

## Layout
- `pyproject.toml` — project metadata + dependencies.
- `schema/weekly_news_digest.schema.json` — copy of the shared contract
  (keep this in sync with `docs/messaging_consumer/schema`).
- `src/messaging_consumer/contracts.py` — schema validation helpers.
- `src/messaging_consumer/notion_ingest.py` — lightweight helper to list
  generated reports from Notion (kept for reference until the build starts).
- `tests/` — contract + context regression tests.
- `docs/messaging_consumer/GREETING_WORKFLOW.md` — high-level flow for Raycast →
  Notion → OpenAI → Gmail.

## Development notes
- Secrets/env variables follow `docs/messaging_consumer/ENVIRONMENT.md`.
- `VOICE_MODEL_ID` defaults to `ft:gpt-4o-2024-08-06:mercury-technologies-inc:wam:CYJP0xCA`
  (override via env if you rotate models).
- To validate compatibility with SeeRM, drop real exports into
  `fixtures/` and re-run `pytest`.
- See `docs/messaging_consumer/NOTION_SCHEMA.md` for the property names used in
  the company + intel databases; keep this handy for the upcoming build.
- If you need to publish this app separately, copy the folder into a new repo
  (or use `docs/messaging_consumer/template/`) and keep the shared docs as a
  submodule.
