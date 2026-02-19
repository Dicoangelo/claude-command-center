#!/usr/bin/env python3
"""Backfill autonomy streaks from historical tool_events data.

Analyzes 107K+ tool_events via gap analysis to find continuous
autonomous execution runs. A gap > GAP_THRESHOLD seconds between
consecutive tool calls indicates a human interruption (permission
prompt, user input, idle time).

Usage:
    python3 scripts/backfill-autonomy-streaks.py [--threshold 30] [--min-duration 60]
"""
import argparse
import json
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/.claude/data/claude.db")
GAP_THRESHOLD = 30  # seconds — gaps larger than this = interruption
MIN_DURATION = 60   # seconds — only store streaks longer than 1 minute


def get_session_for_timestamp(cur, ts):
    """Find session IDs that overlap with a given unix timestamp."""
    dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    cur.execute(
        """SELECT id FROM sessions
           WHERE started_at <= ? AND (ended_at >= ? OR ended_at IS NULL)
           LIMIT 5""",
        (dt + "Z", dt + "Z"),
    )
    return [row[0] for row in cur.fetchall()]


def compute_streaks(db_path, gap_threshold, min_duration):
    conn = sqlite3.connect(db_path, timeout=10)
    cur = conn.cursor()

    # Pull all tool events ordered by timestamp
    cur.execute("SELECT timestamp, tool_name, context FROM tool_events ORDER BY timestamp")
    rows = cur.fetchall()
    print(f"Loaded {len(rows):,} tool events")

    streaks = []
    run_start = None
    run_end = None
    run_tools = []
    run_contexts = []

    for ts, tool, ctx in rows:
        if run_start is None:
            run_start = ts
            run_end = ts
            run_tools = [tool]
            run_contexts = [ctx] if ctx else []
            continue

        gap = ts - run_end

        if 0 <= gap <= gap_threshold:
            run_end = ts
            run_tools.append(tool)
            if ctx:
                run_contexts.append(ctx)
        else:
            # End current run
            duration = run_end - run_start
            if duration >= min_duration:
                tool_freq = Counter(run_tools).most_common(10)
                project_freq = Counter(run_contexts).most_common(10)
                projects = [p for p, _ in project_freq]

                streaks.append({
                    "start_ts": run_start,
                    "end_ts": run_end,
                    "duration_seconds": duration,
                    "tool_count": len(run_tools),
                    "avg_gap_seconds": round(duration / max(len(run_tools) - 1, 1), 2),
                    "projects": projects,
                    "top_tools": dict(tool_freq),
                })

            # Start new run
            run_start = ts
            run_end = ts
            run_tools = [tool]
            run_contexts = [ctx] if ctx else []

    # Don't forget the last run
    if run_start is not None:
        duration = run_end - run_start
        if duration >= min_duration:
            tool_freq = Counter(run_tools).most_common(10)
            project_freq = Counter(run_contexts).most_common(10)
            projects = [p for p, _ in project_freq]
            streaks.append({
                "start_ts": run_start,
                "end_ts": run_end,
                "duration_seconds": duration,
                "tool_count": len(run_tools),
                "avg_gap_seconds": round(duration / max(len(run_tools) - 1, 1), 2),
                "projects": projects,
                "top_tools": dict(tool_freq),
            })

    print(f"Found {len(streaks):,} streaks >= {min_duration}s")

    # Resolve session IDs for top streaks (expensive, only do top 50)
    streaks.sort(key=lambda s: s["duration_seconds"], reverse=True)
    for s in streaks[:50]:
        s["session_ids"] = get_session_for_timestamp(cur, s["start_ts"])

    # Clear old data and insert
    conn.execute("DELETE FROM autonomy_streaks")
    for s in streaks:
        conn.execute(
            """INSERT INTO autonomy_streaks
               (start_ts, end_ts, duration_seconds, tool_count, avg_gap_seconds,
                projects, top_tools, session_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                s["start_ts"],
                s["end_ts"],
                s["duration_seconds"],
                s["tool_count"],
                s["avg_gap_seconds"],
                json.dumps(s["projects"]),
                json.dumps(s["top_tools"]),
                json.dumps(s.get("session_ids", [])),
            ),
        )
    conn.commit()

    # Print summary
    print(f"\nInserted {len(streaks):,} streaks into autonomy_streaks table")
    if streaks:
        top = streaks[0]
        dur = timedelta(seconds=top["duration_seconds"])
        dt = datetime.fromtimestamp(top["start_ts"])
        print(f"\nRECORD STREAK: {dur}")
        print(f"  Date: {dt.strftime('%Y-%m-%d %H:%M')}")
        print(f"  Tool calls: {top['tool_count']:,}")
        print(f"  Avg gap: {top['avg_gap_seconds']:.1f}s")
        print(f"  Projects: {', '.join(p.split('/')[-1] for p in top['projects'][:4])}")
        print(f"  Top tools: {top['top_tools']}")

    # Stats
    durations = [s["duration_seconds"] for s in streaks]
    total_autonomous = sum(durations)
    if rows:
        total_time = rows[-1][0] - rows[0][0]
        autonomy_rate = (total_autonomous / total_time * 100) if total_time > 0 else 0
        print(f"\nAutonomy rate: {autonomy_rate:.1f}% of active time")

    conn.close()
    return streaks


def main():
    parser = argparse.ArgumentParser(description="Backfill autonomy streaks")
    parser.add_argument("--threshold", type=int, default=GAP_THRESHOLD, help="Gap threshold in seconds")
    parser.add_argument("--min-duration", type=int, default=MIN_DURATION, help="Minimum streak duration in seconds")
    parser.add_argument("--db", default=DB_PATH, help="Database path")
    args = parser.parse_args()

    start = time.time()
    streaks = compute_streaks(args.db, args.threshold, args.min_duration)
    elapsed = time.time() - start
    print(f"\nCompleted in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
