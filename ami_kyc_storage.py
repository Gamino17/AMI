"""Persistencia de las imágenes de KYC FUERA de la SQLite key-value.

Razón: el record de KYC vive en STATE["kyc_verifications"] y se serializa
en cada save_state(). Si metiéramos las imágenes (3 × ~5 MB base64) ahí,
cada request escribiría 15-100 MB a disco — inviable.

Diseño:
- Imágenes en filesystem: AMI_KYC_IMAGE_DIR/<kyc_id>_<kind>.b64
- El record solo guarda flags `has_dni_front`, etc.
- Permisos 0o600 (solo owner). El directorio se crea con 0o700.
- Retención: las imágenes se borran tras N días según política
  (purge_expired_images). El record se conserva para audit.

Para producción ideal: pasar a S3/GCS con SSE. Por ahora filesystem local
basta y el flujo es exactamente el mismo (escribir/leer/borrar por id).
"""
from __future__ import annotations
import os
import re
import sys


_IMAGE_DIR = os.environ.get("AMI_KYC_IMAGE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "kyc_images"
)

# Memory-mode: si AMI_DB_PATH es :memory: (tests), también skip filesystem.
_MEMORY_ONLY = os.environ.get("AMI_DB_PATH") == ":memory:" or os.environ.get("AMI_KYC_DISABLE_FS") == "1"

# Cache RAM cuando _MEMORY_ONLY (tests). Map: (kyc_id, kind) -> bytes.
_RAM_STORE: dict[tuple[str, str], str] = {}

VALID_KINDS = {"dni_front", "dni_back", "selfie"}
_KYC_ID_RE = re.compile(r"^kyc_[A-Za-z0-9_-]+$")


def _ensure_dir():
    if _MEMORY_ONLY:
        return
    try:
        os.makedirs(_IMAGE_DIR, exist_ok=True)
        # Permisos restrictivos en el dir tras crearlo (chmod siempre, idempotente)
        os.chmod(_IMAGE_DIR, 0o700)
    except OSError as e:
        print(f"[ami_kyc_storage] cannot create {_IMAGE_DIR}: {e}", file=sys.stderr)


def _path(kyc_id: str, kind: str) -> str:
    # Validamos forma del kyc_id y kind para impedir path traversal.
    if not _KYC_ID_RE.match(kyc_id):
        raise ValueError(f"invalid kyc_id: {kyc_id!r}")
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind: {kind!r}")
    return os.path.join(_IMAGE_DIR, f"{kyc_id}_{kind}.b64")


def write_image(kyc_id: str, kind: str, b64: str) -> str | None:
    """Guarda la imagen y devuelve la "ruta lógica" para meter en el record."""
    if not b64:
        return None
    if _MEMORY_ONLY:
        _RAM_STORE[(kyc_id, kind)] = b64
        return f"mem://{kyc_id}/{kind}"
    _ensure_dir()
    path = _path(kyc_id, kind)
    try:
        # Open con permisos restrictivos desde el principio.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(b64)
        except Exception:
            os.close(fd)
            raise
        return path
    except OSError as e:
        print(f"[ami_kyc_storage] write failed {path}: {e}", file=sys.stderr)
        return None


def read_image(kyc_id: str, kind: str) -> str | None:
    if _MEMORY_ONLY:
        return _RAM_STORE.get((kyc_id, kind))
    try:
        path = _path(kyc_id, kind)
    except ValueError:
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def delete_image(kyc_id: str, kind: str) -> bool:
    if _MEMORY_ONLY:
        return _RAM_STORE.pop((kyc_id, kind), None) is not None
    try:
        path = _path(kyc_id, kind)
    except ValueError:
        return False
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        return False
    return False


def delete_all_for(kyc_id: str) -> int:
    """Borra las 3 imágenes asociadas a un KYC. Devuelve cuántas borró."""
    n = 0
    for kind in VALID_KINDS:
        if delete_image(kyc_id, kind):
            n += 1
    return n


def image_dir() -> str:
    return ":memory:" if _MEMORY_ONLY else _IMAGE_DIR


def stats() -> dict:
    if _MEMORY_ONLY:
        return {"backend": "memory", "count": len(_RAM_STORE)}
    if not os.path.isdir(_IMAGE_DIR):
        return {"backend": "fs", "dir": _IMAGE_DIR, "count": 0, "bytes": 0}
    n = 0; total = 0
    try:
        for name in os.listdir(_IMAGE_DIR):
            p = os.path.join(_IMAGE_DIR, name)
            if os.path.isfile(p):
                n += 1
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
    except OSError:
        pass
    return {"backend": "fs", "dir": _IMAGE_DIR, "count": n, "bytes": total}
