"""Tests de retries automáticos para SMS y voz failed pre-answer."""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def telco_key(ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "test_telco_key")
    yield "test_telco_key"


def _short_retries(ami_api_module, monkeypatch):
    """Acortamos los backoffs para que los tests no tarden 30s."""
    monkeypatch.setattr(ami_api_module, "SMS_RETRY_BACKOFF_S", (0.05, 0.05, 0.05))
    monkeypatch.setattr(ami_api_module, "CALL_RETRY_BACKOFF_S", (0.05, 0.05))


# ---------------- SMS retries ----------------

def test_sms_failed_dlr_triggers_retry(client, anon_client, active_identity,
                                        telco_key, ami_api_module, monkeypatch):
    """Si el DLR del SMSC reporta failed, AMI reintenta hasta SMS_MAX_RETRIES."""
    _short_retries(ami_api_module, monkeypatch)

    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34611000000", "body": "retry me"},
    )
    msg_id = r.json()["id"]

    # El mock va a marcarlo delivered rápido. Forzamos failed vía DLR webhook.
    time.sleep(0.5)  # deja que el mock primero lo lleve a delivered/sent
    # Forzamos: marcamos manualmente como sent y disparamos DLR failed
    msg = ami_api_module.STATE["sms_messages"][msg_id]
    msg["status"] = "sent"
    r = anon_client.post(
        f"/v1/_telco/sms/dlr?msg_id={msg_id}&status=failed",
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 200

    # Esperamos a que el retry corra
    time.sleep(0.3)
    msg = ami_api_module.STATE["sms_messages"][msg_id]
    # El retry incrementó retry_count y volvió a invocar el adapter (que lo
    # llevó a delivered en el mock). Verificamos retry_count > 0.
    assert msg.get("retry_count", 0) >= 1


def test_sms_retry_count_capped_at_max(client, anon_client, active_identity,
                                         ami_api_module, monkeypatch):
    """retry_count nunca supera SMS_MAX_RETRIES."""
    _short_retries(ami_api_module, monkeypatch)
    monkeypatch.setattr(ami_api_module, "SMS_MAX_RETRIES", 2)

    # Forzamos un MID falso con un msg en failed con retry_count alto
    msg_id = "msg_capped"
    ami_api_module.STATE["sms_messages"][msg_id] = {
        "id": msg_id, "mid": active_identity["mid"],
        "direction": "outbound", "from": "+34600", "to": "+34611",
        "body": "x", "status": "failed",
        "retry_count": 2,
        "created_at": ami_api_module.now(),
        "delivered_at": None, "telco_ref": None,
    }
    ami_api_module._schedule_sms_retry(msg_id)
    time.sleep(0.2)
    # retry_count debe seguir en 2 (no incrementarse porque ya está en el cap)
    assert ami_api_module.STATE["sms_messages"][msg_id]["retry_count"] == 2


# ---------------- Call retries ----------------

def test_call_no_answer_pre_answer_retries(anon_client, active_identity,
                                             ami_api_module, monkeypatch, telco_key):
    """Una llamada que va a no_answer SIN haber sido contestada (sin started_at)
    debe reintentarse automáticamente."""
    _short_retries(ami_api_module, monkeypatch)

    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888",
              "callback_sip_uri": "sip:proj@host.example;transport=tls"},
    )
    call_id = r.json()["id"]
    # Esperamos a que el mock haga al menos initiated → ringing
    time.sleep(0.25)
    call = ami_api_module.STATE["calls"][call_id]
    # Forzamos no_answer desde ringing
    if call["status"] == "ringing":
        ami_api_module.transition_call(call, "no_answer",
                                         hangup_cause="timeout_test")
        time.sleep(0.2)
        assert call.get("retry_count", 0) >= 1


def test_call_with_started_at_does_NOT_retry(anon_client, active_identity,
                                               ami_api_module, monkeypatch):
    """Una llamada que YA fue contestada (started_at != None) y luego falla
    NO se reintenta — la conversación ya empezó y reintentar sería intrusivo."""
    _short_retries(ami_api_module, monkeypatch)

    # Construimos manualmente una call que ya tenía started_at
    call_id = "call_started"
    call = {
        "id": call_id, "mid": active_identity["mid"], "direction": "outbound",
        "from": "+34600", "to": "+34611",
        "callback_sip_uri": "sip:proj@host.example",
        "status": "in_progress",
        "started_at": ami_api_module.now(),  # ya contestada
        "ended_at": None, "duration_sec": None,
        "hangup_cause": None, "telco_ref": None,
        "created_at": ami_api_module.now(),
        "retry_count": 0,
    }
    ami_api_module.STATE["calls"][call_id] = call

    ami_api_module.transition_call(call, "failed",
                                     hangup_cause="mid_call_disconnect")
    time.sleep(0.2)
    assert call.get("retry_count", 0) == 0  # no se reintentó


def test_call_busy_does_NOT_retry(anon_client, active_identity,
                                    ami_api_module, monkeypatch):
    """busy es una señal legítima (línea ocupada), NO se reintenta."""
    _short_retries(ami_api_module, monkeypatch)

    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888",
              "callback_sip_uri": "sip:proj@host.example;transport=tls"},
    )
    call_id = r.json()["id"]
    time.sleep(0.25)
    call = ami_api_module.STATE["calls"][call_id]
    if call["status"] == "ringing":
        ami_api_module.transition_call(call, "busy", hangup_cause="line_busy")
        time.sleep(0.2)
        assert call.get("retry_count", 0) == 0
