"""Tests de Operations v2 · SMS.

Cubre:
  - POST /v1/agent/sms/send (auth: agent_token Nivel 2)
  - GET  /v1/agent/sms (scoped al MID del token)
  - POST /v1/_telco/sms/inbound (webhook del partner telco, X-Telco-Key)
  - DLR simulado: queued → sent → delivered
  - Audit log de los eventos sms_*
"""
from __future__ import annotations

import time

import pytest


# ---------------- outbound: POST /v1/agent/sms/send ----------------

def test_send_sms_with_valid_token_returns_201_queued(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34 600 111 222", "body": "Hola desde el agente"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("msg_")
    assert body["direction"] == "outbound"
    assert body["to"] == "+34600111222"
    assert body["body"] == "Hola desde el agente"
    assert body["status"] == "queued"
    assert body["mid"] == active_identity["mid"]


def test_send_sms_without_token_is_401(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        json={"to": "+34600111222", "body": "x"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_agent_token"


def test_send_sms_with_invalid_to_is_400(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "notanumber", "body": "x"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_to"


def test_send_sms_with_empty_body_is_400(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "   "},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "empty_body"


def test_send_sms_body_too_long_is_400(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "a" * 1001},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "body_too_long"


# ---------------- DLR simulado: queued → sent → delivered ----------------

def test_outbound_sms_progresses_to_delivered(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "ping"},
    )
    msg_id = r.json()["id"]

    # Esperar a que los Timer terminen (0.45s + holgura). Bound estricto: 2s.
    deadline = time.time() + 2.0
    final = None
    while time.time() < deadline:
        r = anon_client.get(
            "/v1/agent/sms",
            headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        )
        msgs = r.json()["messages"]
        matched = [m for m in msgs if m["id"] == msg_id]
        if matched and matched[0]["status"] == "delivered":
            final = matched[0]
            break
        time.sleep(0.1)

    assert final is not None, "el mensaje no llegó a delivered en 2s"
    assert final["delivered_at"] is not None
    assert (final["telco_ref"] or "").startswith("mock:")


# ---------------- list: GET /v1/agent/sms ----------------

def test_list_sms_empty_when_no_messages(anon_client, active_identity):
    r = anon_client.get(
        "/v1/agent/sms",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["messages"] == []


def test_list_sms_returns_only_messages_of_caller_mid(client, anon_client,
                                                       active_identity, sample_customer_payload):
    """Token de un MID NO debe ver mensajes de otro MID."""
    # Mando un SMS desde el active_identity (MID 1)
    anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111111", "body": "from-mid-1"},
    )

    # Provisiono un segundo MID con su propio agent_token
    r = client.post("/v1/sim-requests", json={"country": "ES"})
    sim2 = r.json()["sim_request"]
    offer2 = r.json()["offer"]
    client.post(f"/v1/offers/{offer2['id']}/accept")
    cust = client.post(f"/v1/sim-requests/{sim2['id']}/customer-data",
                       json={"customer": sample_customer_payload})
    customer2_id = cust.json()["customer"]["id"]
    c = client.post("/v1/contracts",
                    json={"offer_id": offer2["id"], "customer_id": customer2_id})
    client.post(f"/v1/contracts/{c.json()['id']}/mock-sign")
    r2 = client.post("/v1/mobile-identities/activate", json={"contract_id": c.json()["id"]})
    mid2_token = r2.json()["agent_token"]

    # Listo SMS con token MID2 → NO debe ver el SMS de MID1
    r = anon_client.get(
        "/v1/agent/sms",
        headers={"Authorization": f"Bearer {mid2_token}"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_list_sms_with_direction_filter(anon_client, active_identity):
    # Envío saliente
    anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111111", "body": "out"},
    )
    r = anon_client.get(
        "/v1/agent/sms?direction=inbound",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0  # ninguno inbound

    r = anon_client.get(
        "/v1/agent/sms?direction=outbound",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.json()["count"] == 1


def test_list_sms_invalid_direction_is_400(anon_client, active_identity):
    r = anon_client.get(
        "/v1/agent/sms?direction=sideways",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 400


# ---------------- inbound: POST /v1/_telco/sms/inbound ----------------

@pytest.fixture
def telco_key(ami_api_module, monkeypatch):
    """Activa la clave del webhook telco para los tests del inbound."""
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "test_telco_key_xyz")
    yield "test_telco_key_xyz"


def test_inbound_disabled_when_no_key(anon_client, ami_api_module, monkeypatch, active_identity):
    """Sin AMI_TELCO_INBOUND_KEY configurada, el endpoint responde 503."""
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", None)
    r = anon_client.post(
        "/v1/_telco/sms/inbound",
        json={"from": "+34111", "to": "+34600549832", "body": "x"},
    )
    assert r.status_code == 503


def test_inbound_with_valid_key_creates_message(anon_client, active_identity, telco_key):
    phone = active_identity["identity"]["phone_number"]
    r = anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34 611 000 000", "to": phone, "body": "Hola agente",
              "telco_ref": "kannel:abc123"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["direction"] == "inbound"
    assert body["body"] == "Hola agente"
    assert body["mid"] == active_identity["mid"]
    assert body["status"] == "received"
    assert body["telco_ref"] == "kannel:abc123"


def test_inbound_with_wrong_key_is_401(anon_client, active_identity, telco_key):
    r = anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": "nope"},
        json={"from": "+34611", "to": active_identity["identity"]["phone_number"], "body": "x"},
    )
    assert r.status_code == 401


def test_inbound_to_unknown_number_is_404(anon_client, telco_key):
    r = anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611", "to": "+34999999999", "body": "x"},
    )
    assert r.status_code == 404


def test_inbound_visible_via_agent_list(anon_client, active_identity, telco_key):
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "body": "responde por favor"},
    )
    r = anon_client.get(
        "/v1/agent/sms?direction=inbound",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["messages"][0]["body"] == "responde por favor"


# ---------------- audit log ----------------

def test_sms_lifecycle_writes_audit_events(client, anon_client, active_identity, telco_key):
    """Las acciones SMS quedan registradas en /v1/events."""
    anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34611000000", "body": "ping"},
    )
    # Espera a que el Timer ejecute las transiciones
    time.sleep(0.6)

    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000",
              "to": active_identity["identity"]["phone_number"],
              "body": "pong"},
    )

    events = client.get("/v1/events").json()["events"]
    actions = [e["action"] for e in events]
    assert "sms_queued" in actions
    assert "sms_sent" in actions
    assert "sms_delivered" in actions
    assert "sms_inbound" in actions
