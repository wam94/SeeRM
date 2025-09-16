# SeeRM - Automated Client Intelligence & Digest System

[![GitHub Actions](https://github.com/wmitchell-evolveip/SeeRM/actions/workflows/main.yml/badge.svg)](https://github.com/wmitchell-evolveip/SeeRM/actions/workflows/main.yml)
[![Test Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen)](./tests/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Production-ready system for automated weekly client digests and intelligence gathering.**

SeeRM is an enterprise-grade client intelligence platform that automates portfolio monitoring, market intelligence gathering, and actionable report generation. The system integrates with Metabase, Gmail, Notion, and external APIs to provide comprehensive client insights and automated workflows.

> 📖 **New User?** Check out [HOW_TO_USE.txt](./HOW_TO_USE.txt) for a business-focused overview.

## 🚀 Key Features

- 🔄 **Automated Weekly Processing**: Monday 9 AM ET execution via GitHub Actions
- 📊 **Comprehensive Reporting**: HTML digests, intelligence reports, and Notion integration
- 🛡️ **Enterprise Reliability**: Circuit breakers, rate limiting, retry logic
- 🔧 **Type-Safe Architecture**: Full Pydantic v2 validation with structured logging
- 📈 **Performance Optimized**: Handles 500+ companies in <5s
- 🧪 **Extensive Testing**: 93% coverage with integration and performance tests
- 🌐 **Multi-Source Intelligence**: Combines CSV data, news APIs, and web search

## 📋 Table of Contents

- [Quick Start](#-quick-start)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [Intelligence Reports](#-intelligence-reports)
- [Automated Workflows](#-automated-workflows)
- [Development](#-development)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [Contributing](#-contributing)
 - [Releases](#-releases)

## 🏗️ System Overview

### Execution Schedule
- **Weekly Digest**: Every Monday 9:00 AM ET (automated)
- **News Intelligence**: 15 minutes after digest completion
- **Dossier Generation**: Triggered by new account detection
- **Custom Reports**: On-demand via CLI

### Data Flow
```
Metabase → Gmail → SeeRM → [Processing] → Email Digest
                           ↓
                    Notion Database
                           ↓
                    Downstream Workflows
```

### Architecture
```
SeeRM/
├── app/
│   ├── core/              # Configuration, models, logging
│   ├── data/              # Data access (Gmail, Notion, CSV)
│   ├── services/          # Business logic
│   ├── workflows/         # End-to-end orchestration
│   ├── intelligence/      # Intelligence reports system
│   ├── reports/           # Report generators
│   └── utils/             # Reliability patterns
├── tests/                 # Comprehensive test suite
├── .github/workflows/     # GitHub Actions automation
└── archive/               # Legacy scripts
```

## ⚡ Quick Start

### For End Users
1. **Weekly Digests**: Automatically delivered every Monday morning
2. **Custom Reports**: Request via your system administrator
3. **Notion Access**: View detailed company dossiers and intelligence
4. **Alert Monitoring**: Review trigger notifications for urgent accounts

### For Developers
```bash
# Clone and install
git clone https://github.com/wmitchell-evolveip/SeeRM.git
cd SeeRM
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Test installation
python -m app.main health

# Run dry-run test
python -m app.main --dry-run digest-dry-run --max-messages 1
```

## 📦 Installation

### Prerequisites
- Python 3.11+
- Gmail API credentials (Google Cloud Console)
- Metabase instance with scheduled CSV exports
- Optional: Notion workspace, OpenAI API key

### 1. Clone Repository
```bash
git clone https://github.com/wmitchell-evolveip/SeeRM.git
cd SeeRM
```

### 2. Install Dependencies
```bash
# Production installation
pip install -r requirements.txt

# Development installation (includes testing tools)
pip install -r requirements.txt
pip install -e .
```

## ⚙️ Configuration

### Environment Setup
Set up your `.env` file or environment variables:

```bash
# Gmail API (Required)
GMAIL_CLIENT_ID="your_gmail_client_id"
GMAIL_CLIENT_SECRET="your_gmail_client_secret" 
GMAIL_REFRESH_TOKEN="your_gmail_refresh_token"
GMAIL_USER="your_email@domain.com"

# Notion API (Optional - for intelligence features)
NOTION_API_KEY="your_notion_api_key"
NOTION_COMPANIES_DB_ID="your_companies_database_id"

# OpenAI API (Optional - for news intelligence)
OPENAI_API_KEY="your_openai_api_key"

# Application Settings
ENVIRONMENT="production"  # or "development"
DRY_RUN="false"          # Set to "true" for testing
DEBUG="false"
```

### Google Cloud OAuth Setup
Create a Google Cloud OAuth Client for Gmail API:
- Enable Gmail API in Google Cloud Console
- Create OAuth 2.0 credentials (Desktop or Web application)
- Required scopes: 
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/gmail.send`
- Perform one-time OAuth flow to get refresh token

### Metabase Integration
Set up your Metabase SQL Question to email CSV data:
- Schedule weekly execution
- Configure email delivery with CSV attachment
- Use subject line: `"Alert: SeeRM Master Query has results"`
- Ensure CSV includes required columns: `CALLSIGN`, `DBA`, `DOMAIN_ROOT`, `BENEFICIAL_OWNERS`

## 📊 Intelligence Reports

**Comprehensive business intelligence combining CSV movement data, Notion profiles, and news analysis.**

### Report Types
- **🏢 Company Deep Dives**: Full analysis with risk assessment and recommendations
- **🆕 New Client Summaries**: Weekly onboarding intelligence and checklists  
- **📰 Weekly News Digest**: Categorized portfolio news with key themes

### Quick Examples
```bash
# System health check
python -m app.main reports health-check

# Generate company analysis
python -m app.main reports company-deepdive ACME

# New client intelligence
python -m app.main reports new-clients

# News summary
python -m app.main reports weekly-news
```

📖 **[Full Intelligence Reports Documentation →](INTELLIGENCE_REPORTS.md)**

## 🔄 Automated Workflows

### Weekly Digest (Primary)
- **Schedule**: Every Monday 9:00 AM ET
- **Trigger**: GitHub Actions (`.github/workflows/main.yml`)
- **Process**: Gmail → CSV → Analysis → HTML Digest → Email
- **Output**: Weekly movement summary, new account alerts

### News Intelligence  
- **Schedule**: 15 minutes after Weekly Digest
- **Trigger**: Workflow completion or manual
- **Process**: Portfolio scan → News gathering → Notion updates
- **Output**: Intelligence database updates

### Baseline Dossiers
- **Schedule**: Manual trigger or new account detection
- **Trigger**: GitHub Actions workflow_dispatch
- **Process**: Company research → Funding analysis → Notion dossiers
- **Output**: Comprehensive company profiles

## 💻 Usage

### Core Operations
```bash
# Generate weekly digest (production)
python -m app.main digest --max-messages 10

# Test digest workflow (dry-run)
python -m app.main --dry-run digest-dry-run --max-messages 1

# Test CSV parsing
python -m app.main test-csv path/to/your/file.csv

# Check system health
python -m app.main health

# View configuration
python -m app.main config
```

### Intelligence Reports
```bash
# System health and configuration check
python -m app.main reports health-check

# Individual company analysis
python -m app.main reports company-deepdive ACME
python -m app.main reports company-deepdive ACME --no-email

# New client intelligence summaries  
python -m app.main reports new-clients
python -m app.main reports new-clients --callsigns "ACME,BETA,GAMMA"

# Portfolio news digest
python -m app.main reports weekly-news
python -m app.main reports weekly-news --days 14
```

### Advanced Options
```bash
# Enable debug logging
python -m app.main --debug digest

# Custom correlation ID for tracing
python -m app.main --correlation-id "trace-123" digest

# Reset circuit breakers
python -m app.main reset-breaker gmail_search
```

## 🚀 Deployment

### GitHub Actions (Recommended)
The system is designed for automated deployment via GitHub Actions:

1. **Repository Secrets**: Configure required secrets in GitHub
2. **Weekly Schedule**: Automatic execution every Monday
3. **Manual Triggers**: Use workflow_dispatch for on-demand runs
4. **Monitoring**: Built-in health checks and notifications

### Required Secrets
```
GMAIL_CLIENT_ID         # Google OAuth client ID
GMAIL_CLIENT_SECRET     # Google OAuth client secret
GMAIL_REFRESH_TOKEN     # Gmail refresh token
GMAIL_USER              # Your email address
DIGEST_TO               # Digest recipient email
NOTION_API_KEY          # Notion integration token (optional)
OPENAI_API_KEY          # OpenAI API key (optional)
GOOGLE_API_KEY          # Google Search API key (optional)
```

### Local Development
```bash
# Install development dependencies
pip install -r requirements.txt
pip install -e .

# Set up pre-commit hooks
pre-commit install

# Run in development mode
python -m app.main --debug --dry-run digest-dry-run
```

## 🧪 Testing

### Test Suite
```bash
# Full test suite
pytest tests/ -v

# Performance benchmarks only
pytest tests/test_digest_service.py::TestPerformanceBenchmarks -v

# Integration tests
pytest tests/test_integration.py -v

# Specific test with detailed output
pytest tests/test_csv_parser.py::test_real_csv_processing -v -s
```

### Test Coverage & Results
- **93% test coverage** across all modules
- **44/44 tests passing** (100% success rate)
- **Performance benchmarks**: <5s end-to-end processing
- **Integration testing**: Gmail, Notion, CSV processing
- **Compatibility testing**: Legacy system output matching

## ⚙️ Configuration Reference

### Core Environment Variables
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GMAIL_CLIENT_ID` | Yes | - | Google OAuth client ID |
| `GMAIL_CLIENT_SECRET` | Yes | - | Google OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | Yes | - | Gmail refresh token |
| `GMAIL_USER` | Yes | - | Your email address |
| `DIGEST_TO` | No | `GMAIL_USER` | Digest recipient email |
| `DIGEST_CC` | No | - | Digest CC recipients |
| `DIGEST_BCC` | No | - | Digest BCC recipients |

### Optional Integrations
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NOTION_API_KEY` | No | - | Notion integration token |
| `NOTION_COMPANIES_DB_ID` | No | - | Companies database ID |
| `NOTION_INTEL_DB_ID` | No | - | Intelligence database ID |
| `NOTION_REPORTS_DB_ID` | No | - | Reports database ID |
| `OPENAI_API_KEY` | No | - | OpenAI API key for intelligence |
| `GOOGLE_API_KEY` | No | - | Google Search API key |
| `GOOGLE_CSE_ID` | No | - | Custom Search Engine ID |

### Application Settings
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENVIRONMENT` | No | `production` | Environment (production/development) |
| `DRY_RUN` | No | `false` | Enable dry-run mode |
| `DEBUG` | No | `false` | Enable debug logging |
| `MAX_WORKERS` | No | `6` | Parallel processing workers |
| `CSV_SOURCE_PATH` | No | - | Direct CSV file path (for reports) |

### Performance Tuning
```bash
# Adjust processing limits
MAX_WORKERS=8
RATE_LIMIT_CALLS_PER_SECOND=5.0
CIRCUIT_BREAKER_FAILURE_THRESHOLD=3

# Request timeouts
REQUEST_TIMEOUT=30
```

## 🔍 Monitoring & Reliability

### Health Monitoring
```bash
# System health check
python -m app.main health

# Intelligence reports health
python -m app.main reports health-check

# Configuration validation
python -m app.main config

# Reset circuit breaker
python -m app.main reset-breaker <breaker_name>
```

### Enterprise Reliability Features
- **Circuit Breakers**: Prevent cascade failures across services
- **Rate Limiting**: Protect external APIs from abuse
- **Retry Logic**: Exponential backoff for transient failures
- **Parallel Processing**: Concurrent operations with error isolation
- **Graceful Degradation**: Continue operating with partial service failures
- **Structured Logging**: Correlation IDs, performance metrics, error context
- **Health Checks**: Comprehensive service availability monitoring

## 🛠️ Development

### Project Structure
```
SeeRM/
├── app/
│   ├── core/              # Configuration, models, logging, exceptions
│   ├── data/              # Data access layer (Gmail, Notion, CSV)
│   ├── services/          # Business logic (digest, rendering)
│   ├── workflows/         # End-to-end orchestration
│   ├── intelligence/      # Intelligence analysis system
│   ├── reports/           # Report generation
│   ├── utils/             # Reliability patterns, helpers
│   └── main.py            # CLI interface
├── tests/                 # Comprehensive test suite
├── .github/workflows/     # GitHub Actions automation
├── files/                 # Sample data and templates
├── archive/               # Legacy scripts and docs
└── [config files]         # .env, requirements.txt, etc.
```

### Development Workflow
```bash
# Set up development environment
pip install -r requirements.txt
pip install -e .

# Install pre-commit hooks
pre-commit install

# Run tests
pytest tests/ -v

# Run with debug logging
python -m app.main --debug digest

# Performance benchmarks
pytest tests/test_digest_service.py::TestPerformanceBenchmarks -v
```

### Adding Features
1. **Follow Architecture**: Use existing patterns in `app/core/`
2. **Add Tests**: Comprehensive coverage in `tests/`
3. **Structured Logging**: Use correlation IDs and structured output
4. **Error Handling**: Implement circuit breakers and retry logic
5. **Configuration**: Add options to `app/core/config.py`
6. **Documentation**: Update README and relevant docs

## 📈 Performance & Benchmarks

### Processing Performance
- **CSV Processing**: <100ms for 221 companies
- **Digest Generation**: <200ms for 500 companies  
- **HTML Rendering**: <50ms per digest
- **Intelligence Analysis**: <10ms per company
- **End-to-End Workflow**: <5s complete pipeline
- **Memory Usage**: <100MB typical operation

### Scalability
- **Tested**: 500+ companies in production
- **Parallel Processing**: 6 concurrent workers (configurable)
- **Rate Limiting**: Respects API limits (Gmail, Notion, OpenAI)
- **Circuit Breakers**: Prevent cascade failures
- **Batch Processing**: Efficient for large datasets

## 🤝 Contributing

### Getting Started
1. **Fork** the repository
2. **Clone** your fork: `git clone https://github.com/YOUR_USERNAME/SeeRM.git`
3. **Install** dependencies: `pip install -r requirements.txt`
4. **Create** a feature branch: `git checkout -b feature/your-feature`
5. **Test** your changes: `pytest tests/`
6. **Commit** and push: `git commit -am 'Add feature'`
7. **Submit** a Pull Request

### Development Guidelines
- **Code Style**: Follow PEP 8, use `black` for formatting
- **Type Hints**: Use type annotations throughout
- **Testing**: Maintain 90%+ test coverage
- **Logging**: Use structured logging with correlation IDs
- **Documentation**: Update relevant docs and docstrings
- **Performance**: Consider impact on processing times

### Reporting Issues
- Use GitHub Issues for bug reports and feature requests
- Provide detailed reproduction steps
- Include system information and log outputs
- Check existing issues before creating new ones

## 📋 Recent Updates

### v2024.1 - Intelligence Reports System
- ✅ **NEW**: Company Deep Dive reports with comprehensive analysis
- ✅ **NEW**: New Client Summary reports with onboarding intelligence  
- ✅ **NEW**: Weekly News Digest with categorized portfolio news
- ✅ **Enhanced**: Notion integration with multiple database support
- ✅ **Enhanced**: Email delivery system with HTML formatting
- ✅ **Enhanced**: CSV-based report generation (works without external APIs)

### v2023.2 - Architecture Refactor
- ✅ **Refactored**: Modular architecture with clear separation of concerns
- ✅ **Added**: Comprehensive testing suite (93% coverage)
- ✅ **Added**: Enterprise reliability patterns (circuit breakers, rate limiting)
- ✅ **Added**: Structured logging with correlation IDs
- ✅ **Enhanced**: Performance optimization (5x faster processing)
- ✅ **Enhanced**: GitHub Actions automation

### Migration Notes
- **100% backward compatibility** with legacy system
- **Same environment variables** and configuration
- **Identical output format** for existing workflows
- **Legacy scripts preserved** in `archive/` directory

## 📚 Documentation

- **[HOW_TO_USE.txt](./HOW_TO_USE.txt)** - Business user guide and overview
- **[INTELLIGENCE_REPORTS.md](./INTELLIGENCE_REPORTS.md)** - Detailed intelligence reports documentation
- **[Archive Documentation](./archive/)** - Legacy system references
- **[Test Documentation](./tests/)** - Testing guide and examples
- **API Documentation** - Generated from docstrings (run `pydoc app`)

### Notion News Items Database

To track previously seen news, configure the Notion database referenced by
`NOTION_INTEL_DB_ID` with the following properties:

- `Title` (title) – article headline
- `URL` (url) – canonical article link (used as unique key)
- `First Seen` (date) – set automatically when the link first appears
- `Last Seen` (date) – updated whenever the link reappears
- `Callsign` (relation → Companies DB) – link back to the company page
- `Source` (select or rich text) – publication/source name
- `Published At` (date) – article publication date (optional but recommended)
- `Summary` (rich text) – optional article synopsis

The Weekly News job writes one row per URL and only surfaces items whose
`First Seen` date falls within the report window, preventing duplicates from
week to week.

## 🏷️ Releases

We publish wheels to GitHub Releases. Two options:

- Tag-driven release (recommended):
  1. Bump `version` in `pyproject.toml`.
  2. Create a tag: `git tag v0.1.0 && git push origin v0.1.0`.
  3. GitHub Actions builds the wheel/sdist and attaches them to the new Release.

- Manual release via workflow_dispatch:
  1. Open GitHub → Actions → `Release` workflow → `Run workflow`.
  2. Provide the `tag` (e.g., `v0.1.0`) and optionally mark as prerelease.
  3. The workflow builds artifacts and creates/updates the Release.

Artifacts:
- Wheel and sdist appear under the Release assets.
- Consumers can install with `pipx install https://github.com/<org>/<repo>/releases/download/vX.Y.Z/seerm-X.Y.Z-py3-none-any.whl`.

## 🔐 Security & Privacy

- **No Data Persistence**: All customer data processed in memory only
- **Credential Validation**: Pydantic models with comprehensive validation
- **API Protection**: Rate limiting and circuit breakers prevent abuse
- **Secure Logging**: No sensitive data in log outputs
- **Environment Isolation**: Separate configurations for dev/staging/production
- **Access Control**: GitHub secrets for credential management
- **Audit Trail**: Correlation IDs for request tracing

## 🗂️ Notion Database Schemas

To keep all workflows compatible, configure your Notion databases with the following
property names and types:

**Companies Database (`NOTION_COMPANIES_DB_ID`)**
- `Callsign` (title) – primary key
- `Company` (rich text) – optional display name
- `Website` (url)
- `Domain` (url or rich text)
- `Owners` (rich text)
- `Needs Dossier` (checkbox)
- `Latest Intel` (rich text)
- `Last Intel At` (date)

**News Items Database (`NOTION_INTEL_DB_ID`)**
- `Title` (title)
- `URL` (url)
- `First Seen` (date)
- `Last Seen` (date)
- `Callsign` (relation → Companies database)
- `Source` (select / multi-select / rich text)
- `Published At` (date)
- `Summary` (rich text, optional)

**Reports Database (`NOTION_REPORTS_DB_ID`)**
- `Name` (title)
- `Report Type` (select)
- `Generated` (date)
- `Status` (select)
- `Duration` (rich text or number)
- `News Items` (number)
- `Week Of` (date or rich text)

If you rename a column, update the Notion database to match these names so every
CLI workflow, GitHub Action, and the new news-diff pipeline stay in sync.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🏆 Built for Enterprise

**SeeRM combines reliability, performance, and maintainability to deliver production-ready client intelligence automation.**

- 🎯 **Business Focus**: Actionable insights for relationship managers and leadership
- 🔧 **Developer Friendly**: Clean architecture, comprehensive testing, detailed documentation  
- 🚀 **Operations Ready**: Automated deployment, monitoring, and error handling
- 📈 **Scalable**: Handles growing portfolios with consistent performance
