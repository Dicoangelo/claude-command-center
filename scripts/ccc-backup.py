#!/usr/bin/env python3
"""Backup the Claude Command Center SQLite database.

Uses SQLite's built-in backup API for WAL-consistent snapshots.
Retains the last N daily backups (default: 7).

Usage:
    python3 ccc-backup.py              # Backup with defaults
    python3 ccc-backup.py --keep 14    # Keep 14 backups
    python3 ccc-backup.py --dest /tmp  # Custom destination
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def backup_database(src: Path, dest_dir: Path, keep: int = 7) -> Path:
    """Backup SQLite database using the backup API.

    Args:
        src: Source database path.
        dest_dir: Destination directory for backups.
        keep: Number of backups to retain.

    Returns:
        Path to the new backup file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup_path = dest_dir / f"claude-db-{timestamp}.db"

    # Use SQLite backup API for WAL consistency
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(backup_path))
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    # Rotate old backups
    backups = sorted(dest_dir.glob("claude-db-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink()
        print(f"  Removed old backup: {old.name}")

    return backup_path


def main():
    parser = argparse.ArgumentParser(description="Backup Claude Command Center database")
    parser.add_argument("--keep", type=int, default=7, help="Number of backups to retain (default: 7)")
    parser.add_argument("--dest", type=str, default=None, help="Backup destination directory")
    args = parser.parse_args()

    src = Path.home() / ".claude" / "data" / "claude.db"
    if not src.exists():
        print(f"Error: Database not found at {src}", file=sys.stderr)
        sys.exit(1)

    dest_dir = Path(args.dest) if args.dest else Path.home() / ".claude" / "backups"

    print(f"Backing up: {src}")
    backup_path = backup_database(src, dest_dir, args.keep)
    size_mb = backup_path.stat().st_size / (1024 * 1024)
    print(f"Backup created: {backup_path} ({size_mb:.1f} MB)")

    remaining = len(list(dest_dir.glob("claude-db-*.db")))
    print(f"Backups retained: {remaining} (max: {args.keep})")


if __name__ == "__main__":
    main()
