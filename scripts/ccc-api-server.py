#!/usr/bin/env python3
"""
CCC Live API Server - Real-time Command Center with SSE streaming

Endpoints:
- GET  /                    - Redirect to /dashboard
- GET  /dashboard           - Serve live dashboard HTML
- GET  /api/stream          - SSE stream (real-time updates every 3s)
- GET  /api/stats           - Session stats and usage data
- GET  /api/cost            - Cost data and savings
- GET  /api/routing         - Routing metrics and DQ scores
- GET  /api/sessions        - Session outcomes
- GET  /api/tools           - Tool usage statistics
- GET  /api/git             - Git activity
- GET  /api/health          - System health check
- GET  /api/fate            - Fate predictions
- GET  /api/cognitive       - Cognitive OS state
- GET  /api/memory/stats    - Memory store statistics
- POST /api/memory/query    - Query across all memory stores

Run: python3 ccc-api-server.py [--port 8766]
"""

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import sys
import sqlite3
import threading
import time
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
DB_PATH = CLAUDE_DIR / "data" / "claude.db"
DASHBOARD_HTML = CLAUDE_DIR / "dashboard" / "claude-command-center.html"
PORT = 8766

# Import pricing config
sys.path.insert(0, str(CLAUDE_DIR / "config"))
try:
    from pricing import get_model_cost, MONTHLY_RATE_USD
except ImportError:
    get_model_cost = lambda m, i, o, c=0: 0.0
    MONTHLY_RATE_USD = 200

# Import memory query engine
sys.path.insert(0, str(CLAUDE_DIR / "scripts"))
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "memory_query_engine",
        str(CLAUDE_DIR / "scripts/memory-query-engine.py")
    )
    memory_engine = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(memory_engine)
    MEMORY_AVAILABLE = True
except Exception:
    memory_engine = None
    MEMORY_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════
# SSE DATA STREAMER
# ═══════════════════════════════════════════════════════════════

class DataStreamer:
    """Polls SQLite every 3s, detects changes, broadcasts to SSE clients."""

    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()
        self.last_hash = {}
        self.running = False

    def add_client(self, wfile):
        with self.lock:
            self.clients.append(wfile)

    def remove_client(self, wfile):
        with self.lock:
            self.clients = [c for c in self.clients if c is not wfile]

    def broadcast(self, event_type, data):
        payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        encoded = payload.encode('utf-8')
        dead = []
        with self.lock:
            for client in self.clients:
                try:
                    client.write(encoded)
                    client.flush()
                except Exception:
                    dead.append(client)
            for d in dead:
                self.clients = [c for c in self.clients if c is not d]

    def _get_db(self):
        conn = sqlite3.connect(str(DB_PATH), timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    def _poll_stats(self):
        """Get current stats snapshot from SQLite."""
        try:
            conn = self._get_db()
            row = conn.execute("""
                SELECT
                    SUM(opus_messages + sonnet_messages + haiku_messages) as total_messages,
                    SUM(session_count) as total_sessions,
                    SUM(tool_calls) as total_tools
                FROM daily_stats
            """).fetchone()

            trow = conn.execute("""
                SELECT
                    SUM(opus_tokens_in) as opus_in, SUM(opus_tokens_out) as opus_out,
                    SUM(opus_cache_read) as opus_cache,
                    SUM(sonnet_tokens_in) as sonnet_in, SUM(sonnet_tokens_out) as sonnet_out,
                    SUM(haiku_tokens_in) as haiku_in, SUM(haiku_tokens_out) as haiku_out
                FROM daily_stats
            """).fetchone()

            # Today's activity
            today = datetime.now().strftime('%Y-%m-%d')
            today_row = conn.execute("""
                SELECT opus_messages + sonnet_messages + haiku_messages as messages,
                       session_count as sessions, tool_calls as tools,
                       opus_tokens_in + opus_tokens_out + opus_cache_read as tokens
                FROM daily_stats WHERE date = ?
            """, (today,)).fetchone()

            # Active sessions (last 5 minutes)
            five_min_ago = datetime.now().timestamp() - 300
            active = conn.execute("""
                SELECT COUNT(*) as c FROM sessions
                WHERE ended_at IS NULL OR ended_at > datetime('now', '-5 minutes')
            """).fetchone()

            # Latest session
            latest = conn.execute("""
                SELECT id, model, message_count, tool_count,
                       started_at, outcome
                FROM sessions ORDER BY started_at DESC LIMIT 1
            """).fetchone()

            conn.close()

            return {
                "totalSessions": row['total_sessions'] or 0,
                "totalMessages": row['total_messages'] or 0,
                "totalTools": row['total_tools'] or 0,
                "tokens": {
                    "opus_in": trow['opus_in'] or 0,
                    "opus_out": trow['opus_out'] or 0,
                    "opus_cache": trow['opus_cache'] or 0,
                },
                "today": {
                    "messages": today_row['messages'] if today_row else 0,
                    "sessions": today_row['sessions'] if today_row else 0,
                    "tools": today_row['tools'] if today_row else 0,
                    "tokens": today_row['tokens'] if today_row else 0,
                } if today_row else {"messages": 0, "sessions": 0, "tools": 0, "tokens": 0},
                "activeSessions": active['c'] if active else 0,
                "latestSession": {
                    "id": latest['id'][:12] if latest else None,
                    "model": latest['model'] if latest else None,
                    "messages": latest['message_count'] if latest else 0,
                    "tools": latest['tool_count'] if latest else 0,
                    "outcome": latest['outcome'] if latest else None,
                } if latest else None,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e), "timestamp": datetime.now().isoformat()}

    def _poll_health(self):
        """Quick health check."""
        try:
            import subprocess
            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
            daemons = len([l for l in result.stdout.split('\n') if 'com.claude' in l])
        except Exception:
            daemons = 0

        return {
            "daemons": daemons,
            "timestamp": datetime.now().isoformat(),
        }

    def _data_changed(self, key, data):
        """Check if data changed since last poll."""
        h = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if self.last_hash.get(key) != h:
            self.last_hash[key] = h
            return True
        return False

    def start(self):
        """Start the background polling thread."""
        if self.running:
            return
        self.running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        """Main polling loop."""
        health_counter = 0
        while self.running:
            try:
                with self.lock:
                    has_clients = len(self.clients) > 0

                if has_clients:
                    # Always send stats
                    stats = self._poll_stats()
                    if self._data_changed('stats', stats):
                        self.broadcast('stats', stats)

                    # Health every 15 seconds (every 5th poll)
                    health_counter += 1
                    if health_counter >= 5:
                        health_counter = 0
                        health = self._poll_health()
                        if self._data_changed('health', health):
                            self.broadcast('health', health)

                    # Heartbeat to keep connection alive
                    self.broadcast('heartbeat', {"t": int(time.time())})
            except Exception:
                pass

            time.sleep(3)

# Global streamer instance
streamer = DataStreamer()

# ═══════════════════════════════════════════════════════════════
# LIVE SSE CLIENT JAVASCRIPT (injected into dashboard HTML)
# ═══════════════════════════════════════════════════════════════

SSE_CLIENT_JS = """
<script>
// ═══════════════════════════════════════════════════════════════
// CCC LIVE — SSE Real-Time Layer
// ═══════════════════════════════════════════════════════════════
(function() {
  'use strict';

  const LIVE_CONFIG = { retryMs: 3000, maxRetries: 50 };
  let retries = 0;
  let connected = false;
  let lastUpdate = null;

  // --- Connection Status Indicator ---
  function createStatusIndicator() {
    const existing = document.getElementById('live-status');
    if (existing) existing.remove();

    const indicator = document.createElement('div');
    indicator.id = 'live-status';
    indicator.style.cssText = 'position:fixed;top:12px;right:16px;display:flex;align-items:center;gap:6px;padding:4px 12px;font-size:0.75rem;cursor:default;z-index:9999;background:rgba(10,12,20,0.85);border:1px solid rgba(0,255,136,0.15);border-radius:20px;backdrop-filter:blur(8px);';
    indicator.innerHTML = `
      <span id="live-dot" style="width:8px;height:8px;border-radius:50%;background:#ff4d6a;display:inline-block;transition:background 0.3s;"></span>
      <span id="live-label" style="color:rgba(255,255,255,0.5);font-family:JetBrains Mono,monospace;">CONNECTING</span>
      <span id="live-ts" style="color:rgba(255,255,255,0.25);font-family:JetBrains Mono,monospace;font-size:0.65rem;margin-left:4px;"></span>
    `;
    document.body.appendChild(indicator);
  }

  function setStatus(state) {
    const dot = document.getElementById('live-dot');
    const label = document.getElementById('live-label');
    if (!dot || !label) return;

    const states = {
      connected: { color: '#00ff88', text: 'LIVE', pulse: true },
      connecting: { color: '#ffcc00', text: 'CONNECTING', pulse: true },
      disconnected: { color: '#ff4d6a', text: 'OFFLINE', pulse: false },
    };
    const s = states[state] || states.disconnected;
    dot.style.background = s.color;
    dot.style.boxShadow = s.pulse ? `0 0 8px ${s.color}` : 'none';
    label.textContent = s.text;
    connected = state === 'connected';
  }

  // --- Format Helpers ---
  function fmtNum(n) {
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
  }

  // --- DOM Updaters ---
  function updateStats(data) {
    // Top stat cards (they use specific structure)
    const statValues = document.querySelectorAll('#overview .stat-value, #statsRow .stat-value');

    // Find and update by matching labels
    // Overview tab uses .label/.value, other tabs use .stat-label/.stat-value
    document.querySelectorAll('.stat-card').forEach(card => {
      const label = card.querySelector('.label') || card.querySelector('.stat-label');
      const value = card.querySelector('.value') || card.querySelector('.stat-value');
      if (!label || !value) return;

      const l = label.textContent.trim().toUpperCase();
      if (l === 'SESSIONS') value.textContent = fmtNum(data.totalSessions);
      else if (l === 'MESSAGES') value.textContent = fmtNum(data.totalMessages);
      else if (l === 'TOOL CALLS') value.textContent = fmtNum(data.totalTools);
      else if (l === 'AVG/DAY') value.textContent = fmtNum(Math.round(data.totalMessages / 35));
    });

    // Token breakdown - uses .token-item > .value/.label
    const tokenBar = document.getElementById('tokenBreakdown');
    if (tokenBar && data.tokens) {
      const items = tokenBar.querySelectorAll('.token-item');
      items.forEach(item => {
        const label = item.querySelector('.label');
        const value = item.querySelector('.value');
        if (!label || !value) return;
        const l = label.textContent.trim().toLowerCase();
        if (l === 'output') value.textContent = fmtNum(data.tokens.opus_out);
        else if (l === 'input') value.textContent = fmtNum(data.tokens.opus_in);
        else if (l === 'cache read') value.textContent = fmtNum(data.tokens.opus_cache);
      });
    }

    // Today's activity in Power Dashboard
    const sessionPosition = document.getElementById('sessionPosition');
    if (sessionPosition && data.today) {
      const pct = Math.min(100, Math.round((data.today.messages / 6800) * 100));
      sessionPosition.textContent = pct + '%';
      const bar = document.getElementById('sessionProgress');
      if (bar) bar.style.width = pct + '%';
    }

    // Update the budget
    const sessionBudget = document.getElementById('sessionBudget');
    if (sessionBudget && data.today) {
      const budgetPct = Math.min(100, Math.round((data.today.tokens / 500000000) * 100));
      sessionBudget.textContent = budgetPct + '% used';
    }

    lastUpdate = new Date();
    const ts = document.getElementById('live-ts');
    if (ts) ts.textContent = lastUpdate.toLocaleTimeString();
  }

  function updateHealth(data) {
    // Update daemon count if visible
    const daemonEl = document.querySelector('[id*="daemonCount"], [id*="daemons"]');
    if (daemonEl) daemonEl.textContent = data.daemons;
  }

  // --- SSE Connection ---
  function connect() {
    setStatus('connecting');
    const source = new EventSource('/api/stream');

    source.addEventListener('stats', (e) => {
      try {
        const data = JSON.parse(e.data);
        updateStats(data);
        if (!connected) { setStatus('connected'); retries = 0; }
      } catch(err) { console.warn('SSE stats parse error:', err); }
    });

    source.addEventListener('health', (e) => {
      try {
        updateHealth(JSON.parse(e.data));
      } catch(err) {}
    });

    source.addEventListener('heartbeat', () => {
      if (!connected) setStatus('connected');
      retries = 0;
    });

    source.onopen = () => { setStatus('connected'); retries = 0; };

    source.onerror = () => {
      source.close();
      setStatus('disconnected');
      retries++;
      if (retries < LIVE_CONFIG.maxRetries) {
        setTimeout(connect, LIVE_CONFIG.retryMs * Math.min(retries, 5));
      }
    };
  }

  // --- Init ---
  createStatusIndicator();
  // Only connect if served from HTTP (not file://)
  if (location.protocol.startsWith('http')) {
    connect();
  } else {
    setStatus('disconnected');
    document.getElementById('live-label').textContent = 'STATIC';
  }
})();
</script>
"""

# ═══════════════════════════════════════════════════════════════
# HTTP HANDLER
# ═══════════════════════════════════════════════════════════════

class CCCAPIHandler(BaseHTTPRequestHandler):
    """API handler with SSE streaming and dashboard serving."""

    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def load_json(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def load_jsonl(self, path, limit=100):
        entries = []
        try:
            with open(path) as f:
                for line in f:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass
            return entries[-limit:] if limit else entries
        except Exception:
            return []

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        routes = {
            '/dashboard': self.serve_dashboard,
            '/api/stream': self.serve_sse,
            '/api/stats': self.get_stats,
            '/api/cost': self.get_cost,
            '/api/routing': self.get_routing,
            '/api/sessions': self.get_sessions,
            '/api/tools': self.get_tools,
            '/api/git': self.get_git,
            '/api/health': self.get_health,
            '/api/fate': self.get_fate,
            '/api/cognitive': self.get_cognitive,
            '/api/memory/stats': self.get_memory_stats,
            '/': self.redirect_dashboard,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        else:
            self.send_json({"error": "Not found", "endpoints": list(routes.keys())}, 404)

    # ─── Dashboard Serving ────────────────────────────────────

    def redirect_dashboard(self, params):
        self.send_response(302)
        self.send_header('Location', '/dashboard')
        self.end_headers()

    def serve_dashboard(self, params):
        """Serve dashboard HTML with live SSE client injected."""
        if not DASHBOARD_HTML.exists():
            self.send_json({"error": "Dashboard not generated. Run: ccc --no-open"}, 404)
            return

        html = DASHBOARD_HTML.read_text(encoding='utf-8')

        # Inject SSE client before closing </body>
        if '</body>' in html:
            html = html.replace('</body>', SSE_CLIENT_JS + '\n</body>')
        else:
            html += SSE_CLIENT_JS

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    # ─── SSE Stream ───────────────────────────────────────────

    def serve_sse(self, params):
        """Server-Sent Events stream for real-time updates."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        # Send initial data burst
        try:
            stats = streamer._poll_stats()
            self.wfile.write(f"event: stats\ndata: {json.dumps(stats)}\n\n".encode())
            self.wfile.flush()
        except Exception:
            return

        # Register client and keep connection alive
        streamer.add_client(self.wfile)
        try:
            while True:
                time.sleep(1)
                # Check if client is still connected
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except Exception:
                    break
        except Exception:
            pass
        finally:
            streamer.remove_client(self.wfile)

    # ─── REST Endpoints (unchanged) ──────────────────────────

    def get_stats(self, params):
        data = self.load_json(CLAUDE_DIR / "stats-cache.json")
        self.send_json(data)

    def get_cost(self, params):
        """Compute cost data live from SQLite using token-level pricing."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT
                    SUM(opus_tokens_in) as opus_in, SUM(opus_tokens_out) as opus_out, SUM(opus_cache_read) as opus_cache,
                    SUM(sonnet_tokens_in) as sonnet_in, SUM(sonnet_tokens_out) as sonnet_out, SUM(sonnet_cache_read) as sonnet_cache,
                    SUM(haiku_tokens_in) as haiku_in, SUM(haiku_tokens_out) as haiku_out, SUM(haiku_cache_read) as haiku_cache,
                    MIN(date) as first_date, COUNT(*) as days_active
                FROM daily_stats
            """).fetchone()
            opus_cost = get_model_cost("opus", row['opus_in'] or 0, row['opus_out'] or 0, row['opus_cache'] or 0)
            sonnet_cost = get_model_cost("sonnet", row['sonnet_in'] or 0, row['sonnet_out'] or 0, row['sonnet_cache'] or 0)
            haiku_cost = get_model_cost("haiku", row['haiku_in'] or 0, row['haiku_out'] or 0, row['haiku_cache'] or 0)
            total_value = opus_cost + sonnet_cost + haiku_cost
            days = row['days_active'] or 1
            months = max(days / 30.0, 1)
            sub_paid = months * MONTHLY_RATE_USD
            # Cache savings: what cache reads would cost at full input price
            cache_total = (row['opus_cache'] or 0) + (row['sonnet_cache'] or 0) + (row['haiku_cache'] or 0)
            input_total = (row['opus_in'] or 0) + (row['sonnet_in'] or 0) + (row['haiku_in'] or 0)
            cache_eff = round(cache_total / max(cache_total + input_total, 1) * 100, 1)
            # Daily cost breakdown (last 30 days)
            daily_rows = conn.execute("""
                SELECT date, opus_tokens_in, opus_tokens_out, opus_cache_read,
                       sonnet_tokens_in, sonnet_tokens_out, sonnet_cache_read,
                       haiku_tokens_in, haiku_tokens_out, haiku_cache_read
                FROM daily_stats ORDER BY date DESC LIMIT 30
            """).fetchall()
            daily_costs = []
            for d in daily_rows:
                dc = (get_model_cost("opus", d['opus_tokens_in'] or 0, d['opus_tokens_out'] or 0, d['opus_cache_read'] or 0)
                    + get_model_cost("sonnet", d['sonnet_tokens_in'] or 0, d['sonnet_tokens_out'] or 0, d['sonnet_cache_read'] or 0)
                    + get_model_cost("haiku", d['haiku_tokens_in'] or 0, d['haiku_tokens_out'] or 0, d['haiku_cache_read'] or 0))
                daily_costs.append({"date": d['date'], "cost": round(dc, 2)})
            conn.close()
            self.send_json({
                "today": daily_costs[0]['cost'] if daily_costs else 0,
                "thisWeek": round(sum(d['cost'] for d in daily_costs[:7]), 2),
                "thisMonth": round(sum(d['cost'] for d in daily_costs[:30]), 2),
                "savedViaCache": round(total_value - sub_paid, 2) if total_value > sub_paid else 0,
                "cacheEfficiency": cache_eff,
                "dailyCosts": daily_costs,
                "totalValue": round(total_value, 2),
                "totalSubscriptionPaid": round(sub_paid, 2),
                "multiplier": round(total_value / sub_paid, 1) if sub_paid > 0 else 0,
                "rate": MONTHLY_RATE_USD,
                "ratePeriod": "monthly",
                "breakdown": {"opus": round(opus_cost, 2), "sonnet": round(sonnet_cost, 2), "haiku": round(haiku_cost, 2)}
            })
        except Exception as e:
            self.send_json({"error": str(e), "today": 0, "thisWeek": 0, "thisMonth": 0, "dailyCosts": []})

    def get_routing(self, params):
        limit = int(params.get('limit', [100])[0])
        dq_scores = self.load_jsonl(CLAUDE_DIR / "kernel/dq-scores.jsonl", limit)
        routing = self.load_jsonl(CLAUDE_DIR / "data/routing-metrics.jsonl", limit)
        feedback = self.load_jsonl(CLAUDE_DIR / "data/routing-feedback.jsonl", limit)
        self.send_json({
            "dq_scores": dq_scores, "routing_metrics": routing,
            "feedback": feedback, "total_dq": len(dq_scores),
            "total_routing": len(routing), "total_feedback": len(feedback)
        })

    def get_sessions(self, params):
        limit = int(params.get('limit', [50])[0])
        sessions = self.load_jsonl(CLAUDE_DIR / "data/session-outcomes.jsonl", limit)
        self.send_json({"sessions": sessions, "count": len(sessions)})

    def get_tools(self, params):
        limit = int(params.get('limit', [100])[0])
        usage = self.load_jsonl(CLAUDE_DIR / "data/tool-usage.jsonl", limit)
        success = self.load_jsonl(CLAUDE_DIR / "data/tool-success.jsonl", limit)
        self.send_json({"usage": usage, "success": success,
                        "total_usage": len(usage), "total_success": len(success)})

    def get_git(self, params):
        limit = int(params.get('limit', [50])[0])
        activity = self.load_jsonl(CLAUDE_DIR / "data/git-activity.jsonl", limit)
        self.send_json({"activity": activity, "count": len(activity)})

    def get_health(self, params):
        import subprocess
        try:
            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
            daemons = [l for l in result.stdout.split('\n') if 'com.claude' in l]
        except Exception:
            daemons = []
        cost_file = CLAUDE_DIR / "kernel/cost-data.json"
        cost_age = (datetime.now().timestamp() - cost_file.stat().st_mtime) / 60 if cost_file.exists() else -1
        self.send_json({
            "status": "healthy" if cost_age < 5 else "stale",
            "daemons_count": len(daemons),
            "cost_data_age_minutes": round(cost_age, 1),
            "timestamp": datetime.now().isoformat()
        })

    def get_fate(self, params):
        limit = int(params.get('limit', [50])[0])
        predictions = self.load_jsonl(CLAUDE_DIR / "kernel/cognitive-os/fate-predictions.jsonl", limit)
        correct = sum(1 for p in predictions if p.get("correct"))
        total = len(predictions)
        self.send_json({
            "predictions": predictions, "accuracy": round(correct / total * 100, 1) if total else 0,
            "correct": correct, "total": total
        })

    def get_cognitive(self, params):
        state = self.load_json(CLAUDE_DIR / "kernel/cognitive-os/current-state.json")
        flow = self.load_json(CLAUDE_DIR / "kernel/cognitive-os/flow-state.json")
        weekly = self.load_json(CLAUDE_DIR / "kernel/cognitive-os/weekly-energy.json")
        self.send_json({"current_state": state, "flow_state": flow, "weekly_energy": weekly})

    def get_memory_stats(self, params):
        if not MEMORY_AVAILABLE:
            self.send_json({"error": "Memory engine not available"}, 503)
            return
        self.send_json(memory_engine.get_stats())

    def post_memory_query(self, body):
        if not MEMORY_AVAILABLE:
            self.send_json({"error": "Memory engine not available"}, 503)
            return
        query = body.get("query", "").strip()
        if not query:
            self.send_json({"error": "Missing 'query' field"}, 400)
            return
        result = memory_engine.run_query(
            query, source=body.get("source", "all"),
            category=body.get("category", "all"),
            limit=min(int(body.get("limit", 10)), 50), surface="dashboard"
        )
        self.send_json(result)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON"}, 400)
                return

        post_routes = {'/api/memory/query': self.post_memory_query}
        handler = post_routes.get(parsed.path)
        if handler:
            handler(body)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def main():
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    # Start the data streamer background thread
    streamer.start()

    server = ThreadingHTTPServer(('localhost', port), CCCAPIHandler)
    print(f"CCC Live Server running on http://localhost:{port}")
    print(f"Dashboard: http://localhost:{port}/dashboard")
    print(f"SSE Stream: http://localhost:{port}/api/stream")
    print(f"Memory engine: {'available' if MEMORY_AVAILABLE else 'unavailable'}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        streamer.running = False
        server.shutdown()


if __name__ == "__main__":
    main()
