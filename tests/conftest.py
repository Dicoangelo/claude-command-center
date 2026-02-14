"""Shared fixtures for CCC test suite."""

import json
import sqlite3
from pathlib import Path

import pytest

# Add project paths for imports
PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CONFIG_DIR = PROJECT_ROOT / "config"


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with realistic test data."""
    db_path = tmp_path / "test-claude.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Create schema from schema.sql
    schema_path = CONFIG_DIR / "schema.sql"
    if schema_path.exists():
        conn.executescript(schema_path.read_text())
    else:
        # Minimal fallback schema
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date DATE PRIMARY KEY,
                opus_messages INTEGER DEFAULT 0, sonnet_messages INTEGER DEFAULT 0, haiku_messages INTEGER DEFAULT 0,
                opus_tokens_in INTEGER DEFAULT 0,
                opus_tokens_out INTEGER DEFAULT 0,
                opus_cache_read INTEGER DEFAULT 0,
                sonnet_tokens_in INTEGER DEFAULT 0,
                sonnet_tokens_out INTEGER DEFAULT 0,
                sonnet_cache_read INTEGER DEFAULT 0,
                haiku_tokens_in INTEGER DEFAULT 0,
                haiku_tokens_out INTEGER DEFAULT 0,
                haiku_cache_read INTEGER DEFAULT 0,
                session_count INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
                cost_estimate REAL DEFAULT 0, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, project_path TEXT, started_at DATETIME NOT NULL,
                ended_at DATETIME, model TEXT NOT NULL, message_count INTEGER DEFAULT 0,
                tool_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0, outcome TEXT, quality_score REAL,
                dq_score REAL, complexity REAL, cost_estimate REAL, metadata JSON,
                transcript_path TEXT, scanned_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS tool_usage (
                tool_name TEXT PRIMARY KEY, total_calls INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0, failure_count INTEGER DEFAULT 0,
                last_used DATETIME, avg_duration_ms REAL
            );
            CREATE TABLE IF NOT EXISTS routing_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME NOT NULL,
                query_hash TEXT, query_preview TEXT, complexity REAL,
                selected_model TEXT, dq_score REAL, dq_validity REAL,
                dq_specificity REAL, dq_correctness REAL, cost_estimate REAL,
                success INTEGER, feedback_at DATETIME
            );
        """)

    # Insert realistic test data
    conn.executemany(
        """INSERT OR IGNORE INTO daily_stats
           (date, opus_messages, sonnet_messages, haiku_messages,
            opus_tokens_in, opus_tokens_out, opus_cache_read,
            sonnet_tokens_in, sonnet_tokens_out, sonnet_cache_read,
            haiku_tokens_in, haiku_tokens_out, haiku_cache_read,
            session_count, tool_calls, cost_estimate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "2026-02-10",
                80,
                20,
                5,
                500000,
                150000,
                4000000,
                100000,
                30000,
                800000,
                10000,
                3000,
                50000,
                12,
                450,
                120.50,
            ),
            (
                "2026-02-11",
                95,
                15,
                3,
                600000,
                180000,
                5000000,
                80000,
                25000,
                700000,
                5000,
                2000,
                30000,
                15,
                520,
                145.30,
            ),
            (
                "2026-02-12",
                110,
                25,
                8,
                700000,
                200000,
                6000000,
                120000,
                35000,
                900000,
                15000,
                5000,
                60000,
                18,
                680,
                175.80,
            ),
        ],
    )

    conn.executemany(
        """INSERT OR IGNORE INTO sessions
           (id, project_path, started_at, ended_at, model, message_count, tool_count,
            input_tokens, output_tokens, cache_read_tokens, outcome, quality_score, cost_estimate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "sess-001",
                "/Users/test/project-a",
                "2026-02-12 10:00:00",
                "2026-02-12 11:30:00",
                "opus",
                85,
                120,
                500000,
                150000,
                4000000,
                "completed",
                0.85,
                55.0,
            ),
            (
                "sess-002",
                "/Users/test/project-b",
                "2026-02-12 14:00:00",
                "2026-02-12 15:00:00",
                "sonnet",
                40,
                60,
                200000,
                60000,
                1500000,
                "completed",
                0.72,
                18.0,
            ),
            (
                "sess-003",
                "/Users/test/project-a",
                "2026-02-12 16:00:00",
                None,
                "opus",
                20,
                30,
                100000,
                30000,
                800000,
                None,
                None,
                10.0,
            ),
        ],
    )

    conn.executemany(
        """INSERT OR IGNORE INTO tool_usage
           (tool_name, total_calls, success_count,
            failure_count, last_used, avg_duration_ms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            ("Read", 5000, 4980, 20, "2026-02-12 16:00:00", 45.2),
            ("Edit", 3000, 2950, 50, "2026-02-12 15:30:00", 120.5),
            ("Bash", 2000, 1900, 100, "2026-02-12 16:00:00", 350.0),
            ("Grep", 1500, 1500, 0, "2026-02-12 14:00:00", 80.3),
        ],
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def tmp_claude_dir(tmp_path, tmp_db):
    """Create a temporary ~/.claude-like directory structure with test data."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # Data directory
    data_dir = claude_dir / "data"
    data_dir.mkdir()

    # Symlink the test DB
    import shutil

    shutil.copy2(str(tmp_db), str(data_dir / "claude.db"))

    # Stats cache
    (claude_dir / "stats-cache.json").write_text(
        json.dumps({"sessions": 100, "messages": 5000, "tools": 2000, "tokens": 50000000})
    )

    # Dashboard directory with minimal HTML
    dash_dir = claude_dir / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "claude-command-center.html").write_text("<html><body><h1>CCC Dashboard</h1></body></html>")

    # Kernel directory
    kernel_dir = claude_dir / "kernel"
    kernel_dir.mkdir()
    (kernel_dir / "dq-scores.jsonl").write_text(
        '{"timestamp":1707700000,"dq_score":0.75,"model":"opus"}\n'
        '{"timestamp":1707700100,"dq_score":0.45,"model":"sonnet"}\n'
    )
    (kernel_dir / "cost-data.json").write_text("{}")

    # Cognitive OS
    cos_dir = kernel_dir / "cognitive-os"
    cos_dir.mkdir()
    (cos_dir / "current-state.json").write_text(json.dumps({"mode": "peak", "energy": 0.8}))
    (cos_dir / "flow-state.json").write_text(json.dumps({"score": 0.72, "state": "focused"}))
    (cos_dir / "weekly-energy.json").write_text(json.dumps({"mon": 0.7, "tue": 0.8}))
    (cos_dir / "fate-predictions.jsonl").write_text(
        '{"prediction":"success","correct":true}\n{"prediction":"failure","correct":false}\n'
    )

    # Data JSONL files
    (data_dir / "session-outcomes.jsonl").write_text('{"session_id":"s1","outcome":"completed","quality":4}\n')
    (data_dir / "tool-usage.jsonl").write_text('{"tool":"Read","count":100}\n')
    (data_dir / "tool-success.jsonl").write_text('{"tool":"Read","success":98,"failure":2}\n')
    (data_dir / "git-activity.jsonl").write_text('{"type":"commit","repo":"test","message":"fix bug"}\n')
    (data_dir / "routing-metrics.jsonl").write_text('{"model":"opus","accuracy":0.85}\n')
    (data_dir / "routing-feedback.jsonl").write_text('{"model":"opus","correct":true}\n')

    # Config directory
    config_dir = claude_dir / "config"
    config_dir.mkdir()

    return claude_dir


@pytest.fixture
def api_server_port():
    """Find a free port for the test server."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
