"""Tests for the database backup script."""

import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from importlib.util import spec_from_file_location, module_from_spec

BACKUP_SCRIPT = Path(__file__).parent.parent / "scripts" / "ccc-backup.py"


def load_backup_module():
    spec = spec_from_file_location("ccc_backup", str(BACKUP_SCRIPT))
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBackup:
    @pytest.fixture
    def backup_mod(self):
        return load_backup_module()

    def test_backup_creates_file(self, backup_mod, tmp_db, tmp_path):
        dest = tmp_path / "backups"
        result = backup_mod.backup_database(tmp_db, dest, keep=7)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_backup_is_valid_sqlite(self, backup_mod, tmp_db, tmp_path):
        dest = tmp_path / "backups"
        result = backup_mod.backup_database(tmp_db, dest, keep=7)
        conn = sqlite3.connect(str(result))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        assert len(tables) > 0

    def test_backup_rotation(self, backup_mod, tmp_db, tmp_path):
        dest = tmp_path / "backups"
        dest.mkdir(parents=True, exist_ok=True)
        # Create 5 fake old backups manually
        import time
        for i in range(5):
            p = dest / f"claude-db-2026-02-0{i+1}-120000.db"
            p.write_bytes(b"fake")
            # Stagger mtime so sorting works
            import os
            os.utime(p, (time.time() - (5 - i) * 3600, time.time() - (5 - i) * 3600))
        # Now backup — should rotate down to keep=3
        backup_mod.backup_database(tmp_db, dest, keep=3)
        backups = list(dest.glob("claude-db-*.db"))
        assert len(backups) == 3

    def test_backup_creates_dest_dir(self, backup_mod, tmp_db, tmp_path):
        dest = tmp_path / "new" / "backup" / "dir"
        result = backup_mod.backup_database(tmp_db, dest, keep=7)
        assert dest.exists()
        assert result.exists()
