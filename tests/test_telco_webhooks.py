"""Tests de los webhooks /v1/_telco/* (donde se cierra el flow live).

Estos endpoints son llamados por Kannel y Asterisk cuando reciben eventos
del partner (DLR SMS, inbound call, cambio de estado de llamada). Hasta
ahora solo el "outbound" del adapter estaba testeado; estos cubren el
"inbound" que cierra el loop.
"""
from __future__ import annotations
import os
import re

import pytest


TELCO_KEY = "telco_test_key"


@pytest.fixture(autouse=True)
def setup_telco_key(ami_api_module, monkeypatch):
    """Activa los endpoints /v1/_telco/* poniendo AMI_TELCO_INBOUND_KEY."""
    monkeypatch.setenv("AMI_TELCO_INBOUND_KEY", TELCO_KEY)
    ami_api_module.TELCO_INBOUND_KEY = TELCO_KEY
    yield
    ami_api_module.TELCO_INBOUND_KEY = os.environ.get("AMI_TELCO_INBOUND_KEY")


def _telco_headers():
    return {"X-Telco-Key": TELCO_KEY}


# ─────────────────────────── DLR ───────────────────────────

def test_dlr_marks_sms_delivered(client, anon_client, active_identity):
    """DLR de Kannel mueve sms de sent → delivered."""
    # Mandamos un SMS para tener uno en STATE
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34611000000", "body": "x"},
    )
    assert r.status_code == 201
    msg_id = r.json()["id"]

    # Kannel reporta DLR delivered (dlr-mask=1 = success). El backend
    # acepta status="delivered" en JSON o form.
    dlr = anon_client.post(
        f"/v1/_telco/sms/dlr?msg_id={msg_id}&status=delivered",
        headers=_telco_headers(),
    )
    assert dlr.status_code in (200, 201), dlr.text


def test_dlr_unauthenticated_fails(anon_client):
    r = anon_client.post("/v1/_telco/sms/dlr?msg_id=x&status=delivered",
                         headers={"X-Telco-Key": "wrong"})
    assert r.status_code == 401


def test_dlr_without_telco_key_configured_returns_503(anon_client, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", None)
    r = anon_client.post("/v1/_telco/sms/dlr?msg_id=x&status=delivered",
                         headers={"X-Telco-Key": "anything"})
    assert r.status_code == 503


# ─────────────────────────── inbound call ───────────────────────────

def test_inbound_call_returns_forward_sip_uri(client, anon_client, active_identity):
    """Si el MID tiene inbound_sip_uri configurado, devolvemos forward + call_id."""
    mid = active_identity["mid"]
    # Configurar inbound endpoint
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": "sip:agent@example.com:5060"})

    # Asterisk llama cuando entra una llamada al número del MID
    # (en este test active_identity ya tiene un phone_number asignado)
    phone = active_identity["identity"]["phone_number"]
    r = anon_client.post("/v1/_telco/calls/inbound",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={"from": "+34999111000", "to": phone,
                               "telco_ref": "PJSIP/trunk-00000a"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["forward_sip_uri"] == "sip:agent@example.com:5060"
    assert body["call_id"].startswith("call_")
    assert body["mid"] == mid


def test_inbound_call_without_endpoint_returns_409(client, anon_client, active_identity):
    """Si el MID no tiene inbound_sip_uri, devolvemos 409 para que Asterisk rechace."""
    phone = active_identity["identity"]["phone_number"]
    r = anon_client.post("/v1/_telco/calls/inbound",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={"from": "+34999111000", "to": phone, "telco_ref": "PJSIP/x"})
    assert r.status_code in (409, 404)


def test_inbound_call_unknown_to_returns_404(anon_client):
    r = anon_client.post("/v1/_telco/calls/inbound",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={"from": "+34999111000", "to": "+34999999999", "telco_ref": "x"})
    assert r.status_code in (404, 409)


# ─────────────────────────── status update ───────────────────────────

def test_call_status_progression(client, anon_client, active_identity):
    """Asterisk reporta ringing → in_progress → completed (via dialplan)."""
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": "sip:agent@example.com:5060"})
    phone = active_identity["identity"]["phone_number"]
    inbound = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={**_telco_headers(), "Content-Type": "application/json"},
        json={"from": "+34999111000", "to": phone, "telco_ref": "PJSIP/x"},
    ).json()
    call_id = inbound["call_id"]

    # ringing
    r = anon_client.post(f"/v1/_telco/calls/{call_id}/status",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={"status": "ringing"})
    assert r.status_code == 200, r.text

    # in_progress
    r = anon_client.post(f"/v1/_telco/calls/{call_id}/status",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={"status": "in_progress"})
    assert r.status_code == 200, r.text

    # completed
    r = anon_client.post(f"/v1/_telco/calls/{call_id}/status",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={"status": "completed", "hangup_cause": "NORMAL_CLEARING"})
    assert r.status_code == 200, r.text


def test_call_status_missing_status_400(anon_client):
    r = anon_client.post("/v1/_telco/calls/call_unknown/status",
                         headers={**_telco_headers(), "Content-Type": "application/json"},
                         json={})
    assert r.status_code == 400


def test_call_status_unauth_returns_401(anon_client):
    r = anon_client.post("/v1/_telco/calls/whatever/status",
                         headers={"X-Telco-Key": "wrong", "Content-Type": "application/json"},
                         json={"status": "ringing"})
    assert r.status_code == 401
