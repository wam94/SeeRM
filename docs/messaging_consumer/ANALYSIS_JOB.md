# Analysis Job Reference

The messaging consumer relies on the existing SeeRM **Analysis Job** to emit
fresh Weekly News Digest artifacts. This document maps that workflow so new
engineers on the messaging project understand where signals originate and how
to hook into them.

## Overview
- Workflow file: `.github/workflows/analysis_job.yml`
- Triggered manually (`workflow_dispatch`) or automatically whenever the
  "External Intel (News)" workflow completes.
- Runs on `ubuntu-latest`, installs Python 3.11, executes
  `python -m app.main news` (the same entry point used locally), then uploads
  rendered HTML fallbacks as GitHub artifacts.

### Key inputs
| Input | Purpose | Notes |
| --- | --- | --- |
| `callsigns` | Optional CSV filter | lets you scope the run to specific companies. |
| `dry_run` | Skip outbound email | use `true` when testing. |
| `lookback_days` | News window | defaults to `10`. |
| `max_per_org` | Cap per company | defaults to `5`. |

### Secrets + Vars
Pulled from `secrets`/`vars` in the SeeRM repo (Notion, Gmail, OpenAI, Google
CSE). These are the same names listed in `ENVIRONMENT.md` so the consumer repo
can reuse them without translation when it needs API access.

### Outputs
1. Weekly News Digest reports written to Notion (`NOTION_REPORTS_DB_ID`).
2. Email digests (HTML + plaintext) via the Gmail delivery pipeline.
3. Optional HTML fallbacks stored as GitHub workflow artifacts (`reports/email_fallbacks/*.html`).
4. **New for this branch**: JSON payloads exported to the shared bucket
   (`s3://{SEERM_EXPORT_BUCKET}/{SEERM_EXPORT_PREFIX}/{report_id}.json`) which
   conform to `docs/messaging_consumer/schema/weekly_news_digest.schema.json`.

The messaging consumer polls (or is notified about) item 4 and treats the
Notion + email outputs as secondary verification channels.

## Signals consumed by messaging project
| Signal | Description | Consumer usage |
| --- | --- | --- |
| Report metadata (week, report_id, themes) | Already in Notion/JSON | Drives personalization segments. |
| Company/category matrix | Derived in `_build_company_category_matrix` | Used for audience routing and referencing. |
| Notable items + relevance scores | Emitted in JSON `notable_items` | Prioritizes talking points. |
| Rendering (markdown/html) | `rendered` object | Provides default copy blocks or fallback text. |

Any future analysis signals (risk flags, AI insights, etc.) should be added to
the schema + sample and annotated here before the messaging repo depends on
them.

## Change detection between repos
To ensure the messaging repo can see upstream format changes:
1. **Schema as contract** – Update
   `docs/messaging_consumer/schema/weekly_news_digest.schema.json` whenever
   Notion/email structure changes. Commit the update in SeeRM and bump the
   schema copy in the messaging repo (`apps/messaging_consumer/schema/` or the
   exported template).
2. **Artifact validation** – The template ships with
   `messaging_consumer.contracts.load_weekly_digest` which validates every JSON
   payload. Any schema-breaking change in SeeRM causes validation/test failures
   in the messaging repo.
3. **Notion property watch** – The consumer should compare the Notion page
   properties it fetches (e.g., `Report ID`, `Week Of`, `Summary Stats`) against
   the expected set. Missing/renamed properties can be surfaced via observability
   alerts to force a schema update.
4. **GitHub artifact smoke tests** – Optional: have the messaging repo consume
   the latest uploaded artifact from the Analysis Job CI run (accessible via the
   GitHub API) to detect format drift even before the S3 export is updated.

Because both repos carry the same schema + fixtures, any intentional format
change is done by editing the schema/sample in SeeRM, copying it into the
messaging repo, and updating its contract tests.
