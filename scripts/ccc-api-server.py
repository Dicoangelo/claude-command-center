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

import hashlib
import importlib.util
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
DB_PATH = CLAUDE_DIR / "data" / "claude.db"
ANTIGRAVITY_DB = HOME / ".agent-core" / "storage" / "antigravity.db"
DASHBOARD_HTML = CLAUDE_DIR / "dashboard" / "claude-command-center.html"
PORT = 8766

# Import pricing config
sys.path.insert(0, str(CLAUDE_DIR / "config"))
try:
    from pricing import MONTHLY_RATE_USD, get_model_cost
except ImportError:

    def get_model_cost(m, i, o, c=0):
        return 0.0

    MONTHLY_RATE_USD = 200

# Import memory query engine
sys.path.insert(0, str(CLAUDE_DIR / "scripts"))
try:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "memory_query_engine", str(CLAUDE_DIR / "scripts/memory-query-engine.py")
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

    def __init__(self) -> None:
        self.clients: List[Any] = []
        self.lock: threading.Lock = threading.Lock()
        self.last_hash: Dict[str, str] = {}
        self.running: bool = False

    def add_client(self, wfile: Any) -> None:
        with self.lock:
            self.clients.append(wfile)

    def remove_client(self, wfile: Any) -> None:
        with self.lock:
            self.clients = [c for c in self.clients if c is not wfile]

    def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        encoded = payload.encode("utf-8")
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

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    def _poll_stats(self) -> Dict[str, Any]:
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
            today = datetime.now().strftime("%Y-%m-%d")
            today_row = conn.execute(
                """
                SELECT opus_messages + sonnet_messages + haiku_messages as messages,
                       session_count as sessions, tool_calls as tools,
                       opus_tokens_in + opus_tokens_out + opus_cache_read as tokens
                FROM daily_stats WHERE date = ?
            """,
                (today,),
            ).fetchone()

            # Active sessions (last 5 minutes)
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

            # Calculate API value for ROI
            cost_row = conn.execute("""
                SELECT SUM(cost_estimate) as total_cost
                FROM daily_stats
            """).fetchone()
            api_value = cost_row["total_cost"] or 0 if cost_row else 0

            # Today's cost
            today_cost_row = conn.execute(
                "SELECT cost_estimate FROM daily_stats WHERE date = ?",
                (today,),
            ).fetchone()
            today_cost = today_cost_row["cost_estimate"] if today_cost_row else 0

            conn.close()

            return {
                "totalSessions": row["total_sessions"] or 0,
                "totalMessages": row["total_messages"] or 0,
                "totalTools": row["total_tools"] or 0,
                "tokens": {
                    "opus_in": trow["opus_in"] or 0,
                    "opus_out": trow["opus_out"] or 0,
                    "opus_cache": trow["opus_cache"] or 0,
                    "sonnet_in": trow["sonnet_in"] or 0,
                    "sonnet_out": trow["sonnet_out"] or 0,
                    "haiku_in": trow["haiku_in"] or 0,
                    "haiku_out": trow["haiku_out"] or 0,
                },
                "today": {
                    "messages": today_row["messages"] if today_row else 0,
                    "sessions": today_row["sessions"] if today_row else 0,
                    "tools": today_row["tools"] if today_row else 0,
                    "tokens": today_row["tokens"] if today_row else 0,
                    "cost": round(today_cost, 2),
                }
                if today_row
                else {
                    "messages": 0,
                    "sessions": 0,
                    "tools": 0,
                    "tokens": 0,
                    "cost": 0,
                },
                "apiValue": round(api_value, 2),
                "activeSessions": active["c"] if active else 0,
                "latestSession": {
                    "id": latest["id"][:12] if latest else None,
                    "model": latest["model"] if latest else None,
                    "messages": latest["message_count"] if latest else 0,
                    "tools": latest["tool_count"] if latest else 0,
                    "outcome": latest["outcome"] if latest else None,
                }
                if latest
                else None,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e), "timestamp": datetime.now().isoformat()}

    def _poll_health(self) -> Dict[str, Any]:
        """Quick health check."""
        try:
            import subprocess

            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
            daemons = len([ln for ln in result.stdout.split("\n") if "com.claude" in ln])
        except Exception:
            daemons = 0

        return {
            "daemons": daemons,
            "timestamp": datetime.now().isoformat(),
        }

    def _poll_velocity(self) -> Dict[str, Any]:
        """Velocity field status for SSE streaming."""
        try:
            coord_dir = CLAUDE_DIR / "coordinator"
            sys.path.insert(0, str(coord_dir))
            from velocity_field import VelocityField, DIM_NAMES
            field = VelocityField()
            vector = field.sample()
            composition = field.compose(vector)

            # Compute level from composite score
            c = vector.weighted_composite()
            level = "surge" if c >= 0.7 else "active" if c >= 0.4 else "steady" if c >= 0.2 else "calm"

            return {
                "composite": round(c, 3),
                "level": level,
                "agents": composition.agent_count_int,
                "parallelism": composition.parallelism_label,
                "effort": composition.effort_label,
                "dimensions": {DIM_NAMES[i]: round(vector.dimensions[i], 3) for i in range(len(vector.dimensions))},
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e), "timestamp": datetime.now().isoformat()}

    def _data_changed(self, key: str, data: Dict[str, Any]) -> bool:
        """Check if data changed since last poll."""
        h = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if self.last_hash.get(key) != h:
            self.last_hash[key] = h
            return True
        return False

    def start(self) -> None:
        """Start the background polling thread."""
        if self.running:
            return
        self.running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self) -> None:
        """Main polling loop."""
        health_counter = 0
        while self.running:
            try:
                with self.lock:
                    has_clients = len(self.clients) > 0

                if has_clients:
                    # Always send stats
                    stats = self._poll_stats()
                    if self._data_changed("stats", stats):
                        self.broadcast("stats", stats)

                    # Health + velocity every 15 seconds (every 5th poll)
                    health_counter += 1
                    if health_counter >= 5:
                        health_counter = 0
                        health = self._poll_health()
                        if self._data_changed("health", health):
                            self.broadcast("health", health)
                        velocity = self._poll_velocity()
                        if self._data_changed("velocity", velocity):
                            self.broadcast("velocity", velocity)

                    # Heartbeat to keep connection alive
                    self.broadcast("heartbeat", {"t": int(time.time())})
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
    indicator.style.cssText = 'display:flex;align-items:center;gap:4px;' +
      'font-size:0.6rem;cursor:default;margin-top:1px;';
    indicator.innerHTML = `
      <span id="live-dot" style="width:6px;height:6px;border-radius:50%;
        background:#ff4d6a;display:inline-block;
        transition:background 0.3s;"></span>
      <span id="live-label" style="color:rgba(255,255,255,0.4);
        font-family:JetBrains Mono,monospace;">CONNECTING</span>
      <span id="live-ts" style="color:rgba(255,255,255,0.25);
        font-family:JetBrains Mono,monospace;font-size:0.55rem;
        margin-left:2px;"></span>
    `;
    const slot = document.getElementById('live-slot');
    if (slot) { slot.appendChild(indicator); }
    else { document.body.appendChild(indicator); }
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
    // Top stat cards — match by label text
    document.querySelectorAll('.stat-card').forEach(card => {
      const label = card.querySelector('.label') || card.querySelector('.stat-label');
      const value = card.querySelector('.value') || card.querySelector('.stat-value');
      if (!label || !value) return;

      const l = label.textContent.trim().toUpperCase();
      if (l === 'SESSIONS') value.textContent = fmtNum(data.totalSessions);
      else if (l === 'MESSAGES') value.textContent = fmtNum(data.totalMessages);
      else if (l === 'TOOL CALLS') value.textContent = fmtNum(data.totalTools);
      else if (l === 'AVG/DAY') {
        const days = Math.max(1, Math.round(data.totalSessions / 70));
        value.textContent = fmtNum(Math.round(data.totalMessages / Math.max(days, 1)));
      }
      else if (l === 'ROI' && data.apiValue > 0) {
        const days = 39;
        const subCost = days * (200 / 30);
        const roi = (data.apiValue / Math.max(subCost, 1)).toFixed(1);
        value.textContent = roi + 'x';
      }
    });

    // Token breakdown
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

    // Power Dashboard — session position
    const sessionPosition = document.getElementById('sessionPosition');
    if (sessionPosition && data.today) {
      const pct = Math.min(100, Math.round((data.today.messages / 6800) * 100));
      sessionPosition.textContent = pct + '%';
      const bar = document.getElementById('sessionProgress');
      if (bar) bar.style.width = pct + '%';
      const fill = document.getElementById('sessionProgressFill');
      if (fill) fill.style.width = pct + '%';
    }

    // Power Dashboard — budget
    const sessionBudget = document.getElementById('sessionBudget');
    if (sessionBudget && data.today) {
      const budgetPct = Math.min(100, Math.round((data.today.tokens / 500000000) * 100));
      sessionBudget.textContent = budgetPct + '% used';
    }

    // Power Dashboard — background agents count
    const bgCount = document.getElementById('bgAgents');
    if (bgCount && data.activeSessions != null) {
      bgCount.textContent = data.activeSessions + ' active';
    }

    // Latest session indicator
    if (data.latestSession) {
      const el = document.getElementById('latestSessionInfo');
      if (el) {
        const s = data.latestSession;
        el.innerHTML = '<span style="color:var(--accent-cyan)">' + (s.model||'') +
          '</span> · ' + s.messages + ' msgs · ' + (s.outcome || 'running');
      }
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

    source.addEventListener('velocity', (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.error) return;
        // Update velocity stat cards on the velocity tab
        document.querySelectorAll('#tab-velocity .stat-card').forEach(card => {
          const label = card.querySelector('.label,.stat-label');
          const value = card.querySelector('.value,.stat-value');
          if (!label || !value) return;
          const l = label.textContent.trim().toUpperCase();
          if (l === 'COMPOSITE') value.textContent = d.composite;
          else if (l === 'LEVEL') value.textContent = d.level;
          else if (l === 'AGENTS') value.textContent = d.agents;
          else if (l === 'PARALLELISM') value.textContent = d.parallelism;
          else if (l === 'EFFORT') value.textContent = d.effort;
        });
        // Update velocity dimension bars
        if (d.dimensions) {
          Object.entries(d.dimensions).forEach(([name, val]) => {
            const bar = document.getElementById('vbar-' + name);
            if (bar) {
              bar.style.width = (val * 100) + '%';
              const valEl = bar.closest('.dim-row')?.querySelector('.dim-val');
              if (valEl) valEl.textContent = val.toFixed(3);
            }
          });
        }
      } catch(err) { console.warn('SSE velocity parse error:', err); }
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

  // --- Chart Auto-Refresh (SQLite → API → Charts) ---
  // Keeps all charts fresh from SQLite every 60s
  let _chartInstances = {};

  function destroyChart(canvasId) {
    if (_chartInstances[canvasId]) {
      _chartInstances[canvasId].destroy();
      delete _chartInstances[canvasId];
    }
    // Also destroy any Chart.js instance on the canvas
    const canvas = document.getElementById(canvasId);
    if (canvas) {
      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();
    }
  }

  function refreshAllCharts() {
    fetch('/api/all-data')
      .then(r => r.json())
      .then(data => {
        if (data.error) return;
        const stats = data.stats;
        if (!stats) return;

        // Update STATS_DATA properties in-place (it's const but object is mutable)
        if (window.STATS_DATA_LIVE) {
          Object.assign(window.STATS_DATA_LIVE, stats);
        } else {
          window.STATS_DATA_LIVE = stats;
        }

        // Rebuild daily activity charts
        if (stats.dailyActivity?.length) {
          const labels = stats.dailyActivity.map(d => d.date.slice(5));

          // Messages chart
          destroyChart('messagesChart');
          const msgCtx = document.getElementById('messagesChart');
          if (msgCtx) {
            const grad = msgCtx.getContext('2d').createLinearGradient(0, 0, 0, 280);
            grad.addColorStop(0, 'rgba(233,69,96,0.9)');
            grad.addColorStop(1, 'rgba(233,69,96,0.3)');
            _chartInstances['messagesChart'] = new Chart(msgCtx, {
              type: 'bar',
              data: { labels, datasets: [{ data: stats.dailyActivity.map(d => d.messageCount), backgroundColor: grad, borderRadius: 8, borderSkipped: false }] },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#666', maxTicksLimit: 15 }, grid: { display: false } }, y: { ticks: { color: '#666' }, grid: { color: 'rgba(255,255,255,0.05)' } } } }
            });
          }

          // Tools chart
          destroyChart('toolsChart');
          const toolCtx = document.getElementById('toolsChart');
          if (toolCtx) {
            const grad2 = toolCtx.getContext('2d').createLinearGradient(0, 0, 0, 280);
            grad2.addColorStop(0, 'rgba(254,202,87,0.9)');
            grad2.addColorStop(1, 'rgba(254,202,87,0.3)');
            _chartInstances['toolsChart'] = new Chart(toolCtx, {
              type: 'bar',
              data: { labels, datasets: [{ data: stats.dailyActivity.map(d => d.toolCallCount), backgroundColor: grad2, borderRadius: 8, borderSkipped: false }] },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#666', maxTicksLimit: 15 }, grid: { display: false } }, y: { ticks: { color: '#666' }, grid: { color: 'rgba(255,255,255,0.05)' } } } }
            });
          }
        }

        // Tokens chart
        if (stats.dailyModelTokens?.length) {
          destroyChart('tokensChart');
          const tokCtx = document.getElementById('tokensChart');
          if (tokCtx) {
            const tokenLabels = stats.dailyModelTokens.map(d => d.date.slice(5));
            _chartInstances['tokensChart'] = new Chart(tokCtx, {
              type: 'line',
              data: {
                labels: tokenLabels,
                datasets: [
                  { label: 'Opus', data: stats.dailyModelTokens.map(d => d.tokensByModel?.opus || 0), borderColor: '#e94560', backgroundColor: 'rgba(233,69,96,0.1)', fill: true, tension: 0.3, pointRadius: 0 },
                  { label: 'Sonnet', data: stats.dailyModelTokens.map(d => d.tokensByModel?.sonnet || 0), borderColor: '#48dbfb', backgroundColor: 'rgba(72,219,251,0.1)', fill: true, tension: 0.3, pointRadius: 0 },
                  { label: 'Haiku', data: stats.dailyModelTokens.map(d => d.tokensByModel?.haiku || 0), borderColor: '#feca57', backgroundColor: 'rgba(254,202,87,0.1)', fill: true, tension: 0.3, pointRadius: 0 },
                ]
              },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#888', boxWidth: 12 } } }, scales: { x: { ticks: { color: '#666', maxTicksLimit: 15 }, grid: { display: false } }, y: { ticks: { color: '#666', callback: v => v >= 1e9 ? (v/1e9).toFixed(0)+'B' : v >= 1e6 ? (v/1e6).toFixed(0)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : v }, grid: { color: 'rgba(255,255,255,0.05)' } } } }
            });
          }
        }

        // Hour chart
        if (stats.hourCounts) {
          destroyChart('hourChart');
          const hourCtx = document.getElementById('hourChart');
          if (hourCtx) {
            const hourData = Array.from({length: 24}, () => 0);
            Object.entries(stats.hourCounts).forEach(([h, c]) => hourData[parseInt(h)] = c);
            _chartInstances['hourChart'] = new Chart(hourCtx, {
              type: 'bar',
              data: { labels: Array.from({length: 24}, (_, i) => i + 'h'), datasets: [{ data: hourData, backgroundColor: hourData.map((_, i) => i >= 22 || i <= 5 ? 'rgba(233,69,96,0.7)' : i >= 9 && i <= 18 ? 'rgba(72,219,251,0.7)' : 'rgba(254,202,87,0.7)'), borderRadius: 4 }] },
              options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#666' }, grid: { display: false } }, y: { ticks: { color: '#666' }, grid: { color: 'rgba(255,255,255,0.05)' } } } }
            });
          }
        }

        // Update stat card values
        updateStats({
          totalSessions: stats.totalSessions,
          totalMessages: stats.totalMessages,
          totalTools: stats.totalTools,
          tokens: stats.modelUsage ? {
            opus_in: stats.modelUsage.opus?.inputTokens || 0,
            opus_out: stats.modelUsage.opus?.outputTokens || 0,
            opus_cache: stats.modelUsage.opus?.cacheReadInputTokens || 0,
            sonnet_in: stats.modelUsage.sonnet?.inputTokens || 0,
            sonnet_out: stats.modelUsage.sonnet?.outputTokens || 0,
            haiku_in: stats.modelUsage.haiku?.inputTokens || 0,
            haiku_out: stats.modelUsage.haiku?.outputTokens || 0,
          } : {},
          apiValue: data.subscription?.totalValue || 0,
          today: stats.dailyActivity?.length ? (() => {
            const today = stats.dailyActivity[stats.dailyActivity.length - 1];
            return { messages: today.messageCount, sessions: today.sessionCount, tools: today.toolCallCount, tokens: 0, cost: 0 };
          })() : { messages: 0, sessions: 0, tools: 0, tokens: 0, cost: 0 },
          activeSessions: 0,
          latestSession: null,
        });

        console.log('[CCC-LIVE] Charts refreshed from SQLite at', new Date().toLocaleTimeString());
      })
      .catch(err => console.warn('[CCC-LIVE] Chart refresh failed:', err));
  }

  // --- Init ---
  createStatusIndicator();
  // Only connect if served from HTTP (not file://)
  if (location.protocol.startsWith('http')) {
    connect();
    // Refresh charts from SQLite on load + every 60s
    setTimeout(refreshAllCharts, 2000);
    setInterval(refreshAllCharts, 60000);
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

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def load_json(self, path: Path) -> Dict[str, Any]:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def load_jsonl(self, path: Path, limit: int = 100) -> List[Dict[str, Any]]:
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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        routes = {
            "/dashboard": self.serve_dashboard,
            "/api/stream": self.serve_sse,
            "/api/stats": self.get_stats,
            "/api/cost": self.get_cost,
            "/api/routing": self.get_routing,
            "/api/sessions": self.get_sessions,
            "/api/tools": self.get_tools,
            "/api/git": self.get_git,
            "/api/health": self.get_health,
            "/api/fate": self.get_fate,
            "/api/cognitive": self.get_cognitive,
            "/api/velocity": self.get_velocity,
            "/api/memory/stats": self.get_memory_stats,
            "/api/autonomy": self.get_autonomy,
            "/api/all-data": self.get_all_data,
            "/api/crm": self.get_crm,
            "/api/expertise": self.get_expertise,
            "/": self.redirect_dashboard,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        else:
            self.send_json({"error": "Not found", "endpoints": list(routes.keys())}, 404)

    # ─── Dashboard Serving ────────────────────────────────────

    def redirect_dashboard(self, params: Dict[str, List[str]]) -> None:
        self.send_response(302)
        self.send_header("Location", "/dashboard")
        self.end_headers()

    def serve_dashboard(self, params: Dict[str, List[str]]) -> None:
        """Serve dashboard HTML with live SSE client injected."""
        if not DASHBOARD_HTML.exists():
            self.send_json({"error": "Dashboard not generated. Run: ccc --no-open"}, 404)
            return

        html = DASHBOARD_HTML.read_text(encoding="utf-8")

        # Inject SSE client before closing </body>
        if "</body>" in html:
            html = html.replace("</body>", SSE_CLIENT_JS + "\n</body>")
        else:
            html += SSE_CLIENT_JS

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    # ─── SSE Stream ───────────────────────────────────────────

    def serve_sse(self, params: Dict[str, List[str]]) -> None:
        """Server-Sent Events stream for real-time updates."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
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

    def get_stats(self, params: Dict[str, List[str]]) -> None:
        # Live query from SQLite, fall back to stats-cache.json
        try:
            _sql_spec = importlib.util.spec_from_file_location(
                "ccc_sql_data",
                str(Path(__file__).parent / "ccc-sql-data.py"),
            )
            _sql_mod = importlib.util.module_from_spec(_sql_spec)
            _sql_spec.loader.exec_module(_sql_mod)
            data = _sql_mod.get_stats_data()
        except Exception:
            data = self.load_json(CLAUDE_DIR / "stats-cache.json")
        self.send_json(data)

    def get_cost(self, params: Dict[str, List[str]]) -> None:
        """Compute cost data live from SQLite using token-level pricing."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT
                    SUM(opus_tokens_in) as opus_in,
                    SUM(opus_tokens_out) as opus_out,
                    SUM(opus_cache_read) as opus_cache,
                    SUM(sonnet_tokens_in) as sonnet_in,
                    SUM(sonnet_tokens_out) as sonnet_out,
                    SUM(sonnet_cache_read) as sonnet_cache,
                    SUM(haiku_tokens_in) as haiku_in,
                    SUM(haiku_tokens_out) as haiku_out,
                    SUM(haiku_cache_read) as haiku_cache,
                    MIN(date) as first_date, COUNT(*) as days_active
                FROM daily_stats
            """).fetchone()
            opus_cost = get_model_cost("opus", row["opus_in"] or 0, row["opus_out"] or 0, row["opus_cache"] or 0)
            sonnet_cost = get_model_cost(
                "sonnet", row["sonnet_in"] or 0, row["sonnet_out"] or 0, row["sonnet_cache"] or 0
            )
            haiku_cost = get_model_cost("haiku", row["haiku_in"] or 0, row["haiku_out"] or 0, row["haiku_cache"] or 0)
            total_value = opus_cost + sonnet_cost + haiku_cost
            days = row["days_active"] or 1
            months = max(days / 30.0, 1)
            sub_paid = months * MONTHLY_RATE_USD
            # Cache savings: what cache reads would cost at full input price
            cache_total = (row["opus_cache"] or 0) + (row["sonnet_cache"] or 0) + (row["haiku_cache"] or 0)
            input_total = (row["opus_in"] or 0) + (row["sonnet_in"] or 0) + (row["haiku_in"] or 0)
            cache_eff = round(cache_total / max(cache_total + input_total, 1) * 100, 1)
            # Daily cost breakdown (all time)
            daily_rows = conn.execute("""
                SELECT date, opus_tokens_in, opus_tokens_out, opus_cache_read,
                       sonnet_tokens_in, sonnet_tokens_out, sonnet_cache_read,
                       haiku_tokens_in, haiku_tokens_out, haiku_cache_read
                FROM daily_stats ORDER BY date ASC
            """).fetchall()
            daily_costs = []
            for d in daily_rows:
                dc = (
                    get_model_cost(
                        "opus", d["opus_tokens_in"] or 0, d["opus_tokens_out"] or 0, d["opus_cache_read"] or 0
                    )
                    + get_model_cost(
                        "sonnet", d["sonnet_tokens_in"] or 0, d["sonnet_tokens_out"] or 0, d["sonnet_cache_read"] or 0
                    )
                    + get_model_cost(
                        "haiku", d["haiku_tokens_in"] or 0, d["haiku_tokens_out"] or 0, d["haiku_cache_read"] or 0
                    )
                )
                daily_costs.append({"date": d["date"], "cost": round(dc, 2)})
            conn.close()
            self.send_json(
                {
                    "today": daily_costs[-1]["cost"] if daily_costs else 0,
                    "thisWeek": round(sum(d["cost"] for d in daily_costs[-7:]), 2),
                    "thisMonth": round(sum(d["cost"] for d in daily_costs[-30:]), 2),
                    "savedViaCache": round(total_value - sub_paid, 2) if total_value > sub_paid else 0,
                    "cacheEfficiency": cache_eff,
                    "dailyCosts": daily_costs,
                    "totalValue": round(total_value, 2),
                    "totalSubscriptionPaid": round(sub_paid, 2),
                    "multiplier": round(total_value / sub_paid, 1) if sub_paid > 0 else 0,
                    "rate": MONTHLY_RATE_USD,
                    "ratePeriod": "monthly",
                    "breakdown": {
                        "opus": round(opus_cost, 2),
                        "sonnet": round(sonnet_cost, 2),
                        "haiku": round(haiku_cost, 2),
                    },
                }
            )
        except Exception as e:
            self.send_json({"error": str(e), "today": 0, "thisWeek": 0, "thisMonth": 0, "dailyCosts": []})

    def get_routing(self, params: Dict[str, List[str]]) -> None:
        limit = int(params.get("limit", [100])[0])
        dq_scores = self.load_jsonl(CLAUDE_DIR / "kernel/dq-scores.jsonl", limit)
        routing = self.load_jsonl(CLAUDE_DIR / "data/routing-metrics.jsonl", limit)
        feedback = self.load_jsonl(CLAUDE_DIR / "data/routing-feedback.jsonl", limit)
        self.send_json(
            {
                "dq_scores": dq_scores,
                "routing_metrics": routing,
                "feedback": feedback,
                "total_dq": len(dq_scores),
                "total_routing": len(routing),
                "total_feedback": len(feedback),
            }
        )

    def get_sessions(self, params: Dict[str, List[str]]) -> None:
        limit = int(params.get("limit", [50])[0])
        sessions = self.load_jsonl(CLAUDE_DIR / "data/session-outcomes.jsonl", limit)
        self.send_json({"sessions": sessions, "count": len(sessions)})

    def get_tools(self, params: Dict[str, List[str]]) -> None:
        """Tool analytics from live tool_events table (114K+ rows)."""
        days = int(params.get("days", [7])[0])
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            cutoff = int(datetime.now().timestamp()) - (days * 86400)

            # Top tools by call count
            usage = [dict(r) for r in conn.execute("""
                SELECT tool_name, count(*) as total_calls,
                       sum(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                       sum(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failure_count,
                       round(avg(duration_ms), 1) as avg_duration_ms,
                       round(100.0 * sum(CASE WHEN success = 1 THEN 1 ELSE 0 END) / count(*), 1) as success_rate
                FROM tool_events WHERE timestamp > ?
                GROUP BY tool_name ORDER BY total_calls DESC
            """, (cutoff,)).fetchall()]

            # Failing tools (lowest success rate with >5 calls)
            failing = [dict(r) for r in conn.execute("""
                SELECT tool_name, count(*) as total,
                       round(100.0 * sum(CASE WHEN success = 1 THEN 1 ELSE 0 END) / count(*), 1) as success_rate
                FROM tool_events WHERE timestamp > ?
                GROUP BY tool_name HAVING count(*) > 5
                ORDER BY success_rate ASC LIMIT 10
            """, (cutoff,)).fetchall()]

            # Hourly tool activity pattern
            hourly = [dict(r) for r in conn.execute("""
                SELECT cast(strftime('%H', timestamp, 'unixepoch', 'localtime') as integer) as hour,
                       count(*) as calls
                FROM tool_events WHERE timestamp > ?
                GROUP BY hour ORDER BY hour
            """, (cutoff,)).fetchall()]

            # Total stats
            totals = dict(conn.execute("""
                SELECT count(*) as total_events,
                       count(DISTINCT tool_name) as unique_tools,
                       round(100.0 * sum(CASE WHEN success = 1 THEN 1 ELSE 0 END) / count(*), 1) as overall_success_rate,
                       round(avg(duration_ms), 1) as avg_duration
                FROM tool_events WHERE timestamp > ?
            """, (cutoff,)).fetchone())

            conn.close()
            self.send_json({
                "usage": usage,
                "failing": failing,
                "hourly": hourly,
                "totals": totals,
                "days": days,
                "total_usage": len(usage),
            })
        except Exception as e:
            # Fallback to old JSONL if SQLite fails
            limit = int(params.get("limit", [100])[0])
            usage = self.load_jsonl(CLAUDE_DIR / "data/tool-usage.jsonl", limit)
            success = self.load_jsonl(CLAUDE_DIR / "data/tool-success.jsonl", limit)
            self.send_json({"usage": usage, "success": success, "total_usage": len(usage), "error_note": str(e)})

    def get_git(self, params: Dict[str, List[str]]) -> None:
        limit = int(params.get("limit", [50])[0])
        activity = self.load_jsonl(CLAUDE_DIR / "data/git-activity.jsonl", limit)
        self.send_json({"activity": activity, "count": len(activity)})

    def get_health(self, params: Dict[str, List[str]]) -> None:
        import subprocess

        try:
            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
            daemons = [ln for ln in result.stdout.split("\n") if "com.claude" in ln]
        except Exception:
            daemons = []
        cost_file = CLAUDE_DIR / "kernel/cost-data.json"
        cost_age = (datetime.now().timestamp() - cost_file.stat().st_mtime) / 60 if cost_file.exists() else -1
        self.send_json(
            {
                "status": "healthy" if cost_age < 5 else "stale",
                "daemons_count": len(daemons),
                "cost_data_age_minutes": round(cost_age, 1),
                "timestamp": datetime.now().isoformat(),
            }
        )

    def get_fate(self, params: Dict[str, List[str]]) -> None:
        limit = int(params.get("limit", [50])[0])
        predictions = self.load_jsonl(CLAUDE_DIR / "kernel/cognitive-os/fate-predictions.jsonl", limit)
        correct = sum(1 for p in predictions if p.get("correct"))
        total = len(predictions)
        self.send_json(
            {
                "predictions": predictions,
                "accuracy": round(correct / total * 100, 1) if total else 0,
                "correct": correct,
                "total": total,
            }
        )

    def get_cognitive(self, params: Dict[str, List[str]]) -> None:
        state = self.load_json(CLAUDE_DIR / "kernel/cognitive-os/current-state.json")
        flow = self.load_json(CLAUDE_DIR / "kernel/cognitive-os/flow-state.json")
        weekly = self.load_json(CLAUDE_DIR / "kernel/cognitive-os/weekly-energy.json")
        self.send_json({"current_state": state, "flow_state": flow, "weekly_energy": weekly})

    def get_velocity(self, params: Dict[str, List[str]]) -> None:
        """VAAC velocity field status — 10D vector, composition, and history."""
        try:
            coord_dir = CLAUDE_DIR / "coordinator"
            sys.path.insert(0, str(coord_dir))

            # Sample velocity field
            from velocity_field import VelocityField, DIM_NAMES
            field = VelocityField()
            vector = field.sample()
            composition = field.compose(vector)

            # Get VAAC 3-signal aggregation
            from velocity import VelocitySignalAggregator
            agg = VelocitySignalAggregator()
            vaac_status = agg.get_velocity_status()

            # Read velocity history
            history_path = coord_dir / "data" / "velocity-history.jsonl"
            history = []
            if history_path.exists():
                for line in history_path.read_text().strip().split("\n")[-50:]:
                    if line.strip():
                        try:
                            history.append(json.loads(line))
                        except Exception:
                            pass

            self.send_json({
                "field": vector.to_dict(),
                "composition": composition.to_dict(),
                "vaac": vaac_status,
                "history": history,
                "dim_names": DIM_NAMES,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def get_autonomy(self, params: Dict[str, List[str]]) -> None:
        """Autonomy streak data from SQLite."""
        try:
            import subprocess

            result = subprocess.run(
                ["python3", str(Path(__file__).parent / "ccc-sql-data.py"), "autonomy"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                self.send_json(json.loads(result.stdout))
            else:
                self.send_json({"error": result.stderr}, 500)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def get_all_data(self, params: Dict[str, List[str]]) -> None:
        """All chart data from SQLite — single source of truth for the dashboard.

        Returns stats, subscription, outcomes, routing, recovery, autonomy
        all from SQLite so charts never go stale.
        """
        try:
            import subprocess
            result = subprocess.run(
                ["python3", str(Path(__file__).parent / "ccc-sql-data.py"), "all"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                self.send_json(json.loads(result.stdout))
            else:
                self.send_json({"error": result.stderr}, 500)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def get_expertise(self, params: Dict[str, List[str]]) -> None:
        """Expertise routing heatmap — domain × model from 4.5K routing events."""
        days = int(params.get("days", [30])[0])
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            cutoff = int(datetime.now().timestamp()) - (days * 86400)

            # Domain × Model heatmap
            heatmap = [dict(r) for r in conn.execute("""
                SELECT domain, chosen_model as model, count(*) as count,
                       round(avg(expertise_level), 2) as avg_expertise,
                       round(avg(query_complexity), 2) as avg_complexity
                FROM expertise_routing_events WHERE timestamp > ?
                GROUP BY domain, chosen_model
                ORDER BY count DESC
            """, (cutoff,)).fetchall()]

            # Domain summary
            domains = [dict(r) for r in conn.execute("""
                SELECT domain, count(*) as total_queries,
                       round(avg(expertise_level), 2) as avg_expertise,
                       round(avg(query_complexity), 2) as avg_complexity,
                       group_concat(DISTINCT chosen_model) as models_used
                FROM expertise_routing_events WHERE timestamp > ?
                GROUP BY domain ORDER BY total_queries DESC
            """, (cutoff,)).fetchall()]

            # Model preference by domain (which model dominates each domain)
            preferences = [dict(r) for r in conn.execute("""
                SELECT domain, chosen_model as dominant_model,
                       count(*) as count,
                       round(100.0 * count(*) / (
                           SELECT count(*) FROM expertise_routing_events e2
                           WHERE e2.domain = expertise_routing_events.domain AND e2.timestamp > ?
                       ), 1) as pct
                FROM expertise_routing_events WHERE timestamp > ?
                GROUP BY domain, chosen_model
                HAVING count(*) = (
                    SELECT max(cnt) FROM (
                        SELECT count(*) as cnt FROM expertise_routing_events e3
                        WHERE e3.domain = expertise_routing_events.domain AND e3.timestamp > ?
                        GROUP BY e3.chosen_model
                    )
                )
                ORDER BY count DESC
            """, (cutoff, cutoff, cutoff)).fetchall()]

            # Complexity distribution
            complexity_dist = [dict(r) for r in conn.execute("""
                SELECT
                    CASE
                        WHEN query_complexity < 0.3 THEN 'low'
                        WHEN query_complexity < 0.6 THEN 'medium'
                        WHEN query_complexity < 0.8 THEN 'high'
                        ELSE 'extreme'
                    END as level,
                    chosen_model as model,
                    count(*) as count
                FROM expertise_routing_events WHERE timestamp > ?
                GROUP BY level, model
                ORDER BY level, count DESC
            """, (cutoff,)).fetchall()]

            # Totals
            totals = dict(conn.execute("""
                SELECT count(*) as total_events,
                       count(DISTINCT domain) as unique_domains,
                       round(avg(expertise_level), 2) as avg_expertise,
                       round(avg(query_complexity), 2) as avg_complexity
                FROM expertise_routing_events WHERE timestamp > ?
            """, (cutoff,)).fetchone())

            conn.close()
            self.send_json({
                "heatmap": heatmap,
                "domains": domains,
                "preferences": preferences,
                "complexity_dist": complexity_dist,
                "totals": totals,
                "days": days,
            })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def get_crm(self, params: Dict[str, List[str]]) -> None:
        """CRM data from antigravity.db — contacts, deals, interactions."""
        try:
            conn = sqlite3.connect(str(ANTIGRAVITY_DB), timeout=5)
            conn.row_factory = sqlite3.Row

            # Stats
            stats = {}
            row = conn.execute("SELECT count(*) as c FROM crm_contacts").fetchone()
            stats["total_contacts"] = row["c"] if row else 0

            row = conn.execute("SELECT count(*) as c, sum(value) as v FROM crm_deals WHERE stage NOT IN ('won','lost')").fetchone()
            stats["active_deals"] = row["c"] if row else 0
            stats["pipeline_value"] = round(row["v"] or 0, 2) if row else 0

            row = conn.execute("SELECT count(*) as c FROM crm_deals WHERE stage = 'won'").fetchone()
            stats["won_deals"] = row["c"] if row else 0

            row = conn.execute("SELECT count(*) as c FROM crm_interactions").fetchone()
            stats["total_interactions"] = row["c"] if row else 0

            row = conn.execute("""
                SELECT count(*) as c FROM crm_interactions
                WHERE follow_up IS NOT NULL AND follow_up_date <= date('now', '+7 days')
            """).fetchone()
            stats["follow_ups_due"] = row["c"] if row else 0

            # Contacts with deal + interaction counts
            contacts = [dict(r) for r in conn.execute("""
                SELECT c.id, c.name, c.company, c.role, c.category, c.x_handle, c.email, c.source,
                       count(DISTINCT d.id) as deals, count(DISTINCT i.id) as interactions
                FROM crm_contacts c
                LEFT JOIN crm_deals d ON d.contact_id = c.id
                LEFT JOIN crm_interactions i ON i.contact_id = c.id
                GROUP BY c.id ORDER BY c.updated_at DESC
            """).fetchall()]

            # Deals with contact names
            deals = [dict(r) for r in conn.execute("""
                SELECT d.*, c.name as contact_name
                FROM crm_deals d LEFT JOIN crm_contacts c ON d.contact_id = c.id
                ORDER BY CASE d.stage
                    WHEN 'prospect' THEN 1 WHEN 'contacted' THEN 2 WHEN 'meeting' THEN 3
                    WHEN 'proposal' THEN 4 WHEN 'negotiation' THEN 5 WHEN 'won' THEN 6 WHEN 'lost' THEN 7 END,
                    d.updated_at DESC
            """).fetchall()]

            # Recent interactions
            interactions = [dict(r) for r in conn.execute("""
                SELECT i.*, c.name as contact_name
                FROM crm_interactions i LEFT JOIN crm_contacts c ON i.contact_id = c.id
                ORDER BY i.created_at DESC LIMIT 20
            """).fetchall()]

            # Follow-ups due
            follow_ups = [dict(r) for r in conn.execute("""
                SELECT c.name, c.company, i.follow_up, i.follow_up_date, i.type
                FROM crm_interactions i JOIN crm_contacts c ON i.contact_id = c.id
                WHERE i.follow_up IS NOT NULL AND i.follow_up_date <= date('now', '+7 days')
                ORDER BY i.follow_up_date
            """).fetchall()]

            # Category breakdown
            categories = [dict(r) for r in conn.execute("""
                SELECT category, count(*) as count FROM crm_contacts GROUP BY category ORDER BY count DESC
            """).fetchall()]

            conn.close()

            self.send_json({
                "stats": stats,
                "contacts": contacts,
                "deals": deals,
                "interactions": interactions,
                "follow_ups": follow_ups,
                "categories": categories,
            })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def get_memory_stats(self, params: Dict[str, List[str]]) -> None:
        if not MEMORY_AVAILABLE:
            self.send_json({"error": "Memory engine not available"}, 503)
            return
        self.send_json(memory_engine.get_stats())

    def post_memory_query(self, body: Dict[str, Any]) -> None:
        if not MEMORY_AVAILABLE:
            self.send_json({"error": "Memory engine not available"}, 503)
            return
        query = body.get("query", "").strip()
        if not query:
            self.send_json({"error": "Missing 'query' field"}, 400)
            return
        result = memory_engine.run_query(
            query,
            source=body.get("source", "all"),
            category=body.get("category", "all"),
            limit=min(int(body.get("limit", 10)), 50),
            surface="dashboard",
        )
        self.send_json(result)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON"}, 400)
                return

        post_routes = {"/api/memory/query": self.post_memory_query}
        handler = post_routes.get(parsed.path)
        if handler:
            handler(body)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main() -> None:
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    # Start the data streamer background thread
    streamer.start()

    server = ThreadingHTTPServer(("localhost", port), CCCAPIHandler)
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
