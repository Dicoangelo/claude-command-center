[PRD]
# PRD: CCC Data Intelligence Upgrade

## Overview

The Claude Command Center dashboard sits on top of `claude.db` (22 tables), but the broader Antigravity ecosystem contains **massive uncaptured data** across 3 databases and 24 JSONL files. This upgrade captures the highest-value missing data, adds new dashboard tabs, and hardens the pipeline against the silent failures that caused 40+ days of data staleness.

**The numbers:**
- `antigravity.db`: 33,240 findings, 8,156 sessions, 1,424 papers, 1,620 DQ scores — ZERO in CCC
- `supermemory.db`: 6,857 memory items, 1,893 learnings, 12.1M knowledge links — ZERO in CCC
- 12 JSONL files (39,745 lines) with cost tracking, DQ scores, effectiveness, errors — NOT backfilled

## Goals

- Capture all high-value data from antigravity.db, supermemory.db, and uncaptured JSONL files into claude.db
- Add 3 new dashboard tabs: Research Intelligence, Cost Intelligence, Pipeline Health
- Eliminate silent failures with structured error logging and daemon health monitoring
- Make fix-data-flows.py the single reliable ETL pipeline for ALL data sources

## Quality Gates

These commands must pass for every user story:
- `cd ~/projects/core/claude-command-center && python3 -m pytest tests/ -v` — all tests pass
- `ruff check scripts/ tests/` — lint clean
- `curl -s http://localhost:8766/api/health` — returns 200

For data stories, also verify:
- `sqlite3 ~/.claude/data/claude.db "SELECT COUNT(*) FROM <table>"` — rows > 0
- New API endpoints return valid JSON

For UI stories, also include:
- Take Chrome DevTools screenshot to verify visual correctness

## User Stories

### US-001: Cost Tracking Table + Backfill
**Description:** As a dashboard user, I want to see actual API cost data so that I can track spend trends and ROI accurately.

**Acceptance Criteria:**
- [ ] Create `cost_events` table in schema.sql: `(id, timestamp, model, input_tokens, output_tokens, cache_tokens, cost_usd, session_id)`
- [ ] Add cost backfill to fix-data-flows.py from `cost-tracking.jsonl` (2,757 entries)
- [ ] Incremental — only insert entries newer than MAX(timestamp)
- [ ] Add `/api/cost-detail` endpoint returning daily cost breakdown by model
- [ ] Verify: `SELECT COUNT(*) FROM cost_events` > 2000 after backfill

### US-002: DQ Score History Table + Backfill
**Description:** As a dashboard user, I want full DQ scoring history so that I can see routing quality trends over time.

**Acceptance Criteria:**
- [ ] Create `dq_score_events` table: `(id, timestamp, query_hash, model, dq_score, validity, specificity, correctness, complexity, routing_method)`
- [ ] Backfill from `dq-scores.jsonl` (4,940 entries) — currently only 1,208 in routing_decisions
- [ ] Backfill from antigravity.db `dq_scores` table (1,620 entries), dedup by timestamp
- [ ] Add `/api/dq-history` endpoint with daily averages and trend line data
- [ ] Verify: `SELECT COUNT(*) FROM dq_score_events` > 4000

### US-003: Research Intelligence Tables + Backfill
**Description:** As a dashboard user, I want to see my research corpus (papers, findings, URLs) so that I can track knowledge accumulation.

**Acceptance Criteria:**
- [ ] Create `research_findings` table: `(id, timestamp, content, source, tier, category, session_id, quality_score)`
- [ ] Create `research_papers` table: `(id, arxiv_id, title, relevance_score, added_at, category)`
- [ ] Backfill findings from antigravity.db `findings` (33,240 rows)
- [ ] Backfill papers from antigravity.db `papers` (1,424 rows)
- [ ] Add `/api/research` endpoint returning totals, top papers, recent findings
- [ ] Verify: findings > 30000, papers > 1000

### US-004: Knowledge & Memory Stats Table + Backfill
**Description:** As a dashboard user, I want to see my knowledge graph stats (memory items, learnings, links) so that I can track intellectual capital.

**Acceptance Criteria:**
- [ ] Create `knowledge_stats` table: `(id, date, memory_items, learnings, memory_links, error_patterns, reviews_due)`
- [ ] Backfill daily snapshots from supermemory.db counts
- [ ] Add `/api/knowledge` endpoint with current totals and growth trend
- [ ] Verify: at least 1 row in knowledge_stats with accurate counts

### US-005: Effectiveness & Co-Evolution Backfill
**Description:** As a dashboard user, I want to see how the co-evolution system is performing.

**Acceptance Criteria:**
- [ ] Create `effectiveness_events` table: `(id, timestamp, metric, value, context)`
- [ ] Create `modification_events` table: `(id, timestamp, mod_type, target, status, confidence)`
- [ ] Backfill from `effectiveness.jsonl` (7,905 entries)
- [ ] Backfill from `modifications.jsonl` (623 entries)
- [ ] Add `/api/coevolution` endpoint with effectiveness trend and recent modifications
- [ ] Verify: effectiveness_events > 7000, modification_events > 500

### US-006: Error Catalog Table + Backfill
**Description:** As a dashboard user, I want to see error patterns and their resolution status.

**Acceptance Criteria:**
- [ ] Create `error_catalog` table: `(id, timestamp, error_type, message, context, resolved, resolution)`
- [ ] Backfill from `errors.jsonl` (373 entries)
- [ ] Backfill from antigravity.db `error_patterns` (39 rows)
- [ ] Backfill from supermemory.db `error_patterns` (8 rows), dedup
- [ ] Add `/api/errors` endpoint with error frequency, resolution rate, top patterns
- [ ] Verify: error_catalog > 350

### US-007: Silent Failure Elimination
**Description:** As a maintainer, I want all data pipeline failures to be logged visibly.

**Acceptance Criteria:**
- [ ] Create `pipeline_health` table: `(id, timestamp, pipeline_name, status, rows_processed, error_message, duration_ms)`
- [ ] Wrap every fix-data-flows.py function in try/except that logs to `pipeline_health`
- [ ] Add `/api/pipeline-health` endpoint showing last run status per pipeline
- [ ] Replace all bare `except: pass` with `except Exception as e:` that logs
- [ ] Add 3 tests for pipeline health logging
- [ ] Verify: `SELECT COUNT(DISTINCT pipeline_name) FROM pipeline_health` >= 8

### US-008: Daemon Health Table + API
**Description:** As a dashboard user, I want to see daemon fleet status in the dashboard.

**Acceptance Criteria:**
- [ ] Create `daemon_health` table: `(id, timestamp, daemon_name, status, pid, exit_code)`
- [ ] Add daemon check to fix-data-flows.py parsing `launchctl list`
- [ ] Add `/api/daemons` endpoint returning daemon states
- [ ] SSE heartbeat includes daemon health summary
- [ ] Verify: daemon_health populated with accurate states

### US-009: Research Intelligence Dashboard Tab (Tab 18)
**Description:** As a dashboard user, I want a Research tab showing papers, findings, and knowledge growth.

**Acceptance Criteria:**
- [ ] Add tab 18 to dashboard HTML with keyboard shortcut
- [ ] Display: total findings, total papers, top 10 papers by relevance
- [ ] Chart: findings accumulation over time (line chart)
- [ ] Chart: papers by category (bar chart)
- [ ] Screenshot → `assets/tabs/18-research.png`

### US-010: Cost Intelligence Dashboard Tab (Tab 19)
**Description:** As a dashboard user, I want a Cost tab showing actual spend and model efficiency.

**Acceptance Criteria:**
- [ ] Add tab 19 to dashboard HTML with keyboard shortcut
- [ ] Chart: daily cost trend (line), cost by model (stacked bar), cost per message
- [ ] Display: predicted monthly spend, comparison to $200/day subscription
- [ ] Screenshot → `assets/tabs/19-cost.png`

### US-011: Pipeline Health in Infrastructure Tab
**Description:** As a dashboard user, I want Infrastructure tab to show pipeline and daemon health.

**Acceptance Criteria:**
- [ ] Add pipeline health section to existing Infrastructure tab
- [ ] Show: last run, status (green/yellow/red), rows processed per pipeline
- [ ] Show: daemon fleet status with PIDs
- [ ] Red indicator if any pipeline failed in 24h

### US-012: Schema Migration System
**Description:** As a maintainer, I want automatic schema migrations for table changes.

**Acceptance Criteria:**
- [ ] Create `scripts/migrate.py` reading `config/migrations/` directory
- [ ] Numbered .sql files: 001_add_cost_events.sql, etc.
- [ ] Track applied in `metadata` table
- [ ] Runs idempotently
- [ ] Create migrations for all new tables (US-001 through US-008)
- [ ] Add 2 tests: idempotency and table existence

## Functional Requirements

- FR-1: All new tables via migration files, not manual ALTER TABLE
- FR-2: All backfills incremental (newer than MAX(timestamp) only)
- FR-3: All backfills log to pipeline_health
- FR-4: Cross-DB reads use separate connections with read-only and short timeouts
- FR-5: New endpoints follow existing pattern: connect → query → close → send_json
- FR-6: SSE stats include new table row counts
- FR-7: New tabs keyboard-navigable, matching dark theme

## Non-Goals

- Rewriting dashboard to component framework
- Real-time streaming from antigravity.db
- External access or authentication
- Modifying antigravity.db or supermemory.db schemas
- Adding new LaunchAgent daemons

## Dependencies

```
US-012 (migrations) ─┐
US-007 (pipeline)  ──┤── can run in parallel (no deps)
US-001..006 (tables) ┘
                     │
US-008 (daemons)  ───── depends on US-007
US-009 (research tab) ── depends on US-003
US-010 (cost tab)  ───── depends on US-001
US-011 (infra tab) ───── depends on US-007 + US-008
```

## Success Metrics

| Metric | Before | After |
|--------|--------|-------|
| Tables with live data | 10/22 | 18/30+ |
| JSONL files backfilled | 2 | 10+ |
| Cross-DB data in CCC | 0 rows | 40,000+ |
| Silent failure detection | None | Every pipeline logged |
| Dashboard tabs | 17 | 20 |
| Cost tracking | JSONL only | SQLite + dashboard |
| Research visibility | Zero | 33K findings + 1.4K papers |
[/PRD]
