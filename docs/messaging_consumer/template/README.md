# Messaging Consumer Template

Copy this folder into a new repository to bootstrap the automation that reads SeeRM
artifacts and generates outbound/inbound-ready copy.

## Quick start
```bash
uv venv
uv pip install -e .
cp ../examples/weekly_news_digest.json fixtures/sample_report.json
pytest
```

The template assumes:
- Python 3.11+
- `uv` (or switch to Poetry/pip as needed)
- Access to the same secrets documented in `ENVIRONMENT.md`

## What's included
- `src/messaging_consumer/contracts.py` — validation helpers wrapping the
  JSON Schema located in `schema/weekly_news_digest.schema.json`.
- `src/messaging_consumer/notion_ingest.py` — stub client showing how to read
  report metadata from Notion.
- `tests/test_contract.py` — guards to ensure SeeRM exports stay compatible.
- `fixtures/sample_report.json` — drop in SeeRM-provided samples for local runs.

## Next steps
- Replace the stub messenger logic with the actual campaign workflow.
- Extend the schema + fixtures if SeeRM starts emitting new report types.
- Wire CI (GitHub Actions, etc.) to lint + test on every push.
