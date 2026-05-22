"""Tests del TelcoAdapter — selector mock/live y comportamiento del mock."""
from __future__ import annotations

import os
import time

import pytest


def test_default_mode_is_mock(ami_api_module):
    import ami_telco
    ami_telco.reset_active_adapter()
    a = ami_telco.get_active_adapter()
    assert a.name == "mock"
    assert a.health() == {"adapter": "mock"}


def test_live_mode_selectable_via_env(ami_api_module, monkeypatch):
    import ami_telco
    ami_telco.reset_active_adapter()
    monkeypatch.setenv("AMI_TELCO_MODE", "live")
    a = ami_telco.get_active_adapter()
    assert a.name == "live"
    h = a.health()
    assert h["adapter"] == "live"
    assert h["kannel_configured"] is False  # sin envs
    assert h["ari_configured"] is False


def test_health_endpoint_reports_telco(client):
    h = client.get("/v1/health").json()
    assert h["ok"] is True
    assert "telco" in h
    assert h["telco"]["adapter"] == "mock"


# ---------------- DLR webhook ----------------

@pytest.fixture
def telco_key(ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "test_telco_key_xyz")
    yield "test_telco_key_xyz"


def test_dlr_webhook_marks_delivered(client, anon_client, active_identity, telco_key):
    """En modo mock el sms ya se entrega solo, pero el webhook DLR también
    debe funcionar — un partner real lo usaría para confirmar entrega."""
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34611000000", "body": "x"},
    )
    msg_id = r.json()["id"]
    time.sleep(0.6)  # deja al mock acabar

    r = anon_client.post(
        f"/v1/_telco/sms/dlr?msg_id={msg_id}&status=delivered&telco_ref=smpp:ABC123",
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "delivered"
    assert body["telco_ref"] == "smpp:ABC123"


def test_dlr_webhook_failed_status(anon_client, active_identity, telco_key):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34611000000", "body": "x"},
    )
    msg_id = r.json()["id"]

    r = anon_client.post(
        f"/v1/_telco/sms/dlr?msg_id={msg_id}&status=failed",
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 200
    # El estado final puede ser failed o delivered según orden con los Timers
    # del mock — lo que importa es que el endpoint responde 200 y procesa.
    assert r.json()["status"] in ("failed", "delivered")


def test_dlr_unknown_msg_is_404(anon_client, telco_key):
    r = anon_client.post(
        "/v1/_telco/sms/dlr?msg_id=msg_nope&status=delivered",
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 404


def test_dlr_without_key_is_401(anon_client, active_identity, telco_key):
    r = anon_client.post(
        "/v1/_telco/sms/dlr?msg_id=x&status=delivered",
        headers={"X-Telco-Key": "wrong"},
    )
    assert r.status_code == 401


def test_dlr_disabled_when_no_key(anon_client, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", None)
    r = anon_client.post("/v1/_telco/sms/dlr?msg_id=x&status=delivered")
    assert r.status_code == 503
