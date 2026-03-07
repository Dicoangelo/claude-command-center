[PRD]
# PRD: CCC Ground Truth Upgrade

## Overview

Fix all issues identified in the 2026-03-06 ground-truth audit of the Claude Command Center. The audit found: API server crash-looping on port conflict, 11 of 13 daemons not running, 1 failing test, stale documentation across CLAUDE.md/README/MEMORY.md, unvalidated scripts with no tests, and missing screenshots for 2 newer tabs. This PRD addresses every finding to bring documentation in line with reality and reality in line with expectations.

**Project:** `~/projects/core/claude-command-center`
**Audit reference:** `~/.claude/projects/-Users-dicoangelo/memory/ccc-grounded.md`

## Goals

- Restore the CCC API server to running state with resilient port handling
- Reduce daemon fleet to only what's needed and ensure those daemons stay alive
- Achieve 100% test pass rate (currently 137/139 — 1 failed, 1 skipped)
- Eliminate all stale claims in documentation (tab counts, test counts, ROI figures)
- Add data freshness visibility to the dashboard so users know when data is stale
- Remove or test every unvalidated script — no dead code in the repo

## Quality Gates

These commands must pass for every user story:
- `python3 -m pytest tests/ -v --tb=short` — All tests passing
- `ruff check scripts/ config/` — No lint errors

For UI/dashboard stories, also include:
- Take Chrome DevTools screenshot of affected tab(s) to verify visual correctness

## User Stories

### US-001: Fix API Server Port Conflict Crash Loop
**Description:** As a user, I want the CCC API server to start reliably so that the dashboard is always available when I need it.

**Acceptance Criteria:**
- [ ] Add SO_REUSEADDR socket option to ccc-api-server.py before server_bind()
- [ ] Add startup logic that detects port 8766 in use, identifies the PID, and kills it before binding
- [ ] Server starts successfully after a fresh launchctl kickstart of com.claude.api-server
- [ ] curl http://localhost:8766/api/health returns 200 after server start
- [ ] Error log at ~/.claude/logs/api-server-error.log shows no crash loop entries after fix

### US-002: Audit and Fix Daemon Fleet
**Description:** As a maintainer, I want only necessary daemons running so that system resources aren't wasted and the watchdog can focus on what matters.

**Acceptance Criteria:**
- [ ] Document the purpose and necessity of each of the 13 LaunchAgents in a table
- [ ] Classify each as KEEP, FIX, or REMOVE with justification
- [ ] Fix all KEEP daemons so they run successfully
- [ ] Remove plist files for REMOVE daemons
- [ ] Update ccc-watchdog.py CRITICAL_DAEMONS list to match the kept set
- [ ] Run ccc-status.sh and verify it reports all kept daemons as healthy

### US-003: Fix Failing Test test_update_daily_stats
**Description:** As a developer, I want all tests to pass so that CI is green and regressions are caught.

**Acceptance Criteria:**
- [ ] Read tests/test_datastore.py:104 and trace the root cause of the empty dates list
- [ ] Fix the bug in either the test or the datastore.py implementation
- [ ] python3 -m pytest tests/test_datastore.py -v shows 0 failures
- [ ] Full suite: 0 failures, 0 errors

### US-004: Update Stale Documentation — Tab Count and Test Count
**Description:** As a future AI session or contributor, I want documentation to match reality.

**Acceptance Criteria:**
- [ ] README.md: Update "15-Tab Mission Control" to "17-Tab Mission Control"
- [ ] README.md: Update tests badge from "124 passing" to actual passing count
- [ ] README.md: Add tabs 16 (Autonomy) and 17 (Velocity) to the tab gallery
- [ ] Grep for "12-tab" or "15-tab" references and flag for user

### US-005: Update Stale Documentation — ROI and Cost Figures
**Description:** As a user, I want cost/ROI documentation to reflect actual database values.

**Acceptance Criteria:**
- [ ] Query v_subscription_summary view for current ROI multiplier
- [ ] Query v_daily_costs for current average daily API equivalent
- [ ] Flag stale values in CLAUDE.md for user approval before editing
- [ ] Update ccc-grounded.md ROI section with current DB values
- [ ] Add note: "Auto-computed from claude.db. Self-assessed, not externally validated."

### US-006: Capture Screenshots for Tabs 16-17
**Description:** As a README visitor, I want to see all 17 tabs in the gallery.

**Acceptance Criteria:**
- [ ] Start the CCC API server (depends on US-001)
- [ ] Take Chrome DevTools screenshot of tab 16 (Autonomy) → assets/tabs/16-autonomy.png
- [ ] Take Chrome DevTools screenshot of tab 17 (Velocity) → assets/tabs/17-velocity.png
- [ ] Add both to README tab gallery

### US-007: Audit Unvalidated Scripts — Test or Remove
**Description:** As a maintainer, I want every script to be either tested or removed.

**Acceptance Criteria:**
- [ ] Determine if ccc-autonomous-brain.py, ccc-autopilot.py, ccc-intelligence-layer.py are called by anything
- [ ] If actively used: add at least 3 tests each
- [ ] If unused: remove from repo and remove symlinks from ~/.claude/scripts/

### US-008: Add Data Staleness Indicator to Dashboard
**Description:** As a dashboard user, I want to see when data was last updated.

**Acceptance Criteria:**
- [ ] Add "Last updated: X minutes ago" indicator to dashboard nav bar
- [ ] Query MAX(timestamp) from activity_events
- [ ] Yellow if > 1 hour, red if > 24 hours, green if fresh
- [ ] Updates on each SSE tick (3s)
- [ ] Chrome DevTools screenshot to verify

### US-009: Final Documentation Reconciliation
**Description:** As a future AI session, I want all memory files to reflect post-upgrade state.

**Acceptance Criteria:**
- [ ] Run full test suite — record exact counts
- [ ] Run launchctl list | grep claude — record daemon states
- [ ] Confirm server running via curl
- [ ] Update ccc-grounded.md with post-upgrade state
- [ ] Update MEMORY.md CCC section
- [ ] Grep for remaining stale references across ~/.claude/

## Dependencies

```
US-001 (fix server) ─┬─→ US-006 (screenshots) ─→ US-009 (final docs)
                     │
US-002 (fix daemons) ┤
US-003 (fix test)    ┤
US-004 (docs: tabs)  ┤
US-005 (docs: ROI)   ┼─→ US-009 (final docs)
US-007 (audit scripts)┤
US-008 (staleness)   ┘
```

## Non-Goals

- Rewriting the dashboard frontend framework
- Adding new dashboard tabs beyond staleness indicator
- Changing the SQLite schema
- External/cloud deployment
- Refactoring to Flask/FastAPI

## Success Metrics

- python3 -m pytest tests/ -v → 0 failures
- curl localhost:8766/api/health → 200
- launchctl list | grep claude → all kept daemons show PIDs with exit 0
- Grep for "12-tab", "15-tab", "124 tests", "4.5x ROI" → 0 matches
- All 17 tab screenshots present in assets/tabs/
- Dashboard shows "Last updated: X min ago" indicator
[/PRD]
