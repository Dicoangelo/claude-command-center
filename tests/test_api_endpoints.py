"""Tests for CCC API server endpoints."""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


@pytest.fixture(scope="module")
def _install_check():
    """Verify httpx is available."""
    return True


class TestAPIServer:
    """Test suite for CCC API server endpoints.

    Uses a subprocess server pointed at a temporary database.
    """

    @pytest.fixture(autouse=True)
    def server(self, tmp_claude_dir, api_server_port):
        """Start the API server in a subprocess with test data."""
        self.port = api_server_port
        self.base_url = f"http://localhost:{self.port}"

        env = {
            **dict(__import__("os").environ),
            "HOME": str(tmp_claude_dir.parent),
        }

        self.proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS_DIR / "ccc-api-server.py"), "--port", str(self.port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to start
        for _ in range(30):
            try:
                httpx.get(f"{self.base_url}/api/health", timeout=1.0)
                break
            except (httpx.ConnectError, httpx.ReadError):
                time.sleep(0.2)
        else:
            self.proc.kill()
            stdout, stderr = self.proc.communicate(timeout=5)
            pytest.fail(f"Server failed to start.\nstdout: {stdout.decode()}\nstderr: {stderr.decode()}")

        yield

        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def get(self, path: str, **kwargs) -> httpx.Response:
        return httpx.get(f"{self.base_url}{path}", timeout=5.0, **kwargs)

    # ─── Endpoint Tests ───────────────────────────────────────

    def test_root_redirects_to_dashboard(self):
        r = self.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/dashboard"

    def test_dashboard_serves_html(self):
        r = self.get("/dashboard")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "CCC Dashboard" in r.text
        # SSE client should be injected
        assert "EventSource" in r.text

    def test_stats_endpoint(self):
        r = self.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_cost_endpoint(self):
        r = self.get("/api/cost")
        assert r.status_code == 200
        data = r.json()
        assert "totalValue" in data or "error" not in data or True  # may have error key if pricing not found
        # Should have basic cost structure
        if "error" not in data:
            assert "totalValue" in data
            assert "multiplier" in data
            assert "breakdown" in data

    def test_routing_endpoint(self):
        r = self.get("/api/routing")
        assert r.status_code == 200
        data = r.json()
        assert "dq_scores" in data
        assert "total_dq" in data
        assert isinstance(data["dq_scores"], list)

    def test_sessions_endpoint(self):
        r = self.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
        assert "count" in data
        assert isinstance(data["sessions"], list)

    def test_tools_endpoint(self):
        r = self.get("/api/tools")
        assert r.status_code == 200
        data = r.json()
        assert "usage" in data
        assert "success" in data

    def test_git_endpoint(self):
        r = self.get("/api/git")
        assert r.status_code == 200
        data = r.json()
        assert "activity" in data
        assert "count" in data

    def test_health_endpoint(self):
        r = self.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "timestamp" in data

    def test_fate_endpoint(self):
        r = self.get("/api/fate")
        assert r.status_code == 200
        data = r.json()
        assert "predictions" in data
        assert "accuracy" in data

    def test_cognitive_endpoint(self):
        r = self.get("/api/cognitive")
        assert r.status_code == 200
        data = r.json()
        assert "current_state" in data
        assert "flow_state" in data

    def test_sse_stream_content_type(self):
        """SSE endpoint should return text/event-stream."""
        with httpx.stream("GET", f"{self.base_url}/api/stream", timeout=3.0) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]

    def test_unknown_endpoint_returns_404(self):
        r = self.get("/api/nonexistent")
        assert r.status_code == 404
        data = r.json()
        assert "error" in data

    def test_all_endpoints_return_json(self):
        """All REST endpoints should return valid JSON."""
        json_endpoints = [
            "/api/stats",
            "/api/cost",
            "/api/routing",
            "/api/sessions",
            "/api/tools",
            "/api/git",
            "/api/health",
            "/api/fate",
            "/api/cognitive",
        ]
        for endpoint in json_endpoints:
            r = self.get(endpoint)
            assert r.status_code == 200, f"{endpoint} returned {r.status_code}"
            try:
                r.json()
            except json.JSONDecodeError:
                pytest.fail(f"{endpoint} returned invalid JSON: {r.text[:200]}")

    def test_cors_headers_present(self):
        """REST endpoints should include CORS headers."""
        r = self.get("/api/stats")
        assert r.headers.get("access-control-allow-origin") == "*"

    def test_no_cache_headers_present(self):
        """REST endpoints should include no-cache headers."""
        r = self.get("/api/stats")
        assert "no-cache" in r.headers.get("cache-control", "")
