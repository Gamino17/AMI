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


# ----------------------------------------------------------------------------
# S3 offsite upload tests. NUNCA contactan con S3 real: interceptamos urlopen.
# ----------------------------------------------------------------------------
def _clear_s3_env(monkeypatch):
    for k in (
        "AMI_BACKUP_S3_BUCKET",
        "AMI_BACKUP_S3_REGION",
        "AMI_BACKUP_S3_PREFIX",
        "AMI_BACKUP_AWS_ACCESS_KEY_ID",
        "AMI_BACKUP_AWS_SECRET_ACCESS_KEY",
        "AMI_BACKUP_S3_ENDPOINT",
    ):
        monkeypatch.delenv(k, raising=False)


def test_upload_to_s3_not_configured(monkeypatch, tmp_path):
    _clear_s3_env(monkeypatch)
    import importlib, ami_backup
    importlib.reload(ami_backup)
    f = tmp_path / "dummy.db"
    f.write_bytes(b"x")
    res = ami_backup._upload_to_s3(str(f))
    assert res == {"ok": False, "reason": "not_configured"}


def test_upload_to_s3_signs_request_correctly(monkeypatch, tmp_path):
    """Verifica que el PUT lleva headers SigV4 con formato correcto.

    Interceptamos urllib.request.urlopen para capturar la Request sin contactar
    con S3. Validamos que la Authorization header tiene la forma
    `AWS4-HMAC-SHA256 Credential=.../.../.../s3/aws4_request, SignedHeaders=..., Signature=<64hex>`
    y que x-amz-content-sha256 == sha256(payload).
    """
    monkeypatch.setenv("AMI_BACKUP_S3_BUCKET", "my-backup-bucket")
    monkeypatch.setenv("AMI_BACKUP_S3_REGION", "eu-west-1")
    monkeypatch.setenv("AMI_BACKUP_S3_PREFIX", "ami-backups/")
    monkeypatch.setenv("AMI_BACKUP_AWS_ACCESS_KEY_ID", "AKIAFAKEFAKEFAKEFAKE")
    monkeypatch.setenv("AMI_BACKUP_AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.delenv("AMI_BACKUP_S3_ENDPOINT", raising=False)

    import importlib, ami_backup
    importlib.reload(ami_backup)

    # Archivo a subir
    payload = b"sqlite-snapshot-bytes-12345"
    f = tmp_path / "ami_state.2026-05-26_120000.db"
    f.write_bytes(payload)

    captured = {}

    class FakeResp:
        status = 200
        headers = {"ETag": '"abc123etag"'}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(ami_backup.urllib.request, "urlopen", fake_urlopen)

    res = ami_backup._upload_to_s3(str(f))

    # Resultado
    assert res["ok"] is True, res
    assert res["status"] == 200
    assert res["etag"] == "abc123etag"

    # Request básica
    assert captured["method"] == "PUT"
    assert captured["timeout"] == 30
    assert captured["data"] == payload
    assert captured["url"] == (
        "https://my-backup-bucket.s3.eu-west-1.amazonaws.com/"
        "ami-backups/ami_state.2026-05-26_120000.db"
    )

    # Headers son case-insensitive en urllib; los normalizamos.
    headers = {k.lower(): v for k, v in captured["headers"].items()}

    # x-amz-content-sha256 debe ser sha256(payload) en hex
    import hashlib as _h
    assert headers["x-amz-content-sha256"] == _h.sha256(payload).hexdigest()

    # x-amz-date formato YYYYMMDDTHHMMSSZ
    import re
    assert re.fullmatch(r"\d{8}T\d{6}Z", headers["x-amz-date"])

    # Host
    assert headers["host"] == "my-backup-bucket.s3.eu-west-1.amazonaws.com"

    # Content-Length
    assert headers["content-length"] == str(len(payload))

    # Authorization: AWS4-HMAC-SHA256 Credential=AKID/DATE/REGION/s3/aws4_request,
    # SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=<64hex>
    auth = headers["authorization"]
    m = re.fullmatch(
        r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/(s3)/aws4_request, "
        r"SignedHeaders=([a-z0-9;\-]+), Signature=([0-9a-f]{64})",
        auth,
    )
    assert m, f"Authorization header malformed: {auth}"
    assert m.group(1) == "AKIAFAKEFAKEFAKEFAKE"
    assert m.group(3) == "eu-west-1"
    # Los signed headers deben estar ordenados alfabéticamente
    assert m.group(5) == "host;x-amz-content-sha256;x-amz-date"


def test_upload_to_s3_custom_endpoint_path_style(monkeypatch, tmp_path):
    """Con AMI_BACKUP_S3_ENDPOINT (p.ej. Backblaze B2) usamos path-style."""
    monkeypatch.setenv("AMI_BACKUP_S3_BUCKET", "mybucket")
    monkeypatch.setenv("AMI_BACKUP_S3_REGION", "us-west-002")
    monkeypatch.setenv("AMI_BACKUP_S3_PREFIX", "ami/")
    monkeypatch.setenv("AMI_BACKUP_AWS_ACCESS_KEY_ID", "K000FAKE")
    monkeypatch.setenv("AMI_BACKUP_AWS_SECRET_ACCESS_KEY", "secretFAKE")
    monkeypatch.setenv("AMI_BACKUP_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com")

    import importlib, ami_backup
    importlib.reload(ami_backup)

    f = tmp_path / "ami_state.2026-05-26_120000.db"
    f.write_bytes(b"data")

    captured = {}

    class FakeResp:
        status = 200
        headers = {"ETag": '"e"'}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["host"] = dict((k.lower(), v) for k, v in req.header_items()).get("host")
        return FakeResp()

    monkeypatch.setattr(ami_backup.urllib.request, "urlopen", fake_urlopen)

    res = ami_backup._upload_to_s3(str(f))
    assert res["ok"]
    assert captured["url"] == (
        "https://s3.us-west-002.backblazeb2.com/mybucket/ami/"
        "ami_state.2026-05-26_120000.db"
    )
    assert captured["host"] == "s3.us-west-002.backblazeb2.com"


def test_snapshot_includes_s3_result_when_configured(real_db, monkeypatch):
    """snapshot_once() debe propagar el resultado del upload en result['s3']."""
    monkeypatch.setenv("AMI_BACKUP_S3_BUCKET", "bucket")
    monkeypatch.setenv("AMI_BACKUP_AWS_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("AMI_BACKUP_AWS_SECRET_ACCESS_KEY", "SECRET")
    monkeypatch.setenv("AMI_BACKUP_S3_REGION", "eu-west-1")

    import importlib, ami_backup
    importlib.reload(ami_backup)

    class FakeResp:
        status = 200
        headers = {"ETag": '"deadbeef"'}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout=None):
        return FakeResp()

    monkeypatch.setattr(ami_backup.urllib.request, "urlopen", fake_urlopen)

    r = ami_backup.snapshot_once()
    assert r["ok"], r
    assert "s3" in r
    assert r["s3"]["ok"] is True
    assert r["s3"]["status"] == 200
    assert r["s3"]["etag"] == "deadbeef"


def test_stats_reports_s3_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("AMI_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("AMI_BACKUP_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("AMI_BACKUP_S3_BUCKET", "mybucket")
    monkeypatch.setenv("AMI_BACKUP_AWS_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("AMI_BACKUP_AWS_SECRET_ACCESS_KEY", "SECRET")
    monkeypatch.setenv("AMI_BACKUP_S3_REGION", "eu-central-1")
    monkeypatch.setenv("AMI_BACKUP_S3_PREFIX", "snaps/")

    import importlib, ami_backup
    importlib.reload(ami_backup)

    s = ami_backup.stats()
    assert s["enabled"] is True
    assert s["s3"]["configured"] is True
    assert s["s3"]["bucket"] == "mybucket"
    assert s["s3"]["region"] == "eu-central-1"
    assert s["s3"]["prefix"] == "snaps/"
