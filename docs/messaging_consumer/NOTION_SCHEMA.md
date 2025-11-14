# Notion Schema Contracts

Messaging automation queries two existing Notion databases maintained by SeeRM:

1. **Intel / News DB** (`NOTION_INTEL_DB_ID`)
2. **Company Dossiers DB** (`NOTION_COMPANIES_DB_ID`)

Both are keyed through the **Callsign** relation so every downstream consumer can
look up entries by a unique company slug.

## Intel / News DB

| Property        | Type          | Purpose |
| --------------- | ------------- | ------- |
| `Title`         | Title         | Raw headline or generated title. |
| `URL`           | URL           | Primary article/source link. |
| `First Seen`    | Date          | When the item was first ingested. |
| `Last Seen`     | Date          | Most recent validation timestamp. |
| `Callsign`      | Relation      | Links to the company page in the Companies DB. |
| `Categories`    | Multi-select  | Mirrors `NewsType` (fundraising, product, etc.). |
| `Summary`       | Rich text     | AI-generated or editor-curated summary. |
| `Relevance`     | Number        | Float 0–1 used for ranking. |
| `Source`        | Select/Text   | Publisher or RSS feed name. |
| `Week Of`       | Date          | Aggregation helper for weekly digests. |

### Query expectations
- Filter by `Callsign` relation.
- Sort descending by `First Seen` or `Last Seen`.
- Optional filters: `Week Of` for digest alignment, `Categories` for targeted
  campaigns, `Relevance` threshold.

## Companies / Dossiers DB

| Property            | Type          | Purpose |
| ------------------- | ------------- | ------- |
| `Name`              | Title         | Company display name. |
| `Callsign`          | Rich text     | Unique slug (lowercase). |
| `Status`            | Select        | Active / Watch / Archived. |
| `Segments`          | Multi-select  | Industry vertical tags. |
| `Owner`             | People        | RM assignment. |
| `Dossier Ready`     | Checkbox      | Flag when a full dossier exists. |
| `Last Intel Update` | Date          | Most recent SeeRM refresh. |
| `Intel Summary`     | Rich text     | Rolling synopsis of latest intel. |
| `News Items`        | Relation      | Backlink to Intel DB entries. |

### Query expectations
- Filter by `Callsign` equals `{slug}`.
- Expand relations (e.g., `News Items`) as needed.
- Use the `Dossier Ready` checkbox + `Last Intel Update` date to decide whether
  a company should be included in messaging campaigns.

## Shared behavior
- All callsign comparisons are case-insensitive but stored lowercase.
- When calling the Notion API, always include the `Notion-Version` header
  (`2022-06-28`) and rehydrate relations if you need cross-database data.
- Secrets required:
  - `NOTION_API_KEY`
  - `NOTION_INTEL_DB_ID`
  - `NOTION_COMPANIES_DB_ID`

The messaging consumer code reads these values via environment variables; see
`docs/messaging_consumer/ENVIRONMENT.md` for the canonical names.
