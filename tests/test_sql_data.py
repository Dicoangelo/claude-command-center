"""Tests for the SQL Data Exporter (ccc-sql-data.py)."""

import json
import sqlite3
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

# ── Mock pricing module before loading sql_data ─────────────────────
_pricing = ModuleType("pricing")
_pricing.MONTHLY_RATE_USD = 200.0


def _mock_cost(model, input_tokens, output_tokens, cache_reads=0):
    rates = {"opus": (5, 25, 0.5), "sonnet": (3, 15, 0.3), "haiku": (1, 5, 0.1)}
    r = rates.get(model, (5, 25, 0.5))
    return (input_tokens * r[0] + output_tokens * r[1] + cache_reads * r[2]) / 1_000_000


_pricing.get_model_cost = _mock_cost
sys.modules.setdefault("pricing", _pricing)

# ── Load the module ─────────────────────────────────────────────────
SCRIPT = Path(__file__).parent.parent / "scripts" / "ccc-sql-data.py"
_spec = spec_from_file_location("ccc_sql_data", str(SCRIPT))
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)

CONFIG_DIR = Path(__file__).parent.parent / "config"


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sql_db(tmp_db):
    """Extend tmp_db with additional data for sql_data tests."""
    conn = sqlite3.connect(str(tmp_db))

    # Update session outcomes to include 'success' / 'abandoned'
    conn.execute("UPDATE sessions SET outcome = 'success' WHERE id = 'sess-001'")
    conn.execute("UPDATE sessions SET outcome = 'abandoned' WHERE id = 'sess-002'")

    # Hourly activity
    conn.executemany(
        "INSERT OR IGNORE INTO hourly_activity (date, hour, session_count, message_count) VALUES (?, ?, ?, ?)",
        [
            ("2026-02-12", 10, 3, 50),
            ("2026-02-12", 14, 2, 30),
            ("2026-02-12", 16, 1, 20),
        ],
    )

    # Routing decisions
    conn.executemany(
        """INSERT INTO routing_decisions
           (timestamp, query_hash, query_preview, complexity, selected_model, dq_score,
            dq_validity, dq_specificity, dq_correctness)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("2026-02-12 10:00:00", "h1", "How to...", 0.3, "sonnet", 0.65, 0.7, 0.6, 0.6),
            ("2026-02-12 11:00:00", "h2", "Architect...", 0.8, "opus", 0.85, 0.9, 0.8, 0.85),
            ("2026-02-12 12:00:00", "h3", "What is...", 0.1, "haiku", 0.45, 0.5, 0.4, 0.45),
        ],
    )

    # Session outcome events
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO session_outcome_events
               (session_id, outcome, quality_score, complexity, model_used, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("sess-001", "success", 4.2, 0.7, "opus", "2026-02-12 11:30:00"),
                ("sess-002", "abandoned", 2.5, 0.3, "sonnet", "2026-02-12 15:00:00"),
            ],
        )
    except sqlite3.OperationalError:
        pass

    # Self-heal events
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO self_heal_events
               (timestamp, error_pattern, fix_applied, success, severity, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (1707700000, "git_username", "set_username", 1, "low", "wrong username"),
                (1707700100, "stale_lock", "clear_lock", 1, "medium", "lock file stale"),
                (1707700200, "corrupt_state", "restore_backup", 0, "high", "state corrupted"),
            ],
        )
    except sqlite3.OperationalError:
        pass

    # Recovery events
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO recovery_events
               (timestamp, error_type, recovery_strategy, success, attempts, error_details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (1707700300, "crash", "restart", 1, 1, "process crashed"),
                (1707700400, "timeout", "retry", 1, 2, "request timeout"),
            ],
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    return tmp_db


@pytest.fixture
def mod(sql_db, monkeypatch):
    """Module with DB_PATH pointed at the test database."""
    monkeypatch.setattr(_mod, "DB_PATH", sql_db)
    return _mod


@pytest.fixture
def empty_mod(tmp_path, monkeypatch):
    """Module with an empty database (schema only, no data)."""
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    schema = (CONFIG_DIR / "schema.sql").read_text()
    conn.executescript(schema)
    conn.close()
    monkeypatch.setattr(_mod, "DB_PATH", db_path)
    return _mod


# ── Tests: get_stats_data ──────────────────────────────────────────


class TestStatsData:
    def test_returns_required_keys(self, mod):
        data = mod.get_stats_data()
        for key in ("totalSessions", "totalMessages", "totalTools", "modelUsage",
                     "dailyActivity", "dailyModelTokens", "hourCounts", "longestSession"):
            assert key in data, f"Missing key: {key}"

    def test_totals_sum_correctly(self, mod):
        data = mod.get_stats_data()
        # (80+20+5) + (95+15+3) + (110+25+8) = 361
        assert data["totalMessages"] == 361
        assert data["totalSessions"] == 12 + 15 + 18

    def test_model_usage_breakdown(self, mod):
        data = mod.get_stats_data()
        usage = data["modelUsage"]
        assert usage["opus"]["messageCount"] == 80 + 95 + 110
        assert usage["sonnet"]["messageCount"] == 20 + 15 + 25
        assert usage["haiku"]["messageCount"] == 5 + 3 + 8

    def test_daily_activity_chronological(self, mod):
        data = mod.get_stats_data()
        dates = [d["date"] for d in data["dailyActivity"]]
        assert dates == sorted(dates)
        assert len(dates) == 3

    def test_longest_session(self, mod):
        data = mod.get_stats_data()
        assert data["longestSession"]["messageCount"] == 85

    def test_token_totals(self, mod):
        data = mod.get_stats_data()
        opus = data["modelUsage"]["opus"]
        assert opus["inputTokens"] == 500000 + 600000 + 700000
        assert opus["outputTokens"] == 150000 + 180000 + 200000


# ── Tests: get_subscription_data ───────────────────────────────────


class TestSubscriptionData:
    def test_returns_required_keys(self, mod):
        data = mod.get_subscription_data()
        for key in ("rate", "totalValue", "multiplier", "breakdown", "savings", "costPerMsg"):
            assert key in data, f"Missing key: {key}"

    def test_rate_matches_mock(self, mod):
        data = mod.get_subscription_data()
        assert data["rate"] == 200.0

    def test_breakdown_has_all_models(self, mod):
        data = mod.get_subscription_data()
        for model in ("opus", "sonnet", "haiku"):
            assert model in data["breakdown"]
            assert data["breakdown"][model] >= 0

    def test_total_value_positive(self, mod):
        data = mod.get_subscription_data()
        assert data["totalValue"] > 0

    def test_multiplier_calculation(self, mod):
        data = mod.get_subscription_data()
        assert data["multiplier"] > 0
        assert isinstance(data["multiplier"], float)


# ── Tests: get_session_outcomes_data ───────────────────────────────


class TestSessionOutcomesData:
    def test_returns_required_keys(self, mod):
        data = mod.get_session_outcomes_data()
        for key in ("sessions", "totals", "qualityDist", "modelOutcomes", "sizeDist", "daily"):
            assert key in data, f"Missing key: {key}"

    def test_totals_structure(self, mod):
        data = mod.get_session_outcomes_data()
        t = data["totals"]
        assert t["total"] == 3
        assert "success" in t
        assert "abandoned" in t
        assert "avgMessages" in t

    def test_sessions_have_required_fields(self, mod):
        data = mod.get_session_outcomes_data()
        assert len(data["sessions"]) > 0
        sess = data["sessions"][0]
        for key in ("session_id", "messages", "outcome", "model"):
            assert key in sess, f"Missing session key: {key}"

    def test_size_distribution(self, mod):
        data = mod.get_session_outcomes_data()
        assert isinstance(data["sizeDist"], dict)


# ── Tests: get_routing_data ────────────────────────────────────────


class TestRoutingData:
    def test_returns_required_keys(self, mod):
        data = mod.get_routing_data()
        for key in ("totalQueries", "avgDqScore", "modelDistribution", "dailyTrend",
                     "dqComponents", "complexityByModel"):
            assert key in data, f"Missing key: {key}"

    def test_total_queries(self, mod):
        data = mod.get_routing_data()
        assert data["totalQueries"] == 3

    def test_avg_dq_score(self, mod):
        data = mod.get_routing_data()
        expected = round((0.65 + 0.85 + 0.45) / 3, 3)
        assert data["avgDqScore"] == expected

    def test_model_distribution_sums_to_one(self, mod):
        data = mod.get_routing_data()
        dist = data["modelDistribution"]
        total = dist["haiku"] + dist["sonnet"] + dist["opus"]
        assert abs(total - 1.0) < 0.01

    def test_dq_components(self, mod):
        data = mod.get_routing_data()
        comp = data["dqComponents"]
        assert comp["validity"] > 0
        assert comp["specificity"] > 0
        assert comp["correctness"] > 0

    def test_complexity_by_model(self, mod):
        data = mod.get_routing_data()
        assert isinstance(data["complexityByModel"], list)
        assert len(data["complexityByModel"]) == 3


# ── Tests: get_recovery_data ──────────────────────────────────────


class TestRecoveryData:
    def test_returns_required_keys(self, mod):
        data = mod.get_recovery_data()
        for key in ("stats", "categories", "outcomes", "matrix"):
            assert key in data, f"Missing key: {key}"

    def test_stats_structure(self, mod):
        data = mod.get_recovery_data()
        s = data["stats"]
        for key in ("total", "autoFix", "autoFixRate", "successRate"):
            assert key in s, f"Missing stats key: {key}"

    def test_total_events(self, mod):
        data = mod.get_recovery_data()
        assert data["stats"]["total"] == 5  # 3 self_heal + 2 recovery

    def test_categories_populated(self, mod):
        data = mod.get_recovery_data()
        assert len(data["categories"]) > 0

    def test_matrix_has_entries(self, mod):
        data = mod.get_recovery_data()
        assert len(data["matrix"]) == 7  # Hardcoded matrix


# ── Tests: empty database ─────────────────────────────────────────


class TestEmptyDatabase:
    def test_stats_empty(self, empty_mod):
        data = empty_mod.get_stats_data()
        assert data["totalSessions"] == 0
        assert data["totalMessages"] == 0
        assert data["totalTools"] == 0

    def test_subscription_empty(self, empty_mod):
        data = empty_mod.get_subscription_data()
        assert data["totalValue"] == 0

    def test_outcomes_empty(self, empty_mod):
        data = empty_mod.get_session_outcomes_data()
        assert data["totals"]["total"] == 0
        assert data["sessions"] == []

    def test_routing_empty(self, empty_mod):
        data = empty_mod.get_routing_data()
        assert data["totalQueries"] == 0
        assert data["avgDqScore"] == 0

    def test_recovery_empty(self, empty_mod):
        data = empty_mod.get_recovery_data()
        assert data["stats"]["total"] == 0


# ── Tests: CLI main() ─────────────────────────────────────────────


class TestMain:
    def test_stats_mode(self, mod, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ccc-sql-data.py", "stats"])
        mod.main()
        data = json.loads(capsys.readouterr().out)
        assert "totalSessions" in data

    def test_all_mode(self, mod, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ccc-sql-data.py", "all"])
        mod.main()
        data = json.loads(capsys.readouterr().out)
        for key in ("stats", "subscription", "outcomes", "routing", "recovery"):
            assert key in data

    def test_unknown_mode_exits(self, mod, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ccc-sql-data.py", "bogus"])
        with pytest.raises(SystemExit, match="1"):
            mod.main()

    def test_no_args_exits(self, mod, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ccc-sql-data.py"])
        with pytest.raises(SystemExit, match="1"):
            mod.main()
