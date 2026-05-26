"""Backup automático de la SQLite del STATE.

Diseño:
- Thread daemon que cada `AMI_BACKUP_INTERVAL_S` (default 3600) snapshot-ea
  la DB usando `sqlite3.Connection.backup()` (atómico, online, sin parar el
  proceso).
- Snapshots en `AMI_BACKUP_DIR` con nombre `ami_state.YYYY-MM-DD_HHMM.db`.
- Rotación: borra snapshots > `AMI_BACKUP_KEEP_DAYS` días (default 7).
- Si AMI_DB_PATH es ":memory:" o ami_storage está deshabilitado, no hace
  nada (tests, dev sin persistencia).
- Sin S3/GCS aún. El disk persistente de Render cubre el caso v1. v2 podemos
  añadir upload offsite cuando importe.
"""
from __future__ import annotations
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone


def _enabled() -> bool:
    db = os.environ.get("AMI_DB_PATH") or ""
    if db == ":memory:" or os.environ.get("AMI_DISABLE_STORAGE") == "1":
        return False
    return True


def _backup_dir() -> str:
    return os.environ.get("AMI_BACKUP_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "backups"
    )


def _ensure_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        return True
    except OSError as e:
        print(f"[ami_backup] cannot create {path}: {e}", file=sys.stderr)
        return False


def snapshot_once() -> dict:
    """Hace un snapshot atómico de la SQLite y rota viejos. Idempotente.

    Devuelve `{ok, path, bytes, kept, removed}` para que el caller / tests
    puedan auditar.
    """
    if not _enabled():
        return {"ok": False, "reason": "disabled"}

    src = os.environ.get("AMI_DB_PATH")
    if not src or not os.path.exists(src):
        return {"ok": False, "reason": "src_missing"}

    dst_dir = _backup_dir()
    if not _ensure_dir(dst_dir):
        return {"ok": False, "reason": "dst_dir_unwritable"}

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    dst = os.path.join(dst_dir, f"ami_state.{ts}.db")

    try:
        # sqlite3 backup API es lo correcto aquí: copia atómica
        # respetando WAL y locks. No funciona con shutil.copy.
        src_conn = sqlite3.connect(src)
        try:
            dst_conn = sqlite3.connect(dst)
            try:
                with dst_conn:
                    src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        os.chmod(dst, 0o600)
    except (sqlite3.Error, OSError) as e:
        print(f"[ami_backup ERROR] backup failed: {e}", file=sys.stderr)
        return {"ok": False, "reason": "backup_failed", "error": str(e)}

    size = 0
    try:
        size = os.path.getsize(dst)
    except OSError:
        pass

    removed = _rotate(dst_dir)
    kept = _list_backups(dst_dir)

    return {
        "ok": True,
        "path": dst,
        "bytes": size,
        "kept": len(kept),
        "removed": removed,
        "ts": ts,
    }


def _list_backups(dst_dir: str) -> list[str]:
    try:
        return sorted([n for n in os.listdir(dst_dir)
                       if n.startswith("ami_state.") and n.endswith(".db")])
    except OSError:
        return []


def _rotate(dst_dir: str) -> int:
    """Borra snapshots con mtime > KEEP_DAYS. Devuelve nº borrados."""
    try:
        keep_days = int(os.environ.get("AMI_BACKUP_KEEP_DAYS") or 7)
    except ValueError:
        keep_days = 7
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for name in _list_backups(dst_dir):
        path = os.path.join(dst_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def backup_loop():
    """Daemon: snapshot cada AMI_BACKUP_INTERVAL_S. Útil arrancar en main."""
    if not _enabled():
        return
    try:
        interval = int(os.environ.get("AMI_BACKUP_INTERVAL_S") or 3600)
    except ValueError:
        interval = 3600
    # Wait al inicio para no snapshot-ear inmediatamente al boot.
    while True:
        try:
            time.sleep(interval)
            r = snapshot_once()
            if r.get("ok"):
                print(f"AMI backup: {r['path']} ({r['bytes']} bytes, kept {r['kept']}, removed {r['removed']})")
            else:
                print(f"AMI backup skipped: {r.get('reason')}")
        except Exception as e:
            print(f"AMI backup loop ERROR: {e}", file=sys.stderr)


def stats() -> dict:
    """Snapshot del estado del módulo, útil en /v1/health."""
    if not _enabled():
        return {"enabled": False}
    backups = _list_backups(_backup_dir())
    return {
        "enabled": True,
        "dir": _backup_dir(),
        "count": len(backups),
        "latest": backups[-1] if backups else None,
    }
