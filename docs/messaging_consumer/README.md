# Messaging Consumer Shared Docs

This folder captures the contracts, env conventions, and background material for
the messaging automation that consumes SeeRM output (Notion intelligence pages,
emails, JSON exports, etc.). The actual implementation now lives in
`apps/messaging_consumer/`.

## Using the built-in app
```
cd apps/messaging_consumer
uv venv
uv pip install -e .[dev]
pytest
```

See `apps/messaging_consumer/README.md` for day-to-day development notes.

## Exporting to a separate repo
If you still want to spin the project out:
1. Copy `apps/messaging_consumer/` (or the lighter
   `docs/messaging_consumer/template/`) into a new repository.
2. Keep this directory (`docs/messaging_consumer/`) synced as a git submodule or
   copy the schema + examples over whenever SeeRM changes them.

The template includes:
- `pyproject.toml` configured for Poetry/UV-compatible builds.
- `src/messaging_consumer/contracts.py` that loads the JSON Schema bundled
  here and validates incoming artifacts.
- `src/messaging_consumer/notion_ingest.py` stub showing how to pull Notion
  blocks and convert them into the contract payload.
- `tests/` proving the contract stays compatible with SeeRM output.
- `GREETING_WORKFLOW.md` documents how the Raycast-driven greeting generator
  stitches together Notion, OpenAI, and Gmail.
- `NOTION_SCHEMA.md` (this directory) documents the properties required for
  callsign-based lookups in both Notion databases.

## Data hand-off
See `OUTPUT_CONTRACT.md` for the formal schema + email/Notion mapping.
High level:
- SeeRM exports a Weekly News Digest JSON payload to shared storage (S3, GCS,
  Notion export, webhook, etc.).
- Messaging repo polls or receives a webhook, validates payload against the
  schema, enriches with any campaign context, then renders outbound messages.
- A thin `artifact_index.json` (optional) records which report IDs were
  already consumed so the two systems stay idempotent.

## Secrets + env vars
`ENVIRONMENT.md` lists the canonical variable names that both repos should use
(e.g. `NOTION_API_KEY`, `OPENAI_API_KEY`, `DIGEST_TO`). Store the actual values
in your secrets manager and inject them independently into each repo's runtime.

## Next steps
- Finalize how SeeRM exports artifacts (Notion page export, webhook, S3, etc.).
- Implement the Notion/email readers inside the new repo using the contract
  scaffolding (or extend `apps/messaging_consumer` directly).
- Expand the shared schema if new report types are introduced.
