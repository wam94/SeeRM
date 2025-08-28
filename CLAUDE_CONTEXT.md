# Claude Context - SeeRM Project

## Project Overview
SeeRM is a client relationship management system that ingests weekly product usage data, enriches it with external intelligence, and produces executive summaries for proactive client engagement.

**Core Purpose:** Transform internal usage metrics into actionable relationship insights for banking technology company serving startups.

## Current Architecture (As of 2025-08-28)

### Data Flow
```
Metabase Weekly CSV → Gmail → Python Processors → Notion Storage → Email Digests
```

### Components
- **main.py**: Weekly digest processor (CSV → rendered HTML email)
- **dossier_baseline.py**: Company profile generator with LLM narratives
- **news_job.py**: Intelligence collection (RSS, Google CSE, news aggregation)
- **enrich_funding.py**: Funding data extraction (Crunchbase API, web scraping)
- **notion_client.py**: Notion API wrapper for company/intel databases
- **gmail_client.py**: Gmail API for CSV ingestion and email sending
- **parser.py**: CSV data normalization and metrics calculation

### Data Sources
1. **Internal**: Metabase CSV (weekly usage metrics, client changes)
2. **External News**: RSS feeds, Google Custom Search Engine
3. **Funding**: Crunchbase API, web scraping with heuristics
4. **LLM**: OpenAI for narrative generation and summarization

### Storage
- **Notion Companies DB**: Client profiles, contact info, domains, dossier flags
- **Notion Intel DB**: Timestamped intelligence items, summaries

### Workflows (GitHub Actions)
- Weekly digest automation
- News collection jobs
- Baseline dossier generation
- Domain/funding discovery probes

## Key Business Objectives
1. **Internal Usage Understanding**: Track product adoption, churn signals, balance changes
2. **External Intelligence**: News, funding, market developments per client
3. **Proactive Engagement**: LLM-generated emails based on usage + intelligence context
4. **Aggregated Insights**: Portfolio-level view with drill-down capability
5. **Support Integration**: ZenDesk ticket analysis (planned)

## Architecture Recommendations (From Analysis)

### Immediate Improvements Needed
1. **Database Layer**: PostgreSQL/SQLite for structured data + complex queries
2. **ZenDesk Integration**: Support ticket ingestion and sentiment analysis  
3. **Web Dashboard**: Executive portfolio view + detailed company drilling
4. **Context Engine**: Multi-source data assembly for LLM email generation
5. **API Layer**: RESTful endpoints for frontend and external integrations

### Proposed Structure
```
/api/companies/{id}/
  ├── profile/        # Usage stats, basic info
  ├── intelligence/   # News, funding, background  
  ├── interactions/   # Support tickets, emails
  └── timeline/       # Chronological view

/dashboard/
  ├── portfolio/      # Executive summary, health scores
  ├── alerts/         # Proactive engagement opportunities
  └── companies/{id}/ # Detailed company view
```

### Development Phases
**Phase 1** (Weeks 1-2): ZenDesk API + structured storage
**Phase 2** (Weeks 3-4): Web dashboard + context engine  
**Phase 3** (Weeks 5-6): Real-time alerts + approval workflows

## Current Limitations
- **No aggregation**: Notion-only storage limits portfolio analysis
- **No support data**: Missing ZenDesk integration for complete client picture
- **Manual email**: No systematic LLM email generation framework
- **No real-time alerts**: Reactive rather than proactive engagement

## Technical Notes
- Uses environment variables for all secrets (proper security)
- GitHub Actions for automation (Monday 9am ET weekly runs)
- Batching support for large client rosters
- Rate limiting and error handling throughout
- Modular design allows incremental enhancement

## Next Session Focus Areas
When resuming work, prioritize:
1. **ZenDesk Integration**: Support ticket API and data modeling
2. **Database Schema**: Design for aggregation and timeline views
3. **Context Engine**: Multi-source data assembly for email generation
4. **Web Interface**: Dashboard for portfolio insights and drilling

---
*Last Updated: 2025-08-28*
*Claude Reference: Use this context to maintain continuity across sessions*