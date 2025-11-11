# Quick Start: Testing Tiered LLM System

## Prerequisites

1. **OpenAI API Key** with access to `gpt-5-mini` (and `gpt-5` for synthesis)
2. **Python environment** with dependencies installed
3. **Test company data** in your CSV roster

## Quick Test (Single Company)

> 💡 **Sample data**: If you just want to smoke-test the pipeline, point `CSV_SOURCE_PATH`
> (or drop the Gmail requirement entirely) at one of the sanitized fixtures in `files/`
> such as `files/SeeRM_Master_Query_2025-09-01T09_05_03.416470514Z.csv`.
> These snapshots mirror the “Org Profile — Will Mitchell” Gmail export that the cron jobs ingest.

### 1. Set Environment Variables

```bash
cd /Users/wmitchell/Documents/project_rm_at_scale/SeeRM

# Required
export OPENAI_API_KEY="your-key-here"

# Enable tiered LLM
export BASELINE_USE_TIERED_LLM=true

# Other required vars (from your existing setup)
export GMAIL_CLIENT_ID="..."
export GMAIL_CLIENT_SECRET="..."
export GMAIL_REFRESH_TOKEN="..."
export GMAIL_USER="..."
export GOOGLE_API_KEY="..."
export GOOGLE_CSE_ID="..."
export NOTION_API_KEY="..."
export NOTION_COMPANIES_DB_ID="..."

# Optional: CSV source
export PROFILE_SUBJECT="Org Profile — Will Mitchell"
export BASELINE_CALLSIGNS="test-company-callsign"  # Single company for testing
```

### 2. Run Baseline Generator

The production cron job (`.github/workflows/baseline.yml`) invokes this same module, so
running it locally exercises the exact code path that updates Notion.

```bash
python -m app.dossier_baseline
```

### 3. Watch for Log Output

You should see:
```
[TIERED LLM MODE] Using multi-stage LLM intelligence system (identity → funding → profile → synthesis)
[TIERED LLM MODE] Identity model: gpt-5-mini
[TIERED LLM MODE] Funding model: gpt-5-mini
[TIERED LLM MODE] Synthesis model: gpt-5
...
[TIERED LLM] test-company: Resolving identity...
[TIERED LLM] test-company: Identity resolved (confidence=0.85, status=active)
[TIERED LLM] test-company: Researching funding...
[TIERED LLM] test-company: Funding researched (confidence=0.75, stage=Seed)
[TIERED LLM] test-company: Collecting news and background...
[TIERED LLM] test-company: Generating dossier...
[TIERED LLM] test-company: Dossier generated successfully
```

### 4. Check Notion

Look for the updated company page in your Notion Companies DB with the new dossier.

## Test Individual Agents

### Test Identity Agent

```python
# In Python REPL or notebook
from app.intelligence.llm_identity_agent import LLMIdentityAgent
import os

os.environ["OPENAI_API_KEY"] = "your-key"

agent = LLMIdentityAgent()

# Test case 1: Direct domain match
identity = agent.resolve(
    callsign="test-co",
    dba="Aalo Atomics",
    owners=["Matt Loszak"],
    csv_domain="aalo.com",
)

print(f"Domain: {identity.current_domain}")
print(f"Confidence: {identity.confidence}")
print(f"Status: {identity.status}")
print(f"Reasoning: {identity.reasoning}")
print(f"Sources: {identity.sources}")

# Test case 2: Empty domain (stealth company)
identity2 = agent.resolve(
    callsign="stealth-co",
    dba="Stealth Startup",
    owners=["John Doe"],
    csv_domain=None,
    linkedin_url="https://linkedin.com/in/johndoe",
)

print(f"\nStealth Test:")
print(f"Status: {identity2.status}")
print(f"Confidence: {identity2.confidence}")
print(f"Reasoning: {identity2.reasoning}")
```

### Test Funding Agent

```python
from app.intelligence.llm_funding_agent import LLMFundingAgent

agent = LLMFundingAgent()

funding = agent.research(
    dba="Aalo Atomics",
    owners=["Matt Loszak"],
    current_domain="aalo.com",
    current_website="https://aalo.com",
    identity_status="active",
    identity_confidence=0.9,
)

print(f"Stage: {funding.funding_stage}")
print(f"Confidence: {funding.confidence}")
if funding.latest_round:
    print(f"Amount: ${funding.latest_round.amount_usd:,}")
    print(f"Round: {funding.latest_round.round_type}")
print(f"Reasoning: {funding.reasoning}")
```

### Test Synthesis Agent

```python
from app.intelligence.llm_synthesis_agent import LLMSynthesisAgent

agent = LLMSynthesisAgent()

dossier = agent.generate_dossier(
    identity={
        "current_domain": "aalo.com",
        "current_website": "https://aalo.com",
        "status": "active",
        "confidence": 0.9,
        "reasoning": "Domain verified, strong brand match",
    },
    funding={
        "funding_stage": "Seed",
        "latest_amount_usd": 5000000,
        "latest_round_type": "Seed",
        "confidence": 0.8,
    },
    profile={
        "products": ["Thermal energy storage platform"],
        "target_customers": ["Large industrial facilities"],
        "value_proposition": "High-density energy storage system for decarbonising industrial heat.",
        "confidence": 0.7,
        "sources": ["https://aalo.com"],
    },
    news_items=[],
    people_background=[],
    dba="Aalo Atomics",
    owners=["Matt Loszak"],
)

print(dossier.markdown_content)
print("\n--- Confidence Note ---")
print(dossier.confidence_note)
print("\n--- Next Steps ---")
for step in dossier.next_steps:
    print(f"- {step}")
```

## Test Edge Cases

### Test 1: Domain Redirect

```python
identity = agent.resolve(
    callsign="redirect-test",
    dba="Old Company Name",
    owners=["Founder Name"],
    csv_domain="oldcompany.com",  # Redirects to newcompany.com
)
# Should detect redirect and return new domain
```

### Test 2: Inactive Domain

```python
identity = agent.resolve(
    callsign="inactive-test",
    dba="Dead Startup",
    owners=["Former Founder"],
    csv_domain="deadstartup.com",  # Parked/inactive
)
# Should mark as "inactive" and search for alternatives
```

### Test 3: Stealth Company

```python
identity = agent.resolve(
    callsign="stealth-test",
    dba="Stealth AI Startup",
    owners=["Jane Smith"],
    csv_domain=None,
    linkedin_url="https://linkedin.com/in/janesmith",
)
# Should mark as "stealth" and provide founder profiles
```

### Test 4: No Funding Information

```python
funding = agent.research(
    dba="Bootstrap Company",
    owners=["Self-Funded Founder"],
    current_domain="bootstrap.com",
    identity_confidence=0.9,
)
# Should explicitly say "No public funding information available"
```

## Troubleshooting

### Error: "openai package required"
```bash
pip install openai
```

### Error: "OPENAI_API_KEY environment variable required"
```bash
export OPENAI_API_KEY="your-key"
```

### Error: "Identity resolution failed: 404"
- Check that you have access to `gpt-5-mini` in your OpenAI account
- Try with older model: `export OPENAI_LLM_IDENTITY_MODEL=gpt-5-mini`

### Fallback to Legacy Mode
If you see:
```
[TIERED LLM] company: Failed, falling back to legacy mode
```

Check the error traceback above it. Common causes:
- API rate limit hit
- Model name incorrect
- Timeout (increase if needed)

### Cost Monitoring

Each agent logs its execution. To estimate costs:
- Identity: ~$0.03 per company
- Funding: ~$0.02 per company
- Synthesis: ~$0.04-0.06 per company

Monitor your OpenAI dashboard for actual usage.

## Next Steps

Once you've validated on a few test companies:

1. **Run on 10 companies:** `export BASELINE_CALLSIGNS="company1,company2,...,company10"`
2. **Compare results:** Check Notion dossiers vs what you'd expect
3. **Validate confidence:** Do the confidence scores make sense?
4. **Check costs:** Review OpenAI API usage
5. **Deploy to production:** Update GitHub Actions workflow

## Rollback

If you need to disable:
```bash
export BASELINE_USE_TIERED_LLM=false
export BASELINE_USE_LLM_INTEL=true  # Use legacy LLM
# OR
unset BASELINE_USE_TIERED_LLM
# (will fall back to deterministic mode)
```

## Support

- **Documentation:** `docs/TIERED_LLM_SYSTEM.md`
- **PR Summary:** `TIERED_LLM_PR_SUMMARY.md`
- **Code:** `app/intelligence/llm_*_agent.py`
