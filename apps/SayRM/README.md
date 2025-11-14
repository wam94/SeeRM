# SayRM

SayRM is a local automation service that centralises:

1. Pulling Notion/company context and summarising it into “briefs”.
2. Capturing (future) internal usage snapshots.
3. Drafting emails from templates with an LLM.
4. Logging everything to SQLite so we can label outputs for fine-tuning later.

The service is intentionally local-only; Raycast commands will call the HTTP
endpoints exposed on `http://127.0.0.1:8070`.

## Running the service

```bash
cd /Users/wmitchell/Documents/project_rm_at_scale/SeeRM
PYTHONPATH="apps/SayRM/src" uvicorn sayrm_service.app:app --reload
# or: PYTHONPATH="apps/SayRM/src" python -m sayrm_service.main
```

> Tip: export `PYTHONPATH="apps/SayRM/src"` in your shell profile so the package
> is always importable. The package automatically adds
> `apps/messaging_consumer/src` to the path so it can reuse the Notion helpers.

Environment variables are pulled from `.env.local`, `apps/SayRM/.env.local`,
then `.env`. At minimum you need:

- `NOTION_API_KEY`
- `NOTION_COMPANIES_DB_ID`
- `OPENAI_API_KEY`

Optional overrides:

- `SAYRM_DB_PATH` – defaults to `apps/SayRM/.sayrm.db`
- `SAYRM_LLM_MODEL` – defaults to `gpt-5-mini`
- `SAYRM_INTERNAL_API` – when you’re ready to point at the internal usage API

## Key endpoints

| Endpoint | Description |
| --- | --- |
| `POST /companies/{callsign}/briefs/external` | Pulls Notion data + summaries |
| `POST /companies/{callsign}/briefs/internal` | Placeholder internal usage summary |
| `GET /templates` | Lists local templates |
| `POST /drafts/compose` | Builds a draft and logs it |
| `POST /drafts/labels` | Adds manual feedback for future tuning |

All POST endpoints automatically write to the SQLite log, so you can later
export the table for fine-tuning data.

## Raycast extension

The Raycast extension lives in `apps/SayRM/raycast/sayrm-extension`.

1. Open Raycast → Extensions → `+` → *Import Extension*
2. Point Raycast at the `sayrm-extension` directory.
3. Set the `SayRM Service URL` preference if you run the service on a
   different port.

Commands included:

- `Client Brief` – pulls the external summary and shows sections you can copy.
- `Usage Snapshot` – fetches the internal API placeholder.
- `Template Picker` – quick access to templates/snippets.
- `Draft Helper` – sends context/template data to the compose endpoint.
- `Label Draft` – attaches dropdown labels to the latest drafts.
