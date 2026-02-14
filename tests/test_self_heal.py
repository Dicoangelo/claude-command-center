"""Tests for the Self-Heal Engine (ccc-self-heal.py)."""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Load the module ─────────────────────────────────────────────────
SCRIPT = Path(__file__).parent.parent / "scripts" / "ccc-self-heal.py"
_spec = spec_from_file_location("ccc_self_heal", str(SCRIPT))
heal = module_from_spec(_spec)
_spec.loader.exec_module(heal)


# ── Tests: HealthCheck class ───────────────────────────────────────


class TestHealthCheck:
    def test_default_state(self):
        hc = heal.HealthCheck("test", "category")
        assert hc.status == "unknown"
        assert hc.can_fix is False
        assert hc.fix_action is None
        assert hc.details == {}

    def test_ok(self):
        hc = heal.HealthCheck("test", "category")
        result = hc.ok("all good")
        assert result is hc
        assert hc.status == "ok"
        assert hc.message == "all good"

    def test_warn_with_fix(self):
        hc = heal.HealthCheck("test", "category")
        hc.warn("needs attention", can_fix=True, fix_action="do_something")
        assert hc.status == "warn"
        assert hc.can_fix is True
        assert hc.fix_action == "do_something"

    def test_error_without_fix(self):
        hc = heal.HealthCheck("test", "category")
        hc.error("broken")
        assert hc.status == "error"
        assert hc.can_fix is False


# ── Tests: parse_timestamp ─────────────────────────────────────────


class TestParseTimestamp:
    def test_none_returns_none(self):
        assert heal.parse_timestamp(None) is None

    def test_invalid_string_returns_none(self):
        assert heal.parse_timestamp("not-a-date") is None

    def test_iso_format(self):
        result = heal.parse_timestamp("2026-02-12T10:00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 2

    def test_iso_with_z(self):
        result = heal.parse_timestamp("2026-02-12T10:00:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_unix_seconds(self):
        result = heal.parse_timestamp(1707700000)
        assert isinstance(result, datetime)

    def test_unix_milliseconds(self):
        result = heal.parse_timestamp(1707700000000)
        assert isinstance(result, datetime)

    def test_datetime_passthrough(self):
        dt = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        result = heal.parse_timestamp(dt)
        assert isinstance(result, datetime)
        assert result.year == 2026

    def test_string_number(self):
        result = heal.parse_timestamp("1707700000")
        assert isinstance(result, datetime)

    def test_naive_datetime_gets_tz(self):
        dt = datetime(2026, 1, 1)
        result = heal.parse_timestamp(dt)
        assert result.tzinfo is not None


# ── Tests: file_age_hours ──────────────────────────────────────────


class TestFileAgeHours:
    def test_nonexistent_file(self):
        age = heal.file_age_hours(Path("/nonexistent/file"))
        assert age == float("inf")

    def test_fresh_file(self, tmp_path):
        f = tmp_path / "fresh.txt"
        f.write_text("test")
        age = heal.file_age_hours(f)
        assert age < 0.1


# ── Tests: check_file_freshness ────────────────────────────────────


class TestCheckFileFreshness:
    def test_fresh_file(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text("{}")
        result = heal.check_file_freshness(f, 24, "test-file")
        assert result.status == "ok"

    def test_missing_file(self, tmp_path):
        result = heal.check_file_freshness(tmp_path / "nope", 24, "test-file")
        assert result.status == "error"
        assert result.can_fix is True
        assert result.fix_action == "regenerate"

    def test_stale_file(self, tmp_path):
        f = tmp_path / "old.json"
        f.write_text("{}")
        old_time = time.time() - 48 * 3600
        os.utime(f, (old_time, old_time))
        result = heal.check_file_freshness(f, 24, "test-file")
        assert result.status == "warn"
        assert result.can_fix is True


# ── Tests: check_json_updated_field ────────────────────────────────


class TestCheckJsonUpdatedField:
    def test_fresh_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"updated": datetime.now().isoformat()}))
        result = heal.check_json_updated_field(f, 24, "test-json")
        assert result.status == "ok"

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{invalid json")
        result = heal.check_json_updated_field(f, 24, "test-json")
        assert result.status == "error"

    def test_missing_file(self, tmp_path):
        result = heal.check_json_updated_field(tmp_path / "nope.json", 24, "test-json")
        assert result.status == "error"

    def test_no_timestamp_falls_back_to_mtime(self, tmp_path):
        f = tmp_path / "no-ts.json"
        f.write_text(json.dumps({"data": "value"}))
        result = heal.check_json_updated_field(f, 24, "test-json")
        assert result.status == "ok"

    def test_stale_timestamp(self, tmp_path):
        f = tmp_path / "old.json"
        f.write_text(json.dumps({"updated": "2020-01-01T00:00:00"}))
        result = heal.check_json_updated_field(f, 24, "test-json")
        assert result.status == "warn"


# ── Tests: check_jsonl_health ──────────────────────────────────────


class TestCheckJsonlHealth:
    def test_valid_jsonl(self, tmp_path):
        f = tmp_path / "good.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n')
        result = heal.check_jsonl_health(f, "test-jsonl")
        assert result.status == "ok"
        assert result.details["valid"] == 2
        assert result.details["invalid"] == 0

    def test_high_error_rate(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text('{"ok":1}\nnot json\nbad line\n')
        result = heal.check_jsonl_health(f, "test-jsonl")
        assert result.status == "warn"
        assert result.details["invalid"] == 2

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        result = heal.check_jsonl_health(f, "test-jsonl")
        assert result.status == "ok"

    def test_missing_file_is_ok(self, tmp_path):
        result = heal.check_jsonl_health(tmp_path / "nope.jsonl", "test-jsonl")
        assert result.status == "ok"


# ── Tests: check_stale_locks ──────────────────────────────────────


class TestCheckStaleLocks:
    def test_no_locks(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(heal, "CLAUDE_DIR", claude_dir)
        monkeypatch.setattr(heal, "HOME", tmp_path)
        result = heal.check_stale_locks()
        assert result.status == "ok"

    def test_stale_lock_detected(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        lock = claude_dir / ".session.lock"
        lock.write_text("locked")
        old_time = time.time() - 2 * 3600
        os.utime(lock, (old_time, old_time))
        monkeypatch.setattr(heal, "CLAUDE_DIR", claude_dir)
        monkeypatch.setattr(heal, "HOME", tmp_path)
        result = heal.check_stale_locks()
        assert result.status == "warn"
        assert result.can_fix is True

    def test_fresh_lock_is_ok(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        lock = claude_dir / ".session.lock"
        lock.write_text("locked")
        monkeypatch.setattr(heal, "CLAUDE_DIR", claude_dir)
        monkeypatch.setattr(heal, "HOME", tmp_path)
        result = heal.check_stale_locks()
        assert result.status == "ok"


# ── Tests: check_log_sizes ────────────────────────────────────────


class TestCheckLogSizes:
    def test_no_logs_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(heal, "LOGS_DIR", tmp_path / "logs")
        result = heal.check_log_sizes()
        assert result.status == "ok"

    def test_small_logs(self, tmp_path, monkeypatch):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "test.log").write_text("small log")
        monkeypatch.setattr(heal, "LOGS_DIR", logs)
        result = heal.check_log_sizes()
        assert result.status == "ok"


# ── Tests: check_daemon_loaded ─────────────────────────────────────


class TestCheckDaemonLoaded:
    def test_daemon_running(self, tmp_path, monkeypatch):
        plist = tmp_path / "com.claude.test.plist"
        plist.write_text("<plist></plist>")
        monkeypatch.setattr(heal, "LAUNCH_AGENTS", tmp_path)
        mock_result = MagicMock()
        mock_result.stdout = "12345\t0\tcom.claude.test\n"
        with patch("subprocess.run", return_value=mock_result):
            result = heal.check_daemon_loaded("com.claude.test")
        assert result.status == "ok"
        assert "12345" in result.message

    def test_daemon_not_loaded(self, tmp_path, monkeypatch):
        plist = tmp_path / "com.claude.missing.plist"
        plist.write_text("<plist></plist>")
        monkeypatch.setattr(heal, "LAUNCH_AGENTS", tmp_path)
        mock_result = MagicMock()
        mock_result.stdout = "other-daemon\n"
        with patch("subprocess.run", return_value=mock_result):
            result = heal.check_daemon_loaded("com.claude.missing")
        assert result.status == "error"
        assert result.can_fix is True
        assert result.fix_action == "load_daemon"

    def test_plist_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(heal, "LAUNCH_AGENTS", tmp_path)
        result = heal.check_daemon_loaded("com.claude.noplist")
        assert result.status == "warn"


# ── Tests: fix functions ──────────────────────────────────────────


class TestFixClearLocks:
    def test_clears_stale_locks(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        lock = claude_dir / ".session.lock"
        lock.write_text("locked")
        old_time = time.time() - 2 * 3600
        os.utime(lock, (old_time, old_time))
        monkeypatch.setattr(heal, "CLAUDE_DIR", claude_dir)
        success, msg = heal.fix_clear_locks()
        assert success is True
        assert "Cleared 1" in msg
        assert not lock.exists()

    def test_no_locks_to_clear(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(heal, "CLAUDE_DIR", claude_dir)
        success, msg = heal.fix_clear_locks()
        assert success is True
        assert "Cleared 0" in msg


class TestFixCleanJsonl:
    def test_removes_invalid_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"ok":1}\nbad line\n{"ok":2}\n')
        success, msg = heal.fix_clean_jsonl(f)
        assert success is True
        assert "Removed 1" in msg
        lines = [l for l in f.read_text().strip().split("\n") if l]
        assert len(lines) == 2
        assert (tmp_path / "data.jsonl.backup").exists()

    def test_missing_file(self):
        success, msg = heal.fix_clean_jsonl(Path("/nonexistent/file.jsonl"))
        assert success is False


class TestFixRotateLogs:
    def test_rotates_oversized(self, tmp_path, monkeypatch):
        logs = tmp_path / "logs"
        logs.mkdir()
        big_log = logs / "big.log"
        big_log.write_text("x\n" * 1000)
        monkeypatch.setattr(heal, "LOGS_DIR", logs)
        monkeypatch.setitem(heal.THRESHOLDS, "max_log_size_mb", 0.0001)
        success, msg = heal.fix_rotate_logs()
        assert success is True
        assert "Rotated 1" in msg
        assert (logs / "big.log.old").exists()
        # Rotated file should be smaller (last 10%)
        assert big_log.stat().st_size < (logs / "big.log.old").stat().st_size

    def test_no_logs_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(heal, "LOGS_DIR", tmp_path / "nonexistent")
        success, msg = heal.fix_rotate_logs()
        assert success is True


# ── Tests: SelfHealingEngine ──────────────────────────────────────


class TestSelfHealingEngine:
    def test_report_structure(self):
        engine = heal.SelfHealingEngine(auto_fix=False, verbose=False)
        hc = heal.HealthCheck("test", "unit")
        hc.ok("fine")
        engine.checks.append(hc)
        report = engine.report()
        for key in ("timestamp", "total_checks", "ok", "warnings", "errors",
                     "fixes_applied", "fixes_successful", "checks", "fixes"):
            assert key in report, f"Missing report key: {key}"

    def test_report_counts(self):
        engine = heal.SelfHealingEngine(auto_fix=False, verbose=False)
        engine.checks.append(heal.HealthCheck("a", "x").ok())
        engine.checks.append(heal.HealthCheck("b", "x").warn("w"))
        engine.checks.append(heal.HealthCheck("c", "x").error("e"))
        report = engine.report()
        assert report["ok"] == 1
        assert report["warnings"] == 1
        assert report["errors"] == 1
        assert report["total_checks"] == 3

    def test_fixes_tracked(self):
        engine = heal.SelfHealingEngine(auto_fix=True, verbose=False)
        engine.fixes_applied.append(("test-fix", True, "fixed"))
        engine.fixes_applied.append(("test-fail", False, "failed"))
        report = engine.report()
        assert report["fixes_applied"] == 2
        assert report["fixes_successful"] == 1

    def test_print_summary_no_crash(self, capsys):
        engine = heal.SelfHealingEngine(auto_fix=False, verbose=False)
        engine.checks.append(heal.HealthCheck("ok-check", "test").ok("good"))
        engine.checks.append(heal.HealthCheck("warn-check", "test").warn("bad", can_fix=True))
        engine.print_summary()
        output = capsys.readouterr().out
        assert "CCC Self-Healing Report" in output
        assert "ok-check" not in output or "warn-check" in output

    def test_apply_fixes_clear_locks(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        lock = claude_dir / ".session.lock"
        lock.write_text("stale")
        old_time = time.time() - 2 * 3600
        os.utime(lock, (old_time, old_time))
        monkeypatch.setattr(heal, "CLAUDE_DIR", claude_dir)

        engine = heal.SelfHealingEngine(auto_fix=True, verbose=False)
        # Manually add a fixable check
        hc = heal.HealthCheck("lock_files", "system")
        hc.warn("stale lock", can_fix=True, fix_action="clear_locks")
        engine.checks.append(hc)
        # Mock log_learning to avoid needing supermemory.db
        monkeypatch.setattr(heal, "log_learning", lambda *a, **kw: None)
        engine.apply_fixes()
        assert len(engine.fixes_applied) == 1
        assert engine.fixes_applied[0][1] is True  # success
        assert not lock.exists()


# ── Tests: evolve_from_patterns ───────────────────────────────────


class TestEvolveFromPatterns:
    def test_no_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(heal, "DATA_DIR", tmp_path)
        heal.evolve_from_patterns()
        output = capsys.readouterr().out
        assert "No recovery outcomes" in output

    def test_with_data(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(heal, "DATA_DIR", tmp_path)
        f = tmp_path / "recovery-outcomes.jsonl"
        f.write_text(
            '{"action":"clear_locks","success":true}\n'
            '{"action":"clear_locks","success":true}\n'
            '{"action":"reload","success":false}\n'
        )
        heal.evolve_from_patterns()
        output = capsys.readouterr().out
        assert "clear_locks" in output
        assert "100%" in output
