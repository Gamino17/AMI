"""Tests del módulo de backup (snapshot atómico de la SQLite)."""
from __future__ import annotations
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def real_db(monkeypatch, tmp_path):
    """Activa una SQLite real (no :memory:) y un dir de backup limpio."""
    db_path = tmp_path / "ami_state.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("AMI_DB_PATH", str(db_path))
    monkeypatch.setenv("AMI_BACKUP_DIR", str(backup_dir))
    monkeypatch.delenv("AMI_DISABLE_STORAGE", raising=False)

    # Creamos una DB con una tabla y un row para que el backup tenga algo.
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO kv VALUES ('test', '{\"x\": 1}')")
    conn.commit()
    conn.close()
    return {"db": db_path, "dir": backup_dir}


def test_snapshot_creates_file(real_db):
    # Importamos aquí porque el módulo lee env vars al import
    import importlib, ami_backup
    importlib.reload(ami_backup)

    r = ami_backup.snapshot_once()
    assert r["ok"], r
    assert os.path.exists(r["path"])
    assert r["bytes"] > 0


def test_snapshot_disabled_in_memory_mode(monkeypatch):
    monkeypatch.setenv("AMI_DB_PATH", ":memory:")
    import importlib, ami_backup
    importlib.reload(ami_backup)
    r = ami_backup.snapshot_once()
    assert r["ok"] is False
    assert r["reason"] == "disabled"


def test_snapshot_missing_src(monkeypatch, tmp_path):
    db = tmp_path / "doesnt_exist.db"
    monkeypatch.setenv("AMI_DB_PATH", str(db))
    monkeypatch.setenv("AMI_BACKUP_DIR", str(tmp_path / "b"))
    import importlib, ami_backup
    importlib.reload(ami_backup)
    r = ami_backup.snapshot_once()
    assert r["ok"] is False and r["reason"] == "src_missing"


def test_rotate_removes_old_backups(real_db, monkeypatch):
    """Backups con mtime > KEEP_DAYS deben borrarse en la siguiente pasada."""
    import importlib, ami_backup
    monkeypatch.setenv("AMI_BACKUP_KEEP_DAYS", "1")
    importlib.reload(ami_backup)

    # Sembramos un "viejo" backup manualmente con mtime de hace 3 días.
    dst_dir = real_db["dir"]
    dst_dir.mkdir(parents=True, exist_ok=True)
    old = dst_dir / "ami_state.2025-01-01_0000.db"
    old.write_text("dummy")
    import time as _t
    old_mtime = _t.time() - 3 * 86400
    os.utime(str(old), (old_mtime, old_mtime))

    # Snapshot nuevo → la rotación debe borrar el viejo
    r = ami_backup.snapshot_once()
    assert r["ok"]
    assert r["removed"] >= 1
    assert not old.exists()
    assert os.path.exists(r["path"])


def test_stats_disabled_returns_flag(monkeypatch):
    monkeypatch.setenv("AMI_DB_PATH", ":memory:")
    import importlib, ami_backup
    importlib.reload(ami_backup)
    assert ami_backup.stats() == {"enabled": False}
