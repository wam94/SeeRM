# Customer Intelligence Platform — Architecture & Implementation Guide (Merged Plan)

**Audience:** engineers + ops building an internal customer intelligence platform (CIP).  
**Goal:** merge the best of two proposals into a single, concrete, implementation‑ready plan you can hand to an LLM (or a teammate) to drive the next steps.

---

## 0) Principles (read me first)

- **Canonical store, Notion as publisher.** Notion is for dissemination and light editing; your **source of truth** lives in a database.
- **Evidence‑first.** Persist raw sources (URLs, dates, text) and surface **citations in every summary**.
- **Idempotent runs with overrides.** Human edits beat heuristics; the system must **replay safely**.
- **Stable identity.** Deterministic `org_id`; explicit precedence for fields (override > CSV > discovered).
- **Human‑in‑the‑loop.** Email drafts and dossier updates require human confirmation by design.
- **Reliability before scale.** Matrix batching, checkpoints, HTTP caching, time caps, retries.
- **Privacy & governance.** Minimize PII, restrict access, keep an audit log of manual overrides.
- **Start simple; grow.** Ship with SQLite or PG; keep Actions; upgrade to Prefect/Airflow only when orchestration pressure is real.

---

## 1) System Goals

- Weekly executive dossiers per client combining **internal product usage** and **external intel** (news, funding, etc.).  
- Aggregate portfolio views (health, adoption, risk) with drill‑down into each company.  
- Proactive & reactive **LLM‑assisted comms**: “congrats on funding,” “usage dip follow‑up,” incident responses—**always with citations**.  
- Expand inputs: Zendesk/support, email context, call transcripts, etc.

---

## 2) High‑Level Architecture

```
            +-----------------+      +--------------------+
Internal →  | Ingestion Jobs  | ---> |  Canonical Store   |  <--- External Intel (News API/RSS/CSE)
DB/CSV      |  (Actions/CI)   |      |  (SQLite/PG)       |  <--- Zendesk / Email metadata
            +--------+--------+      +------+-------------+
                     |                      |
                     |                      v
                     |                Vector Index (per org)
                     |                      |
                     v                      v
                Summarization / RAG  <---  Evidence (events)
                     |
                     v
               Notion (Dossiers, Intel)  +  BI Dashboards (Metabase/Looker)
                     |
                     v
            Human-in-the-loop Email Drafts (CLI/Service)
```

---

## 3) Data Model (canonical store)

> Start with SQLite for speed of adoption; swap in Postgres/Neon/Render/Fly by changing the connection string. If time‑series usage gets large, consider TimescaleDB or PG table partitioning.

### 3.1 Core Tables

**orgs**
- `org_id TEXT PRIMARY KEY` — stable slug from callsign.
- `callsign TEXT UNIQUE`
- `display_name TEXT`
- `domain TEXT`
- `domain_locked BOOLEAN DEFAULT FALSE`
- `official_site_url TEXT`
- `aliases JSON`  (array of strings)
- `identifiers JSON` (e.g., `{crunchbase, linkedin, x_handle}`)
- `manual_notes TEXT`
- `updated_at TIMESTAMP`

**overrides**  _(human‑in‑the‑loop)_
- `org_id TEXT`
- `field TEXT`  (e.g., `domain`, `display_name`, `funding.last_round`)
- `value JSON`
- `source_url TEXT`
- `as_of_date DATE`
- `active BOOLEAN`
- `updated_at TIMESTAMP`
- **PK:** `(org_id, field, as_of_date)`

**events**  _(evidence & observations)_
- `event_id TEXT PRIMARY KEY`  (hash(url + published_at))
- `org_id TEXT`
- `kind TEXT`  (`news|funding|press|tweet|support|product|call`)
- `title TEXT`
- `url TEXT`
- `source TEXT`
- `published_at DATE`
- `raw_text TEXT`
- `extracted JSON` (NER/entities, amounts, sentiment, etc.)
- `ingested_at TIMESTAMP`

**funding_facts**
- `org_id TEXT`
- `round_type TEXT` (`seed|A|B|...`)
- `announced_on DATE`
- `amount_usd BIGINT`
- `investors JSON`
- `source_url TEXT`
- `confidence REAL`
- `created_at TIMESTAMP`
- **PK:** `(org_id, round_type, announced_on, source_url)`

**usage_metrics**  _(time series from internal DB)_
- `org_id TEXT`
- `metric TEXT` (e.g., `maus`, `api_calls`, `feature_x_active`)
- `ts DATE`
- `value DOUBLE PRECISION`
- **PK:** `(org_id, metric, ts)`

**interactions**  _(Zendesk/email/calls summary metadata)_
- `interaction_id TEXT PRIMARY KEY`
- `org_id TEXT`
- `channel TEXT` (`zendesk|email|call`)
- `occurred_at TIMESTAMP`
- `summary TEXT`
- `sentiment REAL`
- `raw_url TEXT`

**action_items**  _(AI suggestions & alerts)_
- `action_id TEXT PRIMARY KEY`
- `org_id TEXT`
- `trigger TEXT` (`usage_drop|funding|negative_support|renewal_window`)
- `generated_at TIMESTAMP`
- `status TEXT` (`open|dismissed|done`)
- `payload JSON` (draft email, talking points, links)

**run_checkpoints**
- `job_name TEXT`
- `slice_key TEXT`
- `position TEXT`
- `updated_at TIMESTAMP`
- **PK:** `(job_name, slice_key)`

### 3.2 Field Precedence Rules

For any computed field:
```
if override.active: use override.value
elif csv_value is not null: use csv_value
else: use discovered_value (from CSE/news heuristics)
```

Funding “best fact” = max-confidence among: **manual override > API (e.g., CB) > heuristics from events**.

### 3.3 Identity & Domain Resolution

- `org_id = slugify(callsign)` (deterministic).  
- Domain pick order: `overrides.domain (if domain_locked)` → `CSV.domain` → **discovered** candidates.  
- When discovering a domain, store as **candidate** with a `verify_domain(url)` score: resolves, `<title>` or `og:site_name` matches name/aliases, etc. Do not auto‑promote if `domain_locked`.

---

## 4) Pipelines & Orchestration

**Keep GitHub Actions** initially; split long runs; add resumability.

### 4.1 Ingestion cadence (suggested)

- **Internal usage:** daily (or weekly to start) → `usage_metrics`  
- **News/External intel:** daily/weekly → `events(kind='news'|'press'|'tweet')`  
- **Zendesk:** daily → `events(kind='support')` + `interactions(channel='zendesk')`  
- **Email context:** on‑demand → `interactions(channel='email')`

### 4.2 Reliability Tactics

- **Matrix batching:** slice orgs into shards.  
- **Wall‑clock cap:** 30–35 min per run; persist `run_checkpoints` on exit.  
- **Resume:** next run resumes from checkpoint.  
- **HTTP caching:** ETag / If‑Modified‑Since; memoize CSE results N days.  
- **Retries/backoff** around network + Notion.  
- **Structured logs** (OpenTelemetry‑friendly JSON).  
- **Dry‑run mode** that prints would‑change diffs.

### 4.3 Example GitHub Actions snippet

```yaml
name: news_job
on:
  schedule: [{cron:  '0 8 * * 1-5'}]
  workflow_dispatch: {}
jobs:
  shard:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        slice: [a, b, c, d]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - name: Run shard
        env:
          SLICE: ${{ matrix.slice }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}  # or sqlite path
        run: |
          python scripts/run_batch.py --slice "$SLICE" --max-minutes 33 --resume-from-checkpoint
```

---

## 5) Evidence‑First Summarization (RAG)

1) **Persist** every fetched item into `events` (with `raw_text`).  
2) Build a **per‑org vector index** (FAISS on disk) from `raw_text + title`.  
3) At generation time, **retrieve top‑K** snippets for the org and pass to the LLM.  
4) **Always emit citations** (`[Source — yyyy-mm-dd](url)`) and store the rendered summary + links back into Notion and/or `action_items.payload`.

### 5.1 Minimal prompt template (executive summary)

```
You are drafting an executive client brief for internal use.
Include: product usage trends, notable external news, and support themes.
Cite all claims from the provided evidence; if not present, say “No evidence.”

Format:
- Company: <display_name> (<domain>)
- Product usage: <trend + metrics; cite>
- External intel (last 30–60 days): <bullets; cite each>
- Support signals: <themes; cite>
- Risks & opportunities: <bullets; cite if evidence-based>
- Next actions (if any): <bullets; internal suggestions; no citation needed>

Evidence (snippets with URLs, dates, titles) is provided below.
Only use the evidence; do not invent facts.
```

### 5.2 Notion Output Conventions

- Include a **“Sources”** section with Markdown links (URL + `published_at`).  
- Keep a **“Last generated”** timestamp.  
- Store the same content in a `summaries` block in the store if you want BI coverage of summary freshness.

---

## 6) Email Generation (human‑in‑the‑loop)

Implement `compose_email(org_id, intent)` → returns `{subject, body_md, citations[]}`.  
- **Intents:** `congrats_funding`, `quarterly_checkin`, `usage_dip_followup`, `incident_followup`.  
- Pull: org context, last 30 days of events, top usage deltas, any `overrides.manual_notes`.  
- Require human confirmation before sending; track draft creation in `action_items`.

**Prompt stub:**

```
Draft a concise email for <intent>. Use the org context and the evidence.
Tone: helpful, professional, ~100–150 words. Include inline citations as [Source].
```

---

## 7) Notion as UI (manual inputs without UX shock)

**Companies DB properties to add:**
- Verified Domain (url), **Lock Domain** (checkbox)
- Official Name, Aliases (text)
- Identifiers (CB/LinkedIn/X)
- Funding (Last Round / Date / Amount / Investors / Source URL)
- Manual Notes

On each run: pull Notion overrides → upsert into `overrides`.  
Dossiers and intel pages continue to render in Notion for org‑wide visibility.

---

## 8) Analytics

- For aggregates (adoption, coverage, funding by sector, alerts): export `orgs`, `usage_metrics`, `funding_facts`, counts from `events` → **PG/BigQuery**.  
- BI layer: **Metabase** or **Looker Studio**.  
- Keep a Notion dashboard page that links to BI charts; don’t force heavy aggregation inside Notion.

---

## 9) Security, Privacy, Compliance

- Secrets in Actions/CI; encrypt DB at rest; restrict network egress.  
- **PII minimization**: drop email bodies by default; summarize to `interactions.summary`.  
- **RBAC** at the BI layer; limit raw evidence access to approved users.  
- **Retention windows** for events/raw_text (e.g., 12–18 months).  
- **Audit log**: every override/upsert is timestamped with `source_url` and actor if available.

---

## 10) Implementation Roadmap (ship in phases)

**Phase 1 (Week 1–2): Canonical store + overrides + evidence**
- Add `app/store.py` (SQLite default; PG‑ready). Create tables if missing.
- Update `news_job.py` & `dossier_baseline.py` to:
  - merge CSV with overrides (precedence above),
  - persist evidence to `events`,
  - include `[Source — yyyy-mm-dd](url)` citations in output.
- Surface `compose_email()` CLI stub returning a draft with citations.

**Phase 2 (Week 3–4): Reliability + RAG**
- Matrix batching, checkpoints, 33‑minute cap, resume logic.
- HTTP caching & memoized CSE.
- Build per‑org FAISS index; retrieve top‑K into summaries + emails.

**Phase 3 (Week 5–6): Analytics + alerts**
- Export tabular CSV → PG/Sheets; stand up Metabase dashboards.
- Implement simple triggers → `action_items` (usage drop, funding, negative support).

**Phase 4 (Week 7+): Polish & scale**
- Optional: move orchestration to Prefect/Airflow; Timescale/partitioning for `usage_metrics`.
- Add Zendesk ingestion; redact PII; sentiment summarization.
- Add call transcript ingestion (ASR) → `events(kind='call')`.

---

## 11) Minimal Code Stubs

### 11.1 `app/store.py` (sketch)

```python
from typing import Optional, Dict, Any, Iterable
import sqlite3, json, time

def connect(db_url: str = "app/store.db"):
    return sqlite3.connect(db_url)

def init(conn):
    # create tables if not exist (orgs, overrides, events, funding_facts, usage_metrics, interactions, action_items, run_checkpoints)
    ...

def upsert_org(conn, org: Dict[str, Any]): ...
def read_overrides(conn, org_id: str) -> Dict[str, Any]: ...
def record_event(conn, event: Dict[str, Any]): ...
def upsert_funding_fact(conn, fact: Dict[str, Any]): ...
def get_checkpoint(conn, job: str, slice_key: str) -> Optional[str]: ...
def set_checkpoint(conn, job: str, slice_key: str, position: str): ...
```

### 11.2 Merge precedence helper

```python
def resolve_field(field, override, csv_val, discovered):
    if override and override.get("active"):
        return override["value"]
    return csv_val if csv_val not in (None, "") else discovered
```

### 11.3 Batch runner skeleton

```bash
python scripts/run_batch.py --slice a --max-minutes 33 --resume-from-checkpoint
```

```python
# scripts/run_batch.py
import time, argparse
start = time.time()
while time.time() - start < args.max_minutes * 60:
    # process next org; periodically write checkpoint
    ...
# write final checkpoint and exit
```

### 11.4 RAG retrieval call (pseudo)

```python
docs = faiss_index.top_k(org_id, query="", k=6)  # latest news + usage context snippets
prompt = render_template("exec_summary.txt", org=org, docs=docs)
resp = llm(prompt)
```

---

## 12) LLM Guardrails & QA

- **No‑evidence, no claim.** If the evidence doesn’t support a statement, say “No evidence.”
- **Citations required** for external facts; include URL and date.
- **Style:** concise, scannable bullets; avoid hype.
- **Regression tests:** keep golden summaries for 5–10 orgs; re‑generate on every PR; diff changes.
- **Telemetry:** log token counts, latency, cache hits.

---

## 13) Notion Property Mapping (suggested)

- `Companies.DB` → `callsign`, `display_name`, `Verified Domain`, `Lock Domain`, `Aliases`, `Identifiers`, `Funding (Last/Date/Amount/Investors/Source)`, `Manual Notes`, `Last Generated`.

---

## 14) Checklists

**Data store ready**
- [ ] Tables created
- [ ] Precedence rules implemented
- [ ] Domain verification scoring

**Pipelines reliable**
- [ ] Matrix + time cap + resume
- [ ] HTTP caching + memoized CSE
- [ ] Structured logs + dry‑run

**Summaries trustworthy**
- [ ] Evidence persisted
- [ ] RAG retrieval wired
- [ ] Citations present in output

**Governance**
- [ ] PII minimized/redacted
- [ ] RBAC enforced in BI
- [ ] Override audit trail

**Comms**
- [ ] `compose_email()` returns subject/body/citations
- [ ] Drafts logged in `action_items`
- [ ] Human review required
```

---

## 15) Appendix: Postgres DDL (sketch)

> Use appropriate JSON/JSONB types in PG; add indexes on `org_id`, `published_at`, `metric, ts`.

```sql
CREATE TABLE orgs (
  org_id TEXT PRIMARY KEY,
  callsign TEXT UNIQUE,
  display_name TEXT,
  domain TEXT,
  domain_locked BOOLEAN DEFAULT FALSE,
  official_site_url TEXT,
  aliases JSONB,
  identifiers JSONB,
  manual_notes TEXT,
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE overrides (
  org_id TEXT,
  field TEXT,
  value JSONB,
  source_url TEXT,
  as_of_date DATE,
  active BOOLEAN DEFAULT TRUE,
  updated_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (org_id, field, as_of_date)
);

CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  org_id TEXT,
  kind TEXT,
  title TEXT,
  url TEXT,
  source TEXT,
  published_at DATE,
  raw_text TEXT,
  extracted JSONB,
  ingested_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE funding_facts (
  org_id TEXT,
  round_type TEXT,
  announced_on DATE,
  amount_usd BIGINT,
  investors JSONB,
  source_url TEXT,
  confidence REAL,
  created_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (org_id, round_type, announced_on, source_url)
);

CREATE TABLE usage_metrics (
  org_id TEXT,
  metric TEXT,
  ts DATE,
  value DOUBLE PRECISION,
  PRIMARY KEY (org_id, metric, ts)
);

CREATE TABLE interactions (
  interaction_id TEXT PRIMARY KEY,
  org_id TEXT,
  channel TEXT,
  occurred_at TIMESTAMP,
  summary TEXT,
  sentiment REAL,
  raw_url TEXT
);

CREATE TABLE action_items (
  action_id TEXT PRIMARY KEY,
  org_id TEXT,
  trigger TEXT,
  generated_at TIMESTAMP DEFAULT NOW(),
  status TEXT,
  payload JSONB
);

CREATE TABLE run_checkpoints (
  job_name TEXT,
  slice_key TEXT,
  position TEXT,
  updated_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (job_name, slice_key)
);
```
