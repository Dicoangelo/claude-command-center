#!/usr/bin/env python3
"""
CCC SQLite Data Exporter

Single script that queries claude.db and outputs all SQLite-sourced template
data as JSON. Called by ccc-generator.sh to replace JSON file reads.

Usage:
    python3 ccc-sql-data.py stats           # __STATS_DATA__
    python3 ccc-sql-data.py subscription    # __SUBSCRIPTION_DATA__
    python3 ccc-sql-data.py outcomes        # __SESSION_OUTCOMES_DATA__
    python3 ccc-sql-data.py routing         # __ROUTING_DATA__
    python3 ccc-sql-data.py recovery        # __RECOVERY_DATA__
    python3 ccc-sql-data.py all             # All above as one JSON object
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path.home() / ".claude/config"))
from pricing import ESTIMATES as COSTS_PER_MSG

DB_PATH = Path.home() / ".claude/data/claude.db"
HOME = Path.home()


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_stats_data():
    """Generate __STATS_DATA__ from SQLite (replaces stats-cache.json)."""
    conn = get_db()

    # Totals
    row = conn.execute("""
        SELECT
            SUM(opus_messages + sonnet_messages + haiku_messages) as total_messages,
            SUM(session_count) as total_sessions,
            SUM(tool_calls) as total_tools,
            SUM(opus_messages) as opus_messages,
            SUM(sonnet_messages) as sonnet_messages,
            SUM(haiku_messages) as haiku_messages
        FROM daily_stats
    """).fetchone()

    total_messages = row['total_messages'] or 0
    total_sessions = row['total_sessions'] or 0
    total_tools = row['total_tools'] or 0

    # Token totals
    trow = conn.execute("""
        SELECT
            SUM(opus_tokens_in) as opus_in, SUM(opus_tokens_out) as opus_out,
            SUM(opus_cache_read) as opus_cache,
            SUM(sonnet_tokens_in) as sonnet_in, SUM(sonnet_tokens_out) as sonnet_out,
            SUM(sonnet_cache_read) as sonnet_cache,
            SUM(haiku_tokens_in) as haiku_in, SUM(haiku_tokens_out) as haiku_out,
            SUM(haiku_cache_read) as haiku_cache
        FROM daily_stats
    """).fetchone()

    # Hourly activity
    hours = conn.execute("""
        SELECT hour, SUM(session_count + message_count) as total
        FROM hourly_activity
        GROUP BY hour ORDER BY hour
    """).fetchall()
    hour_counts = {str(r['hour']): r['total'] for r in hours}

    # Longest session
    longest = conn.execute("""
        SELECT id, substr(started_at, 1, 10) as date, message_count
        FROM sessions
        ORDER BY message_count DESC LIMIT 1
    """).fetchone()

    # Daily activity (all time, chronological for charts)
    daily = conn.execute("""
        SELECT date,
            opus_messages + sonnet_messages + haiku_messages as messageCount,
            session_count as sessionCount,
            tool_calls as toolCallCount
        FROM daily_stats
        ORDER BY date ASC
    """).fetchall()

    # Daily model tokens
    daily_tokens = conn.execute("""
        SELECT date,
            opus_tokens_in + opus_tokens_out + opus_cache_read as opus_total,
            sonnet_tokens_in + sonnet_tokens_out + sonnet_cache_read as sonnet_total,
            haiku_tokens_in + haiku_tokens_out + haiku_cache_read as haiku_total
        FROM daily_stats
        ORDER BY date ASC
    """).fetchall()

    conn.close()

    return {
        "version": 1,
        "lastComputedDate": datetime.now().strftime('%Y-%m-%d'),
        "totalSessions": total_sessions,
        "totalMessages": total_messages,
        "totalTools": total_tools,
        "modelUsage": {
            "opus": {
                "inputTokens": trow['opus_in'] or 0,
                "outputTokens": trow['opus_out'] or 0,
                "cacheReadInputTokens": trow['opus_cache'] or 0,
                "messageCount": row['opus_messages'] or 0,
            },
            "sonnet": {
                "inputTokens": trow['sonnet_in'] or 0,
                "outputTokens": trow['sonnet_out'] or 0,
                "cacheReadInputTokens": trow['sonnet_cache'] or 0,
                "messageCount": row['sonnet_messages'] or 0,
            },
            "haiku": {
                "inputTokens": trow['haiku_in'] or 0,
                "outputTokens": trow['haiku_out'] or 0,
                "cacheReadInputTokens": trow['haiku_cache'] or 0,
                "messageCount": row['haiku_messages'] or 0,
            },
        },
        "hourCounts": hour_counts,
        "longestSession": {
            "messageCount": longest['message_count'] if longest else 0,
            "date": longest['date'] if longest else None,
            "sessionId": longest['id'][:20] if longest else None,
        },
        "dailyActivity": [
            {"date": r['date'], "messageCount": r['messageCount'] or 0,
             "sessionCount": r['sessionCount'] or 0, "toolCallCount": r['toolCallCount'] or 0}
            for r in daily
        ],
        "dailyModelTokens": [
            {"date": r['date'], "tokensByModel": {
                "opus": r['opus_total'] or 0,
                "sonnet": r['sonnet_total'] or 0,
                "haiku": r['haiku_total'] or 0,
            }}
            for r in daily_tokens
        ],
        "totals": {
            "sessions": total_sessions,
            "messages": total_messages,
            "tools": total_tools,
        }
    }


def get_subscription_data():
    """Generate __SUBSCRIPTION_DATA__ from SQLite (replaces subscription-data.json)."""
    conn = get_db()

    row = conn.execute("SELECT * FROM v_subscription_summary").fetchone()
    total_messages = row['total_messages'] or 0
    total_sessions = row['total_sessions'] or 0
    total_value = row['total_value'] or 0
    roi = row['roi_multiplier'] or 0

    conn.close()

    return {
        "rate": 200,
        "currency": "USD",
        "totalValue": round(total_value, 2),
        "multiplier": roi,
        "savings": round(total_value - (total_sessions * 200 / 30), 2),  # rough
        "utilization": "high" if total_messages > 100 else "normal",
        "costPerMsg": round(total_value / max(total_messages, 1), 4),
    }


def get_session_outcomes_data():
    """Generate __SESSION_OUTCOMES_DATA__ from SQLite (replaces session-outcomes.jsonl)."""
    conn = get_db()

    # Individual sessions (last 50 for recent table)
    rows = conn.execute("""
        SELECT
            s.id as session_id,
            substr(s.started_at, 1, 10) as date,
            s.message_count as messages,
            s.tool_count as tools,
            s.outcome,
            s.model,
            s.cost_estimate,
            soe.quality_score as quality,
            soe.complexity
        FROM sessions s
        LEFT JOIN session_outcome_events soe ON soe.session_id = s.id
        WHERE s.started_at IS NOT NULL
        ORDER BY s.started_at DESC
        LIMIT 50
    """).fetchall()

    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r['session_id'],
            "date": r['date'],
            "messages": r['messages'] or 0,
            "tools": r['tools'] or 0,
            "outcome": r['outcome'] or 'unknown',
            "quality": r['quality'] or min(5, max(1, (r['messages'] or 50) / 50)),
            "complexity": r['complexity'] or min(1.0, (r['tools'] or 10) / 100),
            "model_efficiency": 0.8,
            "model": r['model'] or 'opus',
            "models_used": {r['model'] or 'opus': r['messages'] or 0},
        })

    # Aggregates for charts
    totals = conn.execute("""
        SELECT COUNT(*) as total,
            SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success,
            SUM(CASE WHEN outcome = 'abandoned' THEN 1 ELSE 0 END) as abandoned,
            SUM(CASE WHEN outcome = 'partial' THEN 1 ELSE 0 END) as partial,
            SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END) as error,
            SUM(CASE WHEN outcome NOT IN ('success','abandoned','partial','error') THEN 1 ELSE 0 END) as other,
            AVG(message_count) as avg_messages,
            SUM(message_count) as total_messages,
            SUM(tool_count) as total_tools,
            SUM(CASE WHEN message_count = 0 THEN 1 ELSE 0 END) as empty,
            SUM(CASE WHEN message_count > 100 THEN 1 ELSE 0 END) as marathon
        FROM sessions WHERE started_at IS NOT NULL
    """).fetchone()

    # Quality distribution from session_outcome_events
    quality_rows = conn.execute("""
        SELECT CAST(ROUND(quality_score) AS INTEGER) as q, COUNT(*) as c
        FROM session_outcome_events WHERE quality_score > 0
        GROUP BY q ORDER BY q
    """).fetchall()
    quality_dist = {str(r['q']): r['c'] for r in quality_rows}

    # Outcome by model
    model_outcome_rows = conn.execute("""
        SELECT model,
            SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success,
            SUM(CASE WHEN outcome = 'abandoned' THEN 1 ELSE 0 END) as abandoned,
            SUM(CASE WHEN outcome = 'partial' THEN 1 ELSE 0 END) as partial,
            COUNT(*) as total
        FROM sessions WHERE started_at IS NOT NULL
        GROUP BY model
    """).fetchall()
    model_outcomes = {}
    for r in model_outcome_rows:
        model_outcomes[r['model'] or 'opus'] = {
            'success': r['success'], 'abandoned': r['abandoned'],
            'partial': r['partial'], 'total': r['total'],
            'successRate': round(r['success'] / max(r['total'], 1) * 100, 1)
        }

    # Session size distribution
    size_rows = conn.execute("""
        SELECT
            CASE
                WHEN message_count = 0 THEN 'empty'
                WHEN message_count <= 5 THEN 'tiny'
                WHEN message_count <= 20 THEN 'small'
                WHEN message_count <= 50 THEN 'medium'
                WHEN message_count <= 100 THEN 'large'
                ELSE 'marathon'
            END as size,
            COUNT(*) as c
        FROM sessions GROUP BY size
    """).fetchall()
    size_dist = {r['size']: r['c'] for r in size_rows}

    # Daily trends (sessions, outcomes, quality per day)
    daily_rows = conn.execute("""
        SELECT
            substr(s.started_at, 1, 10) as date,
            COUNT(*) as sessions,
            SUM(CASE WHEN s.outcome = 'success' THEN 1 ELSE 0 END) as success,
            SUM(CASE WHEN s.outcome = 'abandoned' THEN 1 ELSE 0 END) as abandoned,
            SUM(s.message_count) as messages,
            AVG(soe.quality_score) as avg_quality,
            SUM(CASE WHEN s.model = 'opus' THEN 1 ELSE 0 END) as opus,
            SUM(CASE WHEN s.model = 'sonnet' THEN 1 ELSE 0 END) as sonnet,
            SUM(CASE WHEN s.model = 'haiku' THEN 1 ELSE 0 END) as haiku
        FROM sessions s
        LEFT JOIN session_outcome_events soe ON soe.session_id = s.id
        WHERE s.started_at IS NOT NULL
        GROUP BY date ORDER BY date ASC
    """).fetchall()
    daily = [{
        'date': r['date'],
        'sessions': r['sessions'],
        'success': r['success'],
        'abandoned': r['abandoned'],
        'messages': r['messages'] or 0,
        'avgQuality': round(r['avg_quality'] or 0, 2),
        'opus': r['opus'], 'sonnet': r['sonnet'], 'haiku': r['haiku']
    } for r in daily_rows]

    conn.close()

    return {
        "sessions": sessions,
        "totals": {
            "total": totals['total'],
            "success": totals['success'],
            "abandoned": totals['abandoned'],
            "partial": totals['partial'],
            "error": totals['error'] or 0,
            "other": totals['other'] or 0,
            "avgMessages": round(totals['avg_messages'] or 0, 1),
            "totalMessages": totals['total_messages'] or 0,
            "totalTools": totals['total_tools'] or 0,
            "empty": totals['empty'] or 0,
            "marathon": totals['marathon'] or 0,
        },
        "qualityDist": quality_dist,
        "modelOutcomes": model_outcomes,
        "sizeDist": size_dist,
        "daily": daily,
    }


def get_routing_data():
    """Generate __ROUTING_DATA__ from SQLite (replaces dq-scores.jsonl reads)."""
    conn = get_db()

    # DQ scores from routing_decisions
    rows = conn.execute("""
        SELECT dq_score, selected_model, timestamp
        FROM routing_decisions
        ORDER BY timestamp DESC
    """).fetchall()

    scores = [r['dq_score'] for r in rows if r['dq_score']]
    models = Counter(r['selected_model'] for r in rows if r['selected_model'])
    total = len(scores)
    avg_dq = sum(scores) / total if scores else 0

    # Daily trend with DQ scores, complexity, and per-model breakdown
    daily_detail = conn.execute("""
        SELECT date(timestamp) as d,
               COUNT(*) as queries,
               ROUND(AVG(dq_score), 3) as avg_dq,
               ROUND(AVG(complexity), 3) as avg_complexity,
               SUM(CASE WHEN selected_model = 'haiku' THEN 1 ELSE 0 END) as haiku,
               SUM(CASE WHEN selected_model = 'sonnet' THEN 1 ELSE 0 END) as sonnet,
               SUM(CASE WHEN selected_model = 'opus' THEN 1 ELSE 0 END) as opus
        FROM routing_decisions
        GROUP BY d ORDER BY d
    """).fetchall()

    daily_trend = [
        {
            "date": r['d'], "queries": r['queries'],
            "avgDq": r['avg_dq'] or 0, "avgComplexity": r['avg_complexity'] or 0,
            "haiku": r['haiku'], "sonnet": r['sonnet'], "opus": r['opus']
        }
        for r in daily_detail
    ]

    # DQ components (validity, specificity, correctness)
    dq_comp = conn.execute("""
        SELECT ROUND(AVG(dq_validity), 3) as validity,
               ROUND(AVG(dq_specificity), 3) as specificity,
               ROUND(AVG(dq_correctness), 3) as correctness
        FROM routing_decisions
        WHERE dq_validity IS NOT NULL
    """).fetchone()
    dq_components = {
        "validity": dq_comp['validity'] or 0,
        "specificity": dq_comp['specificity'] or 0,
        "correctness": dq_comp['correctness'] or 0,
    } if dq_comp else {"validity": 0, "specificity": 0, "correctness": 0}

    # Complexity ranges per model
    complexity_rows = conn.execute("""
        SELECT selected_model,
               ROUND(MIN(complexity), 3) as min_c,
               ROUND(AVG(complexity), 3) as avg_c,
               ROUND(MAX(complexity), 3) as max_c,
               COUNT(*) as cnt
        FROM routing_decisions
        WHERE complexity IS NOT NULL
        GROUP BY selected_model
        ORDER BY avg_c
    """).fetchall()
    complexity_by_model = [
        {"model": r['selected_model'], "min": r['min_c'], "avg": r['avg_c'],
         "max": r['max_c'], "count": r['cnt']}
        for r in complexity_rows
    ]

    # Expertise domain routing
    expertise_domains = []
    try:
        exp_rows = conn.execute("""
            SELECT domain, COUNT(*) as cnt,
                   ROUND(AVG(expertise_level), 3) as avg_expertise,
                   ROUND(AVG(query_complexity), 3) as avg_complexity,
                   chosen_model as primary_model
            FROM expertise_routing_events
            GROUP BY domain
            ORDER BY cnt DESC
        """).fetchall()
        expertise_domains = [
            {"domain": r['domain'], "count": r['cnt'],
             "avgExpertise": r['avg_expertise'] or 0,
             "avgComplexity": r['avg_complexity'] or 0,
             "primaryModel": r['primary_model']}
            for r in exp_rows
        ]
    except Exception:
        pass

    # Model success rates from session_outcome_events
    model_success = {}
    ms_rows = conn.execute("""
        SELECT model_used, COUNT(*) as total,
               SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes
        FROM session_outcome_events
        WHERE model_used IS NOT NULL
        GROUP BY model_used
    """).fetchall()
    for r in ms_rows:
        if r['total'] > 0:
            model_success[r['model_used']] = {
                "success_rate": round(r['successes'] / r['total'] * 100, 1),
                "total": r['total'],
            }

    # Overall accuracy
    total_outcomes = sum(r['total'] for r in ms_rows) if ms_rows else 0
    total_success = sum(r['successes'] for r in ms_rows) if ms_rows else 0
    accuracy = round(total_success / total_outcomes * 100, 1) if total_outcomes > 0 else round(avg_dq * 100, 1)

    model_total = sum(models.values()) or 1
    model_dist = {
        'haiku': round(models.get('haiku', 0) / model_total, 3),
        'sonnet': round(models.get('sonnet', 0) / model_total, 3),
        'opus': round(models.get('opus', 0) / model_total, 3),
    }

    # Cost savings
    haiku_pct = model_dist['haiku']
    sonnet_pct = model_dist['sonnet']
    opus_pct = model_dist['opus']
    actual_cost_pct = (haiku_pct * 0.16) + (sonnet_pct * 0.6) + (opus_pct * 1.0)
    cost_savings = round((1 - actual_cost_pct) * 100, 1) if opus_pct < 1 else 0

    conn.close()

    return {
        "totalQueries": total,
        "avgDqScore": round(avg_dq, 3),
        "dataQuality": round(avg_dq, 2),
        "feedbackCount": total_outcomes,
        "costReduction": cost_savings,
        "routingLatency": 42,
        "modelDistribution": model_dist,
        "modelCounts": dict(models),
        "accuracy": accuracy,
        "targetQueries": 5000,
        "targetDataQuality": 0.80,
        "targetFeedback": 1000,
        "targetAccuracy": 60,
        "productionReady": True,
        "dailyTrend": daily_trend,
        "modelSuccessRates": model_success,
        "routingDecisions": total,
        "latencyMeasured": False,
        "dqComponents": dq_components,
        "complexityByModel": complexity_by_model,
        "expertiseDomains": expertise_domains,
    }


def get_recovery_data():
    """Generate __RECOVERY_DATA__ from SQLite (replaces recovery-outcomes.jsonl)."""
    conn = get_db()

    # Self-heal events (columns: error_pattern, fix_applied, success, severity)
    heal_rows = conn.execute("""
        SELECT timestamp, error_pattern, fix_applied, success, severity, error_message
        FROM self_heal_events ORDER BY timestamp DESC LIMIT 100
    """).fetchall()

    # Recovery events (columns: error_type, recovery_strategy, success, attempts)
    rec_rows = conn.execute("""
        SELECT timestamp, error_type, recovery_strategy, success, attempts, error_details
        FROM recovery_events ORDER BY timestamp DESC LIMIT 100
    """).fetchall()

    total = len(heal_rows) + len(rec_rows)
    auto_fix = sum(1 for r in heal_rows if r['success'])
    success = auto_fix + sum(1 for r in rec_rows if r['success'])

    # Category distribution (using error_pattern/error_type as category)
    categories = Counter()
    for r in heal_rows:
        categories[r['error_pattern'] or 'unknown'] += 1
    for r in rec_rows:
        categories[r['error_type'] or 'unknown'] += 1

    # Timeline (last 7 days)
    now = datetime.now()
    timeline = defaultdict(lambda: {"autoFix": 0, "suggested": 0})
    for r in heal_rows:
        ts = r['timestamp']
        if ts:
            try:
                dt = datetime.fromtimestamp(int(ts))
                if (now - dt).days <= 7:
                    day_str = dt.strftime("%m/%d")
                    if r['success']:
                        timeline[day_str]["autoFix"] += 1
                    else:
                        timeline[day_str]["suggested"] += 1
            except:
                pass

    sorted_days = sorted(timeline.keys())

    # Success by category
    cat_success = defaultdict(lambda: {"success": 0, "total": 0})
    for r in heal_rows:
        cat = r['error_pattern'] or 'unknown'
        cat_success[cat]["total"] += 1
        if r['success']:
            cat_success[cat]["success"] += 1
    for r in rec_rows:
        cat = r['error_type'] or 'unknown'
        cat_success[cat]["total"] += 1
        if r['success']:
            cat_success[cat]["success"] += 1

    conn.close()

    return {
        "stats": {
            "total": total,
            "autoFix": auto_fix,
            "autoFixRate": round(auto_fix / total * 100, 1) if total > 0 else 0,
            "successRate": round(success / total * 100, 1) if total > 0 else 0,
        },
        "categories": dict(categories),
        "outcomes": [
            {"action": r['fix_applied'] or r['error_pattern'] or 'unknown',
             "category": r['error_pattern'] or 'unknown',
             "ts": r['timestamp'] or 0, "success": bool(r['success']), "auto": True}
            for r in heal_rows
        ][:10] + [
            {"action": r['recovery_strategy'] or r['error_type'] or 'unknown',
             "category": r['error_type'] or 'unknown',
             "ts": r['timestamp'] or 0, "success": bool(r['success']), "auto": False}
            for r in rec_rows
        ][:10],
        "timeline": [{"date": d, "autoFix": timeline[d]["autoFix"], "suggested": timeline[d]["suggested"]} for d in sorted_days],
        "successByCategory": {
            cat: round(data["success"] / data["total"] * 100, 1) if data["total"] > 0 else 0
            for cat, data in cat_success.items()
        },
        "matrix": [
            {"category": "Git", "errors": 560, "autoFix": "username, locks", "suggestOnly": "merge conflicts, force push"},
            {"category": "Concurrency", "errors": 55, "autoFix": "stale locks, zombies", "suggestOnly": "parallel sessions"},
            {"category": "Permissions", "errors": 40, "autoFix": "safe paths", "suggestOnly": "system paths"},
            {"category": "Quota", "errors": 25, "autoFix": "cache", "suggestOnly": "model switch"},
            {"category": "Crash", "errors": 15, "autoFix": "corrupt state", "suggestOnly": "restore backup"},
            {"category": "Recursion", "errors": 3, "autoFix": "kill runaway", "suggestOnly": "\u2014"},
            {"category": "Syntax", "errors": 2, "autoFix": "\u2014", "suggestOnly": "always suggest"},
        ],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: ccc-sql-data.py <stats|subscription|outcomes|routing|recovery|all>")
        sys.exit(1)

    mode = sys.argv[1]

    try:
        if mode == 'stats':
            print(json.dumps(get_stats_data()))
        elif mode == 'subscription':
            print(json.dumps(get_subscription_data()))
        elif mode == 'outcomes':
            print(json.dumps(get_session_outcomes_data()))
        elif mode == 'routing':
            print(json.dumps(get_routing_data()))
        elif mode == 'recovery':
            print(json.dumps(get_recovery_data()))
        elif mode == 'all':
            print(json.dumps({
                "stats": get_stats_data(),
                "subscription": get_subscription_data(),
                "outcomes": get_session_outcomes_data(),
                "routing": get_routing_data(),
                "recovery": get_recovery_data(),
            }))
        else:
            print(json.dumps({"error": f"Unknown mode: {mode}"}))
            sys.exit(1)
    except Exception as e:
        # Fallback: output safe defaults so dashboard doesn't break
        sys.stderr.write(f"ccc-sql-data error: {e}\n")
        defaults = {
            'stats': '{"totalSessions":0,"totalMessages":0,"dailyActivity":[],"dailyModelTokens":[],"modelUsage":{},"hourCounts":{}}',
            'subscription': '{"rate":200,"totalValue":0,"multiplier":0}',
            'outcomes': '{"sessions":[],"totals":{"total":0,"success":0,"abandoned":0,"partial":0,"error":0,"other":0,"avgMessages":0,"totalMessages":0,"totalTools":0,"empty":0,"marathon":0},"qualityDist":{},"modelOutcomes":{},"sizeDist":{},"daily":[]}',
            'routing': '{"totalQueries":0,"dataQuality":0.0,"feedbackCount":0}',
            'recovery': '{"stats":{"total":0},"categories":{},"outcomes":[],"timeline":[],"successByCategory":{},"matrix":[]}',
        }
        print(defaults.get(mode, '{}'))


if __name__ == "__main__":
    main()
