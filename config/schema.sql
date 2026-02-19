-- Claude Command Center Database Schema
-- SQLite 3 | WAL mode for concurrent reads
-- Source of truth: ~/.claude/data/claude.db

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- CORE TABLES
-- ============================================================

-- Session tracking with tokens, quality scores, outcomes
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_path TEXT,
    started_at DATETIME NOT NULL,
    ended_at DATETIME,
    model TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    tool_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    outcome TEXT,
    quality_score REAL,
    dq_score REAL,
    complexity REAL,
    cost_estimate REAL,
    metadata JSON,
    transcript_path TEXT,
    scanned_at DATETIME
);

-- Per-day aggregated metrics broken out by model
CREATE TABLE IF NOT EXISTS daily_stats (
    date DATE PRIMARY KEY,
    opus_messages INTEGER DEFAULT 0,
    sonnet_messages INTEGER DEFAULT 0,
    haiku_messages INTEGER DEFAULT 0,
    opus_tokens_in INTEGER DEFAULT 0,
    opus_tokens_out INTEGER DEFAULT 0,
    opus_cache_read INTEGER DEFAULT 0,
    sonnet_tokens_in INTEGER DEFAULT 0,
    sonnet_tokens_out INTEGER DEFAULT 0,
    sonnet_cache_read INTEGER DEFAULT 0,
    haiku_tokens_in INTEGER DEFAULT 0,
    haiku_tokens_out INTEGER DEFAULT 0,
    haiku_cache_read INTEGER DEFAULT 0,
    session_count INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    cost_estimate REAL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Model selection decisions with DQ scoring
CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    query_hash TEXT,
    query_preview TEXT,
    complexity REAL,
    selected_model TEXT,
    dq_score REAL,
    dq_validity REAL,
    dq_specificity REAL,
    dq_correctness REAL,
    cost_estimate REAL,
    success INTEGER,
    feedback_at DATETIME
);

-- Tool success/failure aggregate statistics
CREATE TABLE IF NOT EXISTS tool_usage (
    tool_name TEXT PRIMARY KEY,
    total_calls INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_used DATETIME,
    avg_duration_ms REAL
);

-- Time-of-day activity tracking
CREATE TABLE IF NOT EXISTS hourly_activity (
    date DATE,
    hour INTEGER,
    session_count INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY (date, hour)
);

-- Project-level summaries
CREATE TABLE IF NOT EXISTS projects (
    path TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    icon TEXT,
    session_count INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    last_active DATETIME,
    git_commits INTEGER DEFAULT 0,
    files_modified INTEGER DEFAULT 0
);

-- Monthly cost and ROI tracking
CREATE TABLE IF NOT EXISTS subscription_periods (
    period TEXT PRIMARY KEY,
    monthly_rate REAL,
    currency TEXT DEFAULT 'USD',
    total_messages INTEGER DEFAULT 0,
    total_sessions INTEGER DEFAULT 0,
    api_equivalent REAL DEFAULT 0,
    cache_savings REAL DEFAULT 0,
    roi_multiplier REAL DEFAULT 0
);

-- Key-value configuration
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- EVENT TABLES
-- ============================================================

-- Individual tool execution with timing/errors
CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    success INTEGER NOT NULL,
    duration_ms INTEGER,
    error_message TEXT,
    context TEXT
);

-- General activity log
CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT,
    session_id TEXT
);

-- Detailed routing decisions with reasoning
CREATE TABLE IF NOT EXISTS routing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    query_hash TEXT,
    complexity REAL,
    dq_score REAL,
    chosen_model TEXT,
    reasoning TEXT,
    feedback TEXT
);

-- Session completion data
CREATE TABLE IF NOT EXISTS session_outcome_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    session_id TEXT,
    outcome TEXT,
    quality_score REAL,
    complexity REAL,
    model_used TEXT,
    cost REAL,
    message_count INTEGER
);

-- Command execution tracking
CREATE TABLE IF NOT EXISTS command_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    command TEXT NOT NULL,
    args TEXT,
    success INTEGER,
    execution_time_ms INTEGER
);

-- Routing accuracy feedback
CREATE TABLE IF NOT EXISTS routing_metrics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    query_id TEXT,
    predicted_model TEXT,
    actual_model TEXT,
    dq_score REAL,
    complexity REAL,
    accuracy INTEGER,
    cost_saved REAL,
    reasoning TEXT,
    query_text TEXT
);

-- Git operations (commit, push, PR, branch, merge)
CREATE TABLE IF NOT EXISTS git_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    repo TEXT,
    branch TEXT,
    commit_hash TEXT,
    message TEXT,
    files_changed INTEGER,
    additions INTEGER,
    deletions INTEGER,
    author TEXT
);

-- Auto-recovery attempts with severity
CREATE TABLE IF NOT EXISTS self_heal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    error_pattern TEXT NOT NULL,
    fix_applied TEXT,
    success INTEGER NOT NULL,
    execution_time_ms INTEGER,
    error_message TEXT,
    context TEXT,
    severity TEXT
);

-- Error recovery strategies and outcomes
CREATE TABLE IF NOT EXISTS recovery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    error_type TEXT NOT NULL,
    recovery_strategy TEXT,
    success INTEGER NOT NULL,
    attempts INTEGER DEFAULT 1,
    time_to_recover_ms INTEGER,
    error_details TEXT,
    recovery_method TEXT
);

-- Multi-agent coordination (spawn, complete, lock, unlock)
CREATE TABLE IF NOT EXISTS coordinator_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    agent_id TEXT,
    action TEXT NOT NULL,
    strategy TEXT,
    file_path TEXT,
    result TEXT,
    duration_ms INTEGER,
    exit_code INTEGER
);

-- Domain-aware routing decisions
CREATE TABLE IF NOT EXISTS expertise_routing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    domain TEXT NOT NULL,
    expertise_level REAL,
    query_complexity REAL,
    chosen_model TEXT,
    reasoning TEXT,
    query_hash TEXT
);

-- Permission prompt / autonomy interruption events
CREATE TABLE IF NOT EXISTS autonomy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    event_type TEXT NOT NULL,       -- 'permission_prompt', 'permission_granted', 'permission_denied'
    session_id TEXT,
    tool_name TEXT,
    context TEXT
);

-- Pre-computed autonomous execution streaks
CREATE TABLE IF NOT EXISTS autonomy_streaks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL,
    tool_count INTEGER NOT NULL,
    avg_gap_seconds REAL,
    projects TEXT,                  -- JSON array of project paths
    top_tools TEXT,                 -- JSON: {"Edit": 701, "Bash": 534, ...}
    session_ids TEXT                -- JSON array of overlapping session IDs
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_model ON sessions(model);

CREATE INDEX IF NOT EXISTS idx_routing_timestamp ON routing_decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_routing_model ON routing_decisions(selected_model);

CREATE INDEX IF NOT EXISTS idx_tool_events_timestamp ON tool_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_events_tool_name ON tool_events(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_events_success ON tool_events(success);

CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity_events(event_type);
CREATE INDEX IF NOT EXISTS idx_activity_session ON activity_events(session_id);

CREATE INDEX IF NOT EXISTS idx_outcome_timestamp ON session_outcome_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_outcome_session ON session_outcome_events(session_id);

CREATE INDEX IF NOT EXISTS idx_command_timestamp ON command_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_command_name ON command_events(command);

CREATE INDEX IF NOT EXISTS idx_routing_metrics_timestamp ON routing_metrics_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_routing_metrics_accuracy ON routing_metrics_events(accuracy);
CREATE INDEX IF NOT EXISTS idx_routing_metrics_model ON routing_metrics_events(predicted_model);

CREATE INDEX IF NOT EXISTS idx_git_timestamp ON git_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_git_type ON git_events(event_type);
CREATE INDEX IF NOT EXISTS idx_git_repo ON git_events(repo);
CREATE INDEX IF NOT EXISTS idx_git_branch ON git_events(branch);

CREATE INDEX IF NOT EXISTS idx_self_heal_timestamp ON self_heal_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_self_heal_pattern ON self_heal_events(error_pattern);
CREATE INDEX IF NOT EXISTS idx_self_heal_success ON self_heal_events(success);
CREATE INDEX IF NOT EXISTS idx_self_heal_severity ON self_heal_events(severity);

CREATE INDEX IF NOT EXISTS idx_recovery_timestamp ON recovery_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_recovery_type ON recovery_events(error_type);
CREATE INDEX IF NOT EXISTS idx_recovery_success ON recovery_events(success);
CREATE INDEX IF NOT EXISTS idx_recovery_strategy ON recovery_events(recovery_strategy);

CREATE INDEX IF NOT EXISTS idx_coordinator_timestamp ON coordinator_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_coordinator_agent ON coordinator_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_coordinator_action ON coordinator_events(action);

CREATE INDEX IF NOT EXISTS idx_expertise_timestamp ON expertise_routing_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_expertise_domain ON expertise_routing_events(domain);
CREATE INDEX IF NOT EXISTS idx_expertise_model ON expertise_routing_events(chosen_model);

CREATE INDEX IF NOT EXISTS idx_autonomy_timestamp ON autonomy_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_autonomy_session ON autonomy_events(session_id);
CREATE INDEX IF NOT EXISTS idx_autonomy_event_type ON autonomy_events(event_type);

CREATE INDEX IF NOT EXISTS idx_streaks_start ON autonomy_streaks(start_ts);
CREATE INDEX IF NOT EXISTS idx_streaks_duration ON autonomy_streaks(duration_seconds);

-- ============================================================
-- VIEWS
-- ============================================================

CREATE VIEW IF NOT EXISTS v_daily_costs AS
SELECT
    date,
    (opus_tokens_in * 5.0 / 1000000) + (opus_tokens_out * 25.0 / 1000000) +
    (sonnet_tokens_in * 3.0 / 1000000) + (sonnet_tokens_out * 15.0 / 1000000) +
    (haiku_tokens_in * 0.8 / 1000000) + (haiku_tokens_out * 4.0 / 1000000) as api_cost,
    (opus_cache_read + sonnet_cache_read + haiku_cache_read) * 0.5 / 1000000 as cache_savings,
    opus_messages + sonnet_messages + haiku_messages as total_messages,
    session_count
FROM daily_stats;

CREATE VIEW IF NOT EXISTS v_model_distribution AS
SELECT
    SUM(opus_messages) as opus_total,
    SUM(sonnet_messages) as sonnet_total,
    SUM(haiku_messages) as haiku_total,
    SUM(opus_messages + sonnet_messages + haiku_messages) as grand_total,
    ROUND(100.0 * SUM(opus_messages) / SUM(opus_messages + sonnet_messages + haiku_messages), 1) as opus_pct,
    ROUND(100.0 * SUM(sonnet_messages) / SUM(opus_messages + sonnet_messages + haiku_messages), 1) as sonnet_pct,
    ROUND(100.0 * SUM(haiku_messages) / SUM(opus_messages + sonnet_messages + haiku_messages), 1) as haiku_pct
FROM daily_stats;

CREATE VIEW IF NOT EXISTS v_recent_sessions AS
SELECT
    id, project_path, started_at, model, message_count, outcome,
    quality_score, cost_estimate,
    ROUND((julianday(ended_at) - julianday(started_at)) * 24 * 60, 1) as duration_minutes
FROM sessions
ORDER BY started_at DESC
LIMIT 100;

CREATE VIEW IF NOT EXISTS v_routing_accuracy AS
SELECT
    selected_model,
    COUNT(*) as total_decisions,
    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
    ROUND(AVG(dq_score), 3) as avg_dq_score,
    ROUND(100.0 * SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN success IS NOT NULL THEN 1 ELSE 0 END), 0), 1) as accuracy_pct
FROM routing_decisions
GROUP BY selected_model;

CREATE VIEW IF NOT EXISTS v_token_totals AS
SELECT
    SUM(opus_cache_read + sonnet_cache_read + haiku_cache_read) as total_cache_read,
    SUM(opus_tokens_in + sonnet_tokens_in + haiku_tokens_in) as total_input,
    CASE WHEN SUM(opus_cache_read + sonnet_cache_read + haiku_cache_read +
                  opus_tokens_in + sonnet_tokens_in + haiku_tokens_in +
                  opus_tokens_out + sonnet_tokens_out + haiku_tokens_out) > 0
         THEN ROUND(100.0 * SUM(opus_cache_read + sonnet_cache_read + haiku_cache_read) /
              SUM(opus_cache_read + sonnet_cache_read + haiku_cache_read +
                  opus_tokens_in + sonnet_tokens_in + haiku_tokens_in +
                  opus_tokens_out + sonnet_tokens_out + haiku_tokens_out), 1)
         ELSE 0 END as cache_efficiency
FROM daily_stats;

CREATE VIEW IF NOT EXISTS v_subscription_summary AS
SELECT
    SUM(opus_messages) + SUM(sonnet_messages) + SUM(haiku_messages) as total_messages,
    SUM(session_count) as total_sessions,
    SUM(cost_estimate) as total_value,
    ROUND(SUM(cost_estimate) / (MAX(1, (julianday('now') - julianday(MIN(date))) / 30.0) * 200.0), 1) as roi_multiplier
FROM daily_stats;
