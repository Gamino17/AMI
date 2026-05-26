"""Backup automático de la SQLite del STATE.

Diseño:
- Thread daemon que cada `AMI_BACKUP_INTERVAL_S` (default 3600) snapshot-ea
  la DB usando `sqlite3.Connection.backup()` (atómico, online, sin parar el
  proceso).
- Snapshots en `AMI_BACKUP_DIR` con nombre `ami_state.YYYY-MM-DD_HHMM.db`.
- Rotación: borra snapshots > `AMI_BACKUP_KEEP_DAYS` días (default 7).
- Si AMI_DB_PATH es ":memory:" o ami_storage está deshabilitado, no hace
  nada (tests, dev sin persistencia).
- Upload offsite opcional a S3 (o S3-compatible: Backblaze B2, Wasabi,
  Cloudflare R2) usando SigV4 firmado a mano con stdlib. Sin boto3.
  Se activa configurando AMI_BACKUP_S3_BUCKET + credenciales. Sin esas envs
  el flujo es exactamente el original (solo filesystem local).
"""
from __future__ import annotations
import hashlib
import hmac
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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

    result = {
        "ok": True,
        "path": dst,
        "bytes": size,
        "kept": len(kept),
        "removed": removed,
        "ts": ts,
    }

    # Upload offsite si está configurado. No bloquea el ok del snapshot local.
    if _s3_configured():
        s3_res = _upload_to_s3(dst)
        result["s3"] = s3_res
        if not s3_res.get("ok"):
            print(f"[ami_backup] s3 upload failed: {s3_res.get('error') or s3_res.get('reason')}",
                  file=sys.stderr)

    return result


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
    import ami_log
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
                ami_log.info(
                    "backup_done",
                    path=r["path"],
                    bytes=r["bytes"],
                    kept=r["kept"],
                    removed=r["removed"],
                )
            else:
                ami_log.info("backup_skipped", reason=r.get("reason"))
        except Exception as e:
            ami_log.error("backup_loop_failed", error=str(e), exc_type=type(e).__name__)


def stats() -> dict:
    """Snapshot del estado del módulo, útil en /v1/health."""
    if not _enabled():
        return {"enabled": False}
    backups = _list_backups(_backup_dir())
    out = {
        "enabled": True,
        "dir": _backup_dir(),
        "count": len(backups),
        "latest": backups[-1] if backups else None,
    }
    if _s3_configured():
        out["s3"] = {
            "configured": True,
            "bucket": os.environ.get("AMI_BACKUP_S3_BUCKET"),
            "region": os.environ.get("AMI_BACKUP_S3_REGION") or "eu-west-1",
            "prefix": os.environ.get("AMI_BACKUP_S3_PREFIX") or "ami-backups/",
            "endpoint": os.environ.get("AMI_BACKUP_S3_ENDPOINT") or None,
        }
    else:
        out["s3"] = {"configured": False}
    return out


# ----------------------------------------------------------------------------
# S3 offsite upload (stdlib pura, SigV4 firmado a mano).
# Sin boto3 ni botocore: solo urllib + hmac + hashlib.
# Compatible con AWS S3 y S3-compatible (Backblaze B2, Wasabi, Cloudflare R2)
# vía AMI_BACKUP_S3_ENDPOINT.
# ----------------------------------------------------------------------------
def _s3_configured() -> bool:
    return bool(
        os.environ.get("AMI_BACKUP_S3_BUCKET")
        and os.environ.get("AMI_BACKUP_AWS_ACCESS_KEY_ID")
        and os.environ.get("AMI_BACKUP_AWS_SECRET_ACCESS_KEY")
    )


def _sigv4_signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    """Deriva la clave de firma SigV4 (HMAC encadenado: date → region → service → "aws4_request")."""
    k_date = hmac.new(("AWS4" + secret).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def _s3_endpoint_url(bucket: str, region: str, key: str) -> tuple[str, str]:
    """Resuelve (url, host) para PUT al bucket/key.

    Si AMI_BACKUP_S3_ENDPOINT está seteado, usa path-style (más compatible
    con S3-compatible providers). Si no, virtual-hosted style sobre AWS.
    """
    endpoint = os.environ.get("AMI_BACKUP_S3_ENDPOINT")
    # Path encoding: dejar "/" en el path pero escapar el resto.
    encoded_key = urllib.parse.quote(key, safe="/")
    if endpoint:
        # Path-style: https://endpoint/{bucket}/{key}
        parsed = urllib.parse.urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
        host = parsed.netloc
        scheme = parsed.scheme or "https"
        base_path = (parsed.path or "").rstrip("/")
        url = f"{scheme}://{host}{base_path}/{bucket}/{encoded_key}"
        return url, host
    host = f"{bucket}.s3.{region}.amazonaws.com"
    url = f"https://{host}/{encoded_key}"
    return url, host


def _upload_to_s3(local_path: str) -> dict:
    """Sube `local_path` al bucket S3 configurado vía PUT firmado con SigV4.

    Devuelve `{ok, status, etag, url}` o `{ok: False, reason|error}`.
    """
    if not _s3_configured():
        return {"ok": False, "reason": "not_configured"}

    bucket = os.environ["AMI_BACKUP_S3_BUCKET"]
    region = os.environ.get("AMI_BACKUP_S3_REGION") or "eu-west-1"
    prefix = os.environ.get("AMI_BACKUP_S3_PREFIX") or "ami-backups/"
    access_key = os.environ["AMI_BACKUP_AWS_ACCESS_KEY_ID"]
    secret_key = os.environ["AMI_BACKUP_AWS_SECRET_ACCESS_KEY"]

    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"

    filename = os.path.basename(local_path)
    key = f"{prefix}{filename}"

    try:
        with open(local_path, "rb") as f:
            payload = f.read()
    except OSError as e:
        return {"ok": False, "error": f"read_failed: {e}"}

    payload_hash = hashlib.sha256(payload).hexdigest()

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    url, host = _s3_endpoint_url(bucket, region, key)
    parsed_url = urllib.parse.urlparse(url)
    canonical_uri = parsed_url.path or "/"
    canonical_querystring = ""  # PUT sin query params

    # Headers a firmar. SigV4 exige host + x-amz-content-sha256 + x-amz-date,
    # ordenados lexicográficamente y lowercase.
    signed_headers_map = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    sorted_header_names = sorted(signed_headers_map.keys())
    canonical_headers = "".join(
        f"{name}:{signed_headers_map[name].strip()}\n" for name in sorted_header_names
    )
    signed_headers = ";".join(sorted_header_names)

    canonical_request = "\n".join([
        "PUT",
        canonical_uri,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    service = "s3"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _sigv4_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Host", host)
    req.add_header("x-amz-content-sha256", payload_hash)
    req.add_header("x-amz-date", amz_date)
    req.add_header("Authorization", authorization)
    req.add_header("Content-Length", str(len(payload)))

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            etag = resp.headers.get("ETag", "").strip('"')
            return {"ok": 200 <= status < 300, "status": status, "etag": etag, "url": url}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        return {"ok": False, "error": f"http_{e.code}: {body}", "status": e.code, "url": url}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"ok": False, "error": f"transport: {e}", "url": url}
