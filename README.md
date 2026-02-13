# Claude Command Center (CCC)

Real-time dashboard for Claude Code infrastructure — 15-tab analytics suite with live SSE streaming.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Browser (http://localhost:8766/dashboard)       │
│  ├── 15 interactive tabs                        │
│  └── SSE real-time updates (3s polling)         │
├─────────────────────────────────────────────────┤
│  ccc-api-server.py (ThreadingHTTPServer + SSE)  │
│  ├── /dashboard   → serves HTML + injects SSE   │
│  ├── /api/stream  → SSE event stream            │
│  ├── /api/stats   → session/usage data          │
│  ├── /api/health  → system health               │
│  └── /api/*       → cost, routing, tools, etc.  │
├─────────────────────────────────────────────────┤
│  SQLite (claude.db) — single source of truth    │
│  ├── sessions, daily_stats, routing_decisions   │
│  ├── self_heal_events, recovery_events          │
│  └── tool_usage, error_patterns                 │
├─────────────────────────────────────────────────┤
│  LaunchAgent (com.claude.api-server)            │
│  └── KeepAlive + RunAtLoad                      │
└─────────────────────────────────────────────────┘
```

## Tabs

| Tab | Content |
|-----|---------|
| Overview | Sessions, messages, tools, ROI, power dashboard, charts |
| Memory | Knowledge distribution, tags, growth |
| Activity | Daily activity, session heatmap |
| Cost | API value, subscription ROI, cache efficiency |
| Projects | Repository stats, code metrics |
| Commands | CLI command usage |
| Routing | DQ scores, model routing accuracy |
| Co-Evolution | System evolution tracking |
| Context Packs | Semantic context pack metrics |
| Session Outcomes | ACE analysis, session quality |
| Productivity | Write/read ops, peak days |
| Tool Analytics | Tool usage rates, success metrics |
| Supermemory | Long-term memory, spaced repetition |
| Cognitive | Energy levels, flow state, fate prediction |
| Infrastructure | Daemon health, self-heal, watchdog |

## Quick Start

```bash
# Live dashboard (syncs data + starts server + opens browser)
ccc-live

# Static dashboard (generates HTML file)
ccc

# Server only
python3 scripts/ccc-api-server.py --port 8766

# Health check
curl http://localhost:8766/api/health
```

## Infrastructure

```bash
# Self-heal engine
python3 scripts/ccc-self-heal.py

# Watchdog guardian
python3 scripts/ccc-watchdog.py

# Bootstrap all daemons
bash scripts/ccc-bootstrap.sh

# Status check
bash scripts/ccc-status.sh
```

## Data Pipeline

```
Session transcripts
    → fix-all-dashboard-data.py (scan + aggregate)
    → sqlite-to-jsonl-sync.py (bridge)
    → ccc-sql-data.py (export JSON)
    → ccc-generator.sh (render HTML)
    → ccc-api-server.py (serve live)
```

## Deploy (macOS LaunchAgent)

```bash
cp deploy/com.claude.api-server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.claude.api-server.plist
```

## Requirements

- Python 3.10+
- SQLite3
- macOS (LaunchAgent for auto-start)
- Chart.js (CDN, loaded by dashboard HTML)
