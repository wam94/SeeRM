# Shared Environment Variables

Both SeeRM and the messaging consumer should rely on the same variable names so
the secrets manager can inject them without custom glue. Only copy the keys that
the messaging project truly needs.

| Variable | Source | Used By | Notes |
| --- | --- | --- | --- |
| `NOTION_API_KEY` | 1Password / Vault | Both | Required to read company, intel, and report databases. |
| `NOTION_INTEL_DB_ID` | Notion | SeeRM | Needed if the consumer queries raw intel pages directly. |
| `NOTION_REPORTS_DB_ID` | Notion | Both | Lets the consumer find the Weekly News Digest pages via Notion API. |
| `CSV_SOURCE_PATH` | S3/GCS/local | SeeRM | Listed so the consumer knows where SeeRM ingests data from (read-only). |
| `OPENAI_API_KEY` | OpenAI | Both (optional) | Messaging repo can reuse it for personalization if desired. |
| `OPENAI_CHAT_MODEL` | Config | Both (optional) | Defaults to `gpt-5-mini` per SeeRM. |
| `DIGEST_TO`, `DIGEST_CC`, `DIGEST_BCC` | Secrets manager | Messaging | Use identical keys to see SeeRM recipients. |
| `RELATIONSHIP_MANAGER_NAME` | Config | Both | Ensures tone/voice stays aligned. |
| `ENVIRONMENT` | Config | Both | Keep `production/staging/dev` consistent for telemetry. |
| `SEERM_EXPORT_BUCKET` | Storage | Messaging | New variable defining where SeeRM drops JSON artifacts. |
| `SEERM_EXPORT_PREFIX` | Storage | Messaging | Path within the bucket for digest exports. |
| `MESSAGING_STATE_STORE` | Storage/DB | Messaging | Location to persist offsets or processed IDs. |

Tips:
- Store actual values in your secret manager; keep `.env` files for local dev only.
- Version-control a `env.example` (in the new repo) mirroring this table so new
  contributors know what to configure.
