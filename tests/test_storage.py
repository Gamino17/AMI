"""Tests de persistencia del STATE en SQLite.

Estos tests usan AMI_DB_PATH apuntando a un archivo temporal (no :memory:)
y verifican que tras un "reinicio" simulado (reload del módulo) el STATE
se reconstruye con los datos previos.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Apunta AMI_DB_PATH a un archivo temporal y recarga SOLO ami_storage
    (no ami_api) para no contaminar el fixture session-scoped. Tests que
    necesiten ami_api integrado lo usan vía el fixture estándar."""
    db_path = tmp_path / "ami_test.db"
    monkeypatch.setenv("AMI_DB_PATH", str(db_path))

    # Recargamos solo ami_storage para que coja AMI_DB_PATH nuevo
    if "ami_storage" in sys.modules:
        del sys.modules["ami_storage"]
    import ami_storage as storage
    storage.reset_storage()

    yield db_path

    # Cleanup: vuelve a :memory: y recarga ami_storage
    monkeypatch.setenv("AMI_DB_PATH", ":memory:")
    if "ami_storage" in sys.modules:
        del sys.modules["ami_storage"]
    importlib.import_module("ami_storage")


def test_storage_enabled_with_file_path(temp_db):
    import ami_storage
    assert ami_storage.is_enabled() is True
    assert ami_storage.db_path() == str(temp_db)


def test_storage_disabled_with_memory_path(monkeypatch):
    monkeypatch.setenv("AMI_DB_PATH", ":memory:")
    # Recarga el módulo para que tome la env
    if "ami_storage" in sys.modules:
        del sys.modules["ami_storage"]
    import ami_storage
    assert ami_storage.is_enabled() is False


def test_save_and_load_roundtrip(temp_db):
    """Escribimos STATE, lo borramos en memoria, lo recargamos y debe estar."""
    import ami_storage
    state_in = {
        "mobile_identities": {
            "mid_1": {"id": "mid_1", "phone_number": "+34600", "status": "active"},
        },
        "sms_messages": {"msg_1": {"body": "hola", "mid": "mid_1"}},
        "events": [{"action": "test_event", "at": "2026-01-01T00:00:00"}],
    }
    ami_storage.save_state(state_in)

    state_out = {}
    ami_storage.load_state(state_out)
    assert state_out["mobile_identities"]["mid_1"]["phone_number"] == "+34600"
    assert state_out["sms_messages"]["msg_1"]["body"] == "hola"
    assert state_out["events"][0]["action"] == "test_event"


def test_end_to_end_persistence_across_reload(temp_db):
    """Simulamos un 'reinicio': escribimos state, lo borramos en memoria,
    recreamos el dict, hacemos load_state y debe volver con los datos.

    Esto es exactamente lo que pasa al arrancar el proceso ami_api desde
    cero: STATE arranca vacío con los buckets de los defaults, luego
    load_state lo rellena con lo persistido."""
    import ami_storage

    # Estado "antes del reinicio" — un MID activo con su token
    state_before = {
        "mobile_identities": {
            "mid_persist_1": {
                "id": "mid_persist_1",
                "phone_number": "+34 600 111 222",
                "status": "active",
                "customer_id": "cust_x",
            },
        },
        "agent_tokens": {
            "hash_x": {
                "mid": "mid_persist_1",
                "customer_id": "cust_x",
                "token_prefix": "amiagt_live_abc",
                "created_at": "2026-01-01T00:00:00+00:00",
                "status": "active",
                "revoked_at": None,
                "revoke_reason": None,
            },
        },
        "sms_messages": {"msg_1": {"body": "persisted", "mid": "mid_persist_1"}},
        "events": [{"action": "audit", "at": "2026-01-01T00:00:00"}],
    }
    ami_storage.save_state(state_before)

    # "Reinicio": empezamos con un STATE fresh (como arranca ami_api) y
    # ami_storage lo rellena
    state_after = {
        "sim_requests": {}, "offers": {}, "customers": {}, "contracts": {},
        "mobile_identities": {}, "agent_tokens": {}, "sms_messages": {},
        "events": [], "calls": {}, "webhooks": {},
    }
    ami_storage.load_state(state_after)

    assert "mid_persist_1" in state_after["mobile_identities"]
    assert state_after["mobile_identities"]["mid_persist_1"]["phone_number"] == "+34 600 111 222"
    assert "hash_x" in state_after["agent_tokens"]
    assert state_after["sms_messages"]["msg_1"]["body"] == "persisted"


def test_db_creates_wal_and_kv_table(temp_db):
    """Tras un save, la tabla kv existe y el journal_mode es WAL."""
    import ami_storage
    ami_storage.save_state({"events": [{"action": "x"}]})
    conn = sqlite3.connect(str(temp_db))
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = [t[0] for t in tables]
        assert "kv" in names
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        conn.close()


def test_save_handles_iso_dates_and_unicode(temp_db):
    """Acentos, emoji y datetimes serializan/deserializan bien."""
    import ami_storage
    state_in = {
        "events": [{"action": "sms_inbound", "data": {"body": "Hola — café ☕ 你好"}}],
    }
    ami_storage.save_state(state_in)
    state_out = {}
    ami_storage.load_state(state_out)
    assert state_out["events"][0]["data"]["body"] == "Hola — café ☕ 你好"


def test_storage_stats_reports_path_and_size(temp_db):
    """ami_storage.stats() devuelve metadata útil para health/metrics."""
    import ami_storage
    ami_storage.save_state({"events": [{"action": "x"}]})
    s = ami_storage.stats()
    assert s["enabled"] is True
    assert s["path"] == str(temp_db)
    assert s["size_bytes"] > 0


def test_corrupt_db_does_not_crash_load(temp_db):
    """Si la DB está corrupta, load_state no debe levantar — arranca en blanco."""
    # Escribe basura en lugar de SQLite válido
    with open(str(temp_db), "wb") as f:
        f.write(b"this is not a valid sqlite file")
    import ami_storage
    state = {"foo": "bar"}
    ami_storage.load_state(state)  # no debe levantar
    assert state == {"foo": "bar"}  # state intacto
