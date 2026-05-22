"""Persistencia del STATE en SQLite.

Diseño: una sola tabla `kv` que guarda cada entidad como JSON. SQLite vive
en `AMI_DB_PATH` (default `./ami_state.db`). Para v1 es suficiente — no
necesitamos queries complejas, solo round-trip a disco para sobrevivir
reinicios del proceso.

Carga: al arrancar el módulo ami_api, si la DB existe, se reconstruye
STATE en memoria. Cualquier mutación del STATE pasa por save_state() que
escribe el snapshot completo. Esto es O(N) por escritura pero N es del
orden de cientos para v1 — gana simplicidad sobre per-entity writes.

Para escalar a miles de MIDs hay que pasar a una tabla por entidad y
escrituras incrementales. Eso es v2.
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
from typing import Any


_DB_PATH = os.environ.get("AMI_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ami_state.db"
)

# In-memory mode: si AMI_DB_PATH=":memory:" o vacío, la persistencia se
# deshabilita (mismo comportamiento que v1 pre-storage). Los tests
# importan ami_api en modo memory.
_MEMORY_ONLY = os.environ.get("AMI_DB_PATH") == ":memory:" or os.environ.get("AMI_DISABLE_STORAGE") == "1"

_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    existed = os.path.exists(_DB_PATH)
    conn = sqlite3.connect(_DB_PATH, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")          # mejor para escrituras concurrentes
    conn.execute("PRAGMA synchronous=NORMAL")        # buen balance durabilidad/velocidad
    conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    # Si acabamos de crear el archivo, restringimos permisos: la DB contiene
    # hashes de API keys, webhook secrets en plano, PII de customers, etc.
    # 0o600 = solo owner puede leer/escribir.
    if not existed:
        try:
            os.chmod(_DB_PATH, 0o600)
        except OSError:
            pass
    return conn


def is_enabled() -> bool:
    return not _MEMORY_ONLY


def load_state(state: dict[str, Any]) -> None:
    """Reconstruye `state` desde la DB. Mantiene las claves que ya existan
    en state (no las pisa con vacío si la DB no tiene entrada para ellas)."""
    if _MEMORY_ONLY:
        return
    with _LOCK:
        try:
            conn = _connect()
            try:
                cur = conn.execute("SELECT key, value FROM kv")
                rows = cur.fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            # Si la DB está corrupta o no se puede abrir, arrancamos en blanco
            # — preferible a crashear el proceso al arrancar.
            return
    for key, raw in rows:
        try:
            state[key] = json.loads(raw)
        except json.JSONDecodeError:
            continue


def save_state(state: dict[str, Any]) -> None:
    """Escribe el snapshot completo del STATE a disco. Thread-safe.

    Se llama al final de cada mutación del estado. En v1 con tens-of-MIDs el
    coste es despreciable (<5ms). Si se vuelve cuello de botella, mover a
    escrituras incrementales por entidad."""
    if _MEMORY_ONLY:
        return
    with _LOCK:
        try:
            conn = _connect()
            try:
                conn.execute("BEGIN")
                for key, value in state.items():
                    raw = json.dumps(value, ensure_ascii=False, default=str)
                    conn.execute(
                        "INSERT INTO kv (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, raw),
                    )
                conn.execute("COMMIT")
            finally:
                conn.close()
        except sqlite3.Error:
            # No queremos que un fallo de disco rompa el request en curso.
            # El próximo save reintenta. En prod conviene loguear esto.
            pass


def reset_storage() -> None:
    """SOLO para tests: borra el archivo de DB si existe."""
    if _MEMORY_ONLY:
        return
    try:
        if os.path.exists(_DB_PATH):
            os.remove(_DB_PATH)
    except OSError:
        pass


def db_path() -> str:
    return ":memory:" if _MEMORY_ONLY else _DB_PATH


def stats() -> dict:
    """Snapshot del estado de la DB (útil en /v1/health o /metrics)."""
    if _MEMORY_ONLY:
        return {"enabled": False, "path": ":memory:"}
    try:
        size = os.path.getsize(_DB_PATH) if os.path.exists(_DB_PATH) else 0
    except OSError:
        size = 0
    return {"enabled": True, "path": _DB_PATH, "size_bytes": size}
