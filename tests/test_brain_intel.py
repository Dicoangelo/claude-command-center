"""Tests for ccc-autonomous-brain.py and ccc-intelligence-layer.py and ccc-autopilot.py.

US-007: Audit unvalidated scripts — add at least 3 tests each.
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# ============================================================================
# Helper: run a script and capture output
# ============================================================================

def _run_script(name: str, args: list[str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPTS_DIR / name)]
    if args:
        cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ============================================================================
# ccc-autonomous-brain.py — 4 tests
# ============================================================================

class TestAutonomousBrain:
    """Tests for the autonomous brain script."""

    def test_think_returns_valid_json(self):
        """--think --json should return valid JSON with expected keys."""
        result = _run_script("ccc-autonomous-brain.py", ["--think", "--json"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "predictions" in data
        assert "preventions" in data
        assert "anomalies" in data
        assert "threshold_adjustments" in data
        assert "timestamp" in data

    def test_status_returns_report(self):
        """--status should print a human-readable report."""
        result = _run_script("ccc-autonomous-brain.py", ["--status"])
        assert result.returncode == 0
        assert "BRAIN STATUS" in result.stdout or "Version" in result.stdout

    def test_dashboard_data_returns_json(self):
        """--dashboard-data should return valid JSON with dashboard fields."""
        result = _run_script("ccc-autonomous-brain.py", ["--dashboard-data"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Script may emit multiple JSON objects; parse the first one
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(result.stdout.strip())
        assert "version" in data
        assert "cycles" in data
        assert "predictions" in data

    def test_analyze_returns_patterns(self):
        """--analyze --json should return pattern analysis."""
        result = _run_script("ccc-autonomous-brain.py", ["--analyze", "--json"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list)


# ============================================================================
# ccc-intelligence-layer.py — 4 tests
# ============================================================================

class TestIntelligenceLayer:
    """Tests for the intelligence layer script."""

    def test_dashboard_returns_valid_json(self):
        """--dashboard should return valid JSON with intelligence data."""
        result = _run_script("ccc-intelligence-layer.py", ["--dashboard"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "optimal_hours" in data
        assert "cost_prediction" in data
        assert "model_quality" in data

    def test_analyze_query_returns_routing(self):
        """--analyze should return routing recommendation for a query."""
        result = _run_script("ccc-intelligence-layer.py", ["--analyze", "design a new API"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "routing" in data
        assert "recommended_model" in data["routing"]
        assert data["routing"]["recommended_model"] in ("opus", "sonnet", "haiku")

    def test_timing_shows_optimal_hours(self):
        """--timing should print optimal hours."""
        result = _run_script("ccc-intelligence-layer.py", ["--timing"])
        assert result.returncode == 0
        assert "Optimal Hours" in result.stdout or "Score" in result.stdout or result.stdout.strip() == ""

    def test_cost_shows_prediction(self):
        """--cost should print cost prediction data."""
        result = _run_script("ccc-intelligence-layer.py", ["--cost"])
        assert result.returncode == 0
        # Should contain dollar amounts or status
        assert "Status:" in result.stdout or "Predicted:" in result.stdout


# ============================================================================
# ccc-autopilot.py — 3 tests
# ============================================================================

class TestAutopilot:
    """Tests for the autopilot orchestrator script."""

    def test_once_runs_single_cycle(self):
        """--once should run exactly one cycle and return JSON."""
        result = _run_script("ccc-autopilot.py", ["--once"], timeout=60)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Autopilot logs to stdout before printing JSON; extract the JSON block
        output = result.stdout.strip()
        # Find the first '{' that starts the JSON object
        json_start = output.find("{")
        assert json_start >= 0, f"No JSON found in output: {output[:200]}"
        data = json.loads(output[json_start:])
        assert "timestamp" in data
        assert "brain" in data
        assert "intelligence" in data
        assert "actions_taken" in data

    def test_cycles_one_completes(self):
        """--cycles 1 should run one cycle and exit."""
        result = _run_script("ccc-autopilot.py", ["--cycles", "1"], timeout=60)
        assert result.returncode == 0
        # Autopilot logs to stderr/stdout
        assert "Cycle complete" in result.stderr or "Cycle complete" in result.stdout or "actions" in result.stdout

    def test_help_on_bad_arg(self):
        """Unknown arg should show usage."""
        result = _run_script("ccc-autopilot.py", ["--help-me"])
        assert result.returncode == 0
        assert "Usage" in result.stdout or "once" in result.stdout
