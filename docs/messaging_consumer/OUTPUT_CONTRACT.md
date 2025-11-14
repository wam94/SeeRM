# SeeRM → Messaging Output Contract

The messaging automation consumes the Weekly News Digest that SeeRM already
writes to Notion and email. This contract normalizes those artifacts into a
JSON payload plus a minimal metadata index so the new repo can reason about the
content without depending on SeeRM internals.

## Artifacts

| Artifact | Producer | Consumer usage |
| --- | --- | --- |
| Notion report page (`reports_db_id`) | SeeRM | Source of truth for UI review. The messaging repo reads the page to fetch structured blocks + metadata. |
| Email digest (HTML + plaintext) | SeeRM | Optional fallback when the Notion API is unavailable; parsing rules mirror the JSON payload. |
| JSON export (`s3://{SEERM_EXPORT_BUCKET}/{SEERM_EXPORT_PREFIX}/{report_id}.json`) | SeeRM | Primary artifact. Mirrors the `Report` dataclass plus computed fields. A sample lives in `examples/weekly_news_digest.json`. |

## JSON structure
The JSON payload is deliberately simple and documented via
`schema/weekly_news_digest.schema.json`. High-level layout:

```jsonc
{
  "report_id": "weekly_news_2024-04-12_142233",
  "week_of": "2024-04-12",
  "generated_at": "2024-04-19T14:22:33Z",
  "summary_stats": {
    "total_items": 42,
    "unique_companies": 14,
    "categories_active": 5,
    "notable_items": 6
  },
  "company_categories": [
    {
      "company": "Mercury",
      "categories": ["product", "partnership"]
    }
  ],
  "most_active_companies": [
    ["Mercury", 6],
    ["Rippling", 5]
  ],
  "key_themes": ["AI copilots", "Treasury expansion"],
  "summary": "Top shifts in the portfolio...",
  "by_type": {
    "fundraising": 7,
    "hiring": 8
  },
  "notable_items": [
    {
      "title": "Mercury launches FX hedging",
      "source": "TechCrunch",
      "url": "https://...",
      "companies": ["Mercury"],
      "type": "product",
      "relevance_score": 0.94
    }
  ],
  "rendered": {
    "markdown": "...",
    "html": "..."
  }
}
```

### Field mapping

| JSON field | Source | Notes |
| --- | --- | --- |
| `report_id` | SeeRM `ReportMetadata.report_id` | Must be unique; used as dedupe key. |
| `week_of` | `WeeklyNewsDigest.week_of` | ISO `YYYY-MM-DD`. |
| `generated_at` | `ReportMetadata.generated_at` | ISO timestamp. |
| `summary_stats` | `_create_report_content` | All integers. |
| `company_categories` | `_build_company_category_matrix` | Flattened for messaging heuristics. |
| `most_active_companies` | Derived | Array of `[company, count]`. |
| `key_themes`/`summary` | `WeeklyNewsDigest` | Provide campaign context. |
| `by_type` | `digest.by_type` | Keys align with `NewsType.value`. |
| `notable_items` | `digest.notable_items` | Each item keeps the SeeRM `relevance_score`. |
| `rendered` | `Report.markdown/html` | Mirrors current email content for parity. |

## Notion mapping

| Notion property | Description | JSON twin |
| --- | --- | --- |
| `Name` | `Weekly News Digest - Week of {week_of}` | `title` |
| `Week Of (date)` | Calendar week | `week_of` |
| `Report ID (rich text)` | Primary key aligning across artifacts | `report_id` |
| `Summary Stats (number props)` | Totals stored individually | `summary_stats.*` |
| `Themes (multi-select)` | SeeRM `key_themes` | `key_themes` |
| `Attachment (files)` | Link to JSON export or email PDF | `rendered.*`/S3 object |

Consumer repo should:
1. Pull the Notion page via `NOTION_REPORTS_DB_ID`.
2. Check the `Report ID` against its local store (`MESSAGING_STATE_STORE`).
3. Fetch the JSON asset (if not embedded in Notion).
4. Validate with the JSON Schema.
5. Generate outbound or reply copy.

## Email mapping

Email subject currently: `Weekly News Digest - Week of {week_of}`.

Use the HTML version for feature-rich channels and the markdown/plaintext for SMS
or lightweight replies. The rendered body aligns 1:1 with `rendered.html` and
`rendered.markdown`.
