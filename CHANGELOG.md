# Changelog

All notable changes to Claude Command Center are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- pytest test suite for API server endpoints
- GitHub Actions CI workflow (lint, format, typecheck, test)
- `pyproject.toml` with dev dependencies and tool configs
- `Makefile` with common development targets
- `config/schema.sql` — committed database schema documentation
- `scripts/ccc-backup.py` — SQLite backup with rotation
- `CHANGELOG.md` (this file)

## [1.3.0] - 2026-02-14

### Fixed
- ROI mismatch between Overview tab (21x) and Cost tab (54x) — Cost tab JS had hardcoded wrong Opus pricing ($15/$75 instead of $5/$25)
- `/api/cost` endpoint now computes live from SQLite instead of reading stale `cost-data.json`
- Context Packs savings now uses real `cost_estimate` from `daily_stats` instead of deprecated per-message estimates
- Hero card text overflow on Cognitive tab — `clamp()` font sizing with ellipsis
- Nav tab overflow — shortened names (Co-Evo, Packs, Outcomes, Tools, Supermem, Infra), scroll fade mask

### Changed
- Cost tab JS reads pricing from `PRICING_DATA` variable instead of hardcoded values

## [1.2.0] - 2026-02-13

### Added
- Staggered entrance animations for stat/cost cards
- Model tag component (`.model-tag`) for Cost tab cards
- Scroll fade mask on nav tabs

### Changed
- Visual overhaul: darker backgrounds, dual-layer glow shadows, enhanced glassmorphism
- Nav bar: improved backdrop-filter and active tab styling
- Typography: drop-shadow on values, refined stat card hierarchy

### Fixed
- Duplicate LIVE indicator (removed static, kept SSE-injected)
- Commands tab pricing updated to Opus 4.6 ($5/$25), Sonnet 4.5 ($3/$15), Haiku 4.5 ($1/$5)

## [1.1.0] - 2026-02-12

### Fixed
- Cost model: changed from $200/day to $200/month subscription
- Token-level pricing: Opus 4.6 ($5/$25 in/out, $0.50 cache read)
- `fix-all-dashboard-data.py` Step 16 INSERT OR REPLACE was zeroing token columns — now uses full column list

### Changed
- All cost calculations flow through `pricing.py` centralized module

## [1.0.0] - 2026-02-12

### Added
- 15-tab real-time analytics dashboard
- SSE streaming with 3s polling, MD5 change detection, multi-client broadcast
- 12 REST API endpoints + 1 SSE stream
- Self-healing engine with 8 error patterns and 89% recovery rate
- 5-layer protection: Watchdog, KeepAlive, Bootstrap, Wake Hook, Self-Heal
- SQLite single source of truth with WAL mode
- Dashboard showcase: animated GIF + 15 tab screenshots
- Supreme README with Mermaid architecture diagrams, shields.io badges
- LaunchAgent daemon (`com.claude.api-server`) with KeepAlive
- Keyboard shortcuts for tab switching (1-9, 0, O, P, S, C, I)
- Token breakdown: input/output/cache differentiation per model
