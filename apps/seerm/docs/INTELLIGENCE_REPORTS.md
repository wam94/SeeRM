# SeeRM Intelligence Reports

Automated intelligence report generation combining CSV movement data, Notion company profiles, and news analysis.

## Overview

The Intelligence Reports system provides three types of automated reports:

1. **Company Deep Dive** - Comprehensive analysis for individual companies
2. **New Client Summaries** - Weekly summaries of new client accounts  
3. **Weekly News Digest** - Categorized news summaries across portfolio

## Quick Start

### Minimal Setup (CSV Only)
```bash
# Set required configuration
export CSV_SOURCE_PATH="/path/to/your/data.csv"

# Check system health
python -m app.main reports health-check

# Generate a company deep dive
python -m app.main reports company-deepdive ACME
```

### Full Setup (All Features)
```bash
# Required
export CSV_SOURCE_PATH="/path/to/your/data.csv"

# Optional - Email delivery
export GMAIL_CLIENT_ID="your-client-id"
export GMAIL_CLIENT_SECRET="your-client-secret"  
export GMAIL_REFRESH_TOKEN="your-refresh-token"
export GMAIL_USER="your-email@domain.com"

# Optional - Notion integration
export NOTION_API_KEY="your-notion-api-key"
export NOTION_COMPANIES_DB_ID="database-id-for-companies"
export NOTION_REPORTS_DB_ID="database-id-for-reports"

# Optional - AI enhancements  
export OPENAI_API_KEY="your-openai-key"
export GOOGLE_API_KEY="your-google-key"
export GOOGLE_CSE_ID="your-custom-search-engine-id"
```

## Configuration Options

### Intelligence Reports Settings
```bash
# Feature toggle
export INTELLIGENCE_REPORTS_ENABLED=true  # default: true

# Report parameters  
export INTELLIGENCE_DEFAULT_REPORT_DAYS=7  # default: 7
export INTELLIGENCE_MAX_NEWS_PER_COMPANY=10  # default: 10
export INTELLIGENCE_RISK_ASSESSMENT_ENABLED=true  # default: true
```

### Database Configuration
```bash
# Reports database (optional - for storing generated reports)
export NOTION_REPORTS_DB_ID="your-reports-database-id"

# Companies database (optional - for enhanced company data)  
export NOTION_COMPANIES_DB_ID="your-companies-database-id"

# Intelligence database (optional - for news and intel data)
export NOTION_INTEL_DB_ID="your-intelligence-database-id"
```
Required properties for the intelligence database:

- `Title` (title)
- `URL` (url)
- `First Seen` (date)
- `Last Seen` (date)
- `Callsign` (relation to the Companies database)
- `Source` (select, multi-select, or rich text)
- `Published At` (date)
- `Summary` (rich text, optional)

## CLI Commands

### Health Check
Check system status and configuration:
```bash
python -m app.main reports health-check
```

### Company Deep Dive
Generate comprehensive company analysis:
```bash
# Basic usage
python -m app.main reports company-deepdive ACME

# Skip email delivery
python -m app.main reports company-deepdive ACME --no-email

# Custom config file
python -m app.main reports company-deepdive ACME --config-file custom.env
```

### New Client Summaries  
Generate weekly new client reports:
```bash
# Auto-detect new clients from movements
python -m app.main reports new-clients

# Specify client callsigns
python -m app.main reports new-clients --callsigns "ACME,BETA,GAMMA"

# Skip email delivery
python -m app.main reports new-clients --no-email
```

### Weekly News Digest
Generate categorized news summaries:
```bash  
# Default 7-day lookback
python -m app.main reports weekly-news

# Custom lookback period
python -m app.main reports weekly-news --days 14

# Skip email delivery  
python -m app.main reports weekly-news --no-email
```

## Report Types

### Company Deep Dive Report
**Purpose**: Comprehensive analysis of individual companies  
**Data Sources**: CSV movements, Notion profiles, news history, risk assessment  
**Output**: HTML/Markdown report with metrics, news timeline, recommendations

**Contains**:
- Executive summary
- Current metrics and movement analysis
- Risk assessment with factors
- 90-day news timeline  
- Product usage analysis
- Actionable recommendations
- Similar company comparisons

### New Client Summary Report
**Purpose**: Weekly onboarding intelligence for new accounts  
**Data Sources**: CSV movements, Notion profiles, similar client analysis  
**Output**: Weekly summary with onboarding checklists

**Contains**:
- New client identification
- Initial balance and products
- Risk level assessment
- Onboarding checklist generation
- Similar client recommendations
- High-value account flagging

### Weekly News Digest Report  
**Purpose**: Portfolio-wide news intelligence  
**Data Sources**: Notion intelligence, news categorization, sentiment analysis  
**Output**: Bulletized news summary by category

**Contains**:
- Categorized news by type (funding, acquisitions, partnerships, etc.)
- Notable items highlighting
- Key themes extraction
- Company activity rankings
- Priority items (funding, M&A)
- Quick-scan format for busy executives

## System Architecture

### Data Flow
```
CSV Data → Intelligence Aggregator ← Notion Data
    ↓                                    ↓
Report Generators ←------ News Analysis  
    ↓
HTML/Markdown Reports
    ↓
Email + Notion Storage
```

### Core Components

**IntelligenceAggregator**: Unified data access layer combining CSV, Notion, and news sources

**Report Generators**:
- `CompanyDeepDiveReport` - Individual company analysis
- `NewClientReport` - New client summaries  
- `WeeklyNewsReport` - News digest generation

**Analyzers**:
- `CompanyAnalyzer` - Business metrics and risk assessment
- `NewsAnalyzer` - Content categorization and sentiment

**Storage & Delivery**:
- Email delivery via Enhanced Gmail client
- Notion page creation with metadata
- Local HTML/Markdown file generation

## Notion Integration

### Reports Database Schema
Create a Notion database with these properties:

- **Name** (Title) - Report title
- **Report Type** (Select) - company_deepdive, new_clients, weekly_news  
- **Generated** (Date) - Creation timestamp
- **Status** (Select) - Generated, Sent, Archived
- **Callsign** (Rich Text) - For company-specific reports
- **Company Name** (Rich Text) - Company name
- **Risk Level** (Select) - Low, Medium, High, Critical
- **Duration** (Rich Text) - Generation time
- **News Items** (Number) - Count of news items
- **Client Count** (Number) - For new client reports

### Automatic Page Creation
When `NOTION_REPORTS_DB_ID` is configured:

- Reports automatically create Notion pages
- Markdown content converted to Notion blocks
- Metadata stored as page properties  
- Rich formatting with headers, lists, and links

## Error Handling & Reliability  

### Graceful Degradation
The system works with minimal configuration and gracefully handles missing services:

- **CSV Only**: Basic reports without email/Notion
- **No Gmail**: Reports generated but not emailed
- **No Notion**: Reports generated but not stored  
- **No AI Services**: Fallback to heuristic analysis

### Circuit Breakers
- External service failures don't break report generation
- Automatic retry with exponential backoff
- Performance tracking and monitoring

### Health Monitoring
```bash
# Check all service health
python -m app.main reports health-check

# View overall system health  
python -m app.main health

# Check configuration
python -m app.main config
```

## Development & Testing

### Adding New Report Types

1. **Create Model** in `app/intelligence/models.py`
2. **Create Generator** in `app/reports/new_report_type.py`  
3. **Add CLI Command** in `app/cli_commands/reports.py`
4. **Update Aggregator** if new data sources needed

### Testing Reports  
```bash
# Dry run mode
python -m app.main --dry-run reports company-deepdive TEST

# Debug logging
python -m app.main --debug reports health-check

# Custom config for testing
python -m app.main reports health-check --config-file test.env
```

### Performance Monitoring
All report generation includes automatic performance tracking:
- Generation duration 
- Data source access times
- Success/failure rates
- Circuit breaker status

## Troubleshooting

### Common Issues

**"Missing CSV source path"**
- Set `CSV_SOURCE_PATH` environment variable
- Ensure CSV file exists and is readable

**"Gmail client initialization failed"**  
- Check Gmail OAuth credentials
- Verify refresh token is not expired
- Use `--no-email` flag to skip email delivery

**"Notion API errors"**
- Verify `NOTION_API_KEY` is valid
- Check database IDs are correct
- Ensure integration has access to databases

**"No news items found"**
- Check Notion intelligence database has data
- Verify lookback period includes relevant dates
- Review news categorization filters

### Debug Commands
```bash  
# Verbose health check
python -m app.main --debug reports health-check

# Test specific configuration
python -m app.main config

# Check circuit breaker status
python -m app.main health
```

## Integration with Existing Workflows

### Scheduling
Add to cron or task scheduler:
```bash
# Daily company deep dives for top movers  
0 9 * * * cd /path/to/seerm && python -m app.main reports company-deepdive ACME

# Weekly new client summaries (Mondays)
0 10 * * 1 cd /path/to/seerm && python -m app.main reports new-clients

# Weekly news digest (Fridays)  
0 16 * * 5 cd /path/to/seerm && python -m app.main reports weekly-news
```

### API Integration
The report generators can be imported and used programmatically:
```python
from app.intelligence.data_aggregator import IntelligenceAggregator
from app.reports.company_deepdive import CompanyDeepDiveReport

# Create aggregator
aggregator = IntelligenceAggregator(...)

# Generate report  
generator = CompanyDeepDiveReport(aggregator)
report = generator.generate("ACME", include_email=False)
```

## Security Considerations

- **No customer data storage**: Reports use ephemeral processing
- **API key protection**: Use environment variables, not config files
- **Access control**: Notion integration permissions
- **Email security**: OAuth2 with refresh tokens
- **Rate limiting**: Built-in throttling for external APIs

---

For additional support, see the main [SeeRM documentation](README.md) or submit issues to the project repository.
