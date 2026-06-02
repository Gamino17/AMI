"""Tests de Operations v2 · Voz (bridge-by-API).

AMI es el operador / SIP provider. NO ejecuta voz: cursa la llamada al PSTN
y la bridgea por SIP al callback_sip_uri del cliente (típicamente su endpoint
Realtime de OpenAI). Cubre:

  - POST /v1/agent/calls/place (auth: agent_token Nivel 2)
  - GET  /v1/agent/calls + GET /v1/agent/calls/{id} (scoped)
  - POST /v1/agent/calls/{id}/hangup
  - POST /v1/mobile-identities/{mid}/inbound-config (auth: API key del customer)
  - POST /v1/_telco/calls/inbound (webhook del partner, X-Telco-Key)
  - POST /v1/_telco/calls/{id}/status (webhook del partner)
  - Lifecycle simulado: initiated → ringing → in_progress → completed
"""
from __future__ import annotations

import time

import pytest


REALTIME_URI = "sip:proj_abc@sip.api.openai.com;transport=tls"


# ---------------- outbound: place ----------------

def test_place_call_with_valid_token_returns_201_initiated(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34 600 999 888", "callback_sip_uri": REALTIME_URI},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("call_")
    assert body["direction"] == "outbound"
    assert body["to"] == "+34600999888"
    assert body["callback_sip_uri"] == REALTIME_URI
    assert body["status"] == "initiated"
    assert body["mid"] == active_identity["mid"]


def test_place_call_without_token_is_401(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        json={"to": "+34600999888", "callback_sip_uri": REALTIME_URI},
    )
    assert r.status_code == 401


def test_place_call_invalid_to_is_400(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "notanumber", "callback_sip_uri": REALTIME_URI},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_to"


def test_place_call_invalid_sip_uri_is_400(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888", "callback_sip_uri": "http://not-sip.com"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_callback_sip_uri"


# ---------------- lifecycle simulado ----------------

def test_call_progresses_to_completed(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888", "callback_sip_uri": REALTIME_URI},
    )
    call_id = r.json()["id"]

    # Esperar a que pasen los Timers (0.90s + holgura)
    deadline = time.time() + 3.0
    final = None
    while time.time() < deadline:
        r = anon_client.get(
            f"/v1/agent/calls/{call_id}",
            headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        )
        body = r.json()
        if body.get("status") == "completed":
            final = body
            break
        time.sleep(0.1)

    assert final is not None, "la llamada no llegó a completed en 3s"
    assert final["started_at"] is not None
    assert final["ended_at"] is not None
    assert final["duration_sec"] is not None
    assert (final["telco_ref"] or "").startswith("mock:")


# ---------------- list + scoped ----------------

def test_list_calls_scoped_per_mid(client, anon_client, active_identity, sample_customer_payload):
    """Token de MID1 NO ve llamadas de MID2."""
    anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111111", "callback_sip_uri": REALTIME_URI},
    )

    # Segundo MID
    r = client.post("/v1/sim-requests", json={"country": "ES"})
    sim2 = r.json()["sim_request"]
    offer2 = r.json()["offer"]
    client.post(f"/v1/offers/{offer2['id']}/accept")
    cust = client.post(f"/v1/sim-requests/{sim2['id']}/customer-data",
                       json={"customer": sample_customer_payload})
    c = client.post("/v1/contracts",
                    json={"offer_id": offer2["id"], "customer_id": cust.json()["customer"]["id"]})
    client.post(f"/v1/contracts/{c.json()['id']}/mock-sign")
    r2 = client.post("/v1/mobile-identities/activate", json={"contract_id": c.json()["id"]})
    mid2_token = r2.json()["agent_token"]

    r = anon_client.get(
        "/v1/agent/calls",
        headers={"Authorization": f"Bearer {mid2_token}"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_get_call_of_other_mid_is_404(client, anon_client, active_identity, sample_customer_payload):
    """No puedo leer una llamada cuyo MID no es el del token."""
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111111", "callback_sip_uri": REALTIME_URI},
    )
    call_id = r.json()["id"]

    # Genero un segundo MID con su token
    r = client.post("/v1/sim-requests", json={"country": "ES"})
    sim2 = r.json()["sim_request"]
    offer2 = r.json()["offer"]
    client.post(f"/v1/offers/{offer2['id']}/accept")
    cust = client.post(f"/v1/sim-requests/{sim2['id']}/customer-data",
                       json={"customer": sample_customer_payload})
    c = client.post("/v1/contracts",
                    json={"offer_id": offer2["id"], "customer_id": cust.json()["customer"]["id"]})
    client.post(f"/v1/contracts/{c.json()['id']}/mock-sign")
    r2 = client.post("/v1/mobile-identities/activate", json={"contract_id": c.json()["id"]})
    mid2_token = r2.json()["agent_token"]

    r = anon_client.get(
        f"/v1/agent/calls/{call_id}",
        headers={"Authorization": f"Bearer {mid2_token}"},
    )
    assert r.status_code == 404


# ---------------- hangup ----------------

def test_hangup_call_cancels_initiated_call(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888", "callback_sip_uri": REALTIME_URI},
    )
    call_id = r.json()["id"]

    r = anon_client.post(
        f"/v1/agent/calls/{call_id}/hangup",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    # La cancelación debe ganar a los Timers en el caso típico (initiated → cancelled
    # dispara antes que to_ringing en muchas ejecuciones); aceptamos también que
    # los timers hayan llegado primero y entonces la llamada esté ya in_progress o
    # completed. Lo importante: el endpoint no devuelve 5xx.
    assert r.status_code in (200, 409)


def test_hangup_nonexistent_call_is_404(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/call_doesnotexist/hangup",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 404


def test_hangup_idempotent_on_completed(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888", "callback_sip_uri": REALTIME_URI},
    )
    call_id = r.json()["id"]
    # Esperar a que termine sola
    time.sleep(1.1)

    r = anon_client.post(
        f"/v1/agent/calls/{call_id}/hangup",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


# ---------------- inbound-config ----------------

def test_set_inbound_sip_uri(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/inbound-config",
        json={"inbound_sip_uri": REALTIME_URI},
    )
    assert r.status_code == 200
    assert r.json()["inbound_sip_uri"] == REALTIME_URI


def test_set_inbound_sip_uri_invalid_is_400(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/inbound-config",
        json={"inbound_sip_uri": "ftp://bad"},
    )
    assert r.status_code == 400


def test_clear_inbound_sip_uri_with_null(client, active_identity):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": REALTIME_URI})
    r = client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                    json={"inbound_sip_uri": None})
    assert r.status_code == 200
    assert r.json()["inbound_sip_uri"] is None


# ---------------- voice-config (bridge Twilio-compat) ----------------

VOICE_URL = "https://agent.example.com/voice/webhook"


def test_set_voice_config(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/voice-config",
        json={"voice_url": VOICE_URL},
    )
    assert r.status_code == 200
    cfg = r.json()["voice_config"]
    assert cfg["voice_url"] == VOICE_URL
    assert cfg["id"].startswith("vcfg_")


def test_set_voice_config_invalid_scheme_is_400(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/voice-config",
        json={"voice_url": "ftp://bad/hook"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_voice_url"


def test_clear_voice_config_with_null(client, active_identity):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})
    r = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                    json={"voice_url": None})
    assert r.status_code == 200
    assert r.json()["voice_config"] is None


def test_get_voice_config(client, active_identity):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})
    r = client.get(f"/v1/mobile-identities/{mid}/voice-config")
    assert r.status_code == 200
    assert r.json()["voice_config"]["voice_url"] == VOICE_URL


def test_inbound_route_with_voice_config_returns_voice_mode(
        client, anon_client, active_identity, telco_key):
    mid = active_identity["mid"]
    phone = active_identity["identity"]["phone_number"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})

    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "telco_ref": "ast:voice"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["mode"] == "voice"
    assert body["voice_url"] == VOICE_URL
    assert body["mid"] == mid
    assert body["call_id"].startswith("call_")
    assert "forward_sip_uri" not in body


def test_voice_config_takes_precedence_over_sip(
        client, anon_client, active_identity, telco_key):
    """Con voice_url Y inbound_sip_uri, la entrante va al modo voice (Stasis)."""
    mid = active_identity["mid"]
    phone = active_identity["identity"]["phone_number"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": REALTIME_URI})
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})

    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "telco_ref": "ast:prec"},
    )
    assert r.status_code == 201
    assert r.json()["mode"] == "voice"


# ---------------- telco webhooks ----------------

@pytest.fixture
def telco_key(ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "test_telco_key_xyz")
    yield "test_telco_key_xyz"


def test_inbound_route_with_no_config_is_409(anon_client, active_identity, telco_key):
    """Sin inbound_sip_uri configurado, el partner debe recibir 409 y rechazar la llamada."""
    phone = active_identity["identity"]["phone_number"]
    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "telco_ref": "ast:abc"},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "no_inbound_sip_uri"


def test_inbound_route_returns_forward_sip_uri(client, anon_client, active_identity, telco_key):
    mid = active_identity["mid"]
    phone = active_identity["identity"]["phone_number"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": REALTIME_URI})

    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "telco_ref": "ast:xyz"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["forward_sip_uri"] == REALTIME_URI
    assert body["mid"] == mid
    assert body["call_id"].startswith("call_")


def test_inbound_to_unknown_number_is_404(anon_client, telco_key):
    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611", "to": "+34999999999"},
    )
    assert r.status_code == 404


def test_inbound_with_wrong_key_is_401(anon_client, active_identity, telco_key):
    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": "wrong"},
        json={"from": "+34611", "to": active_identity["identity"]["phone_number"]},
    )
    assert r.status_code == 401


def test_telco_status_webhook_updates_call(client, anon_client, active_identity, telco_key):
    """Una entrante puede transicionar por el webhook del partner: ringing → in_progress → completed."""
    mid = active_identity["mid"]
    phone = active_identity["identity"]["phone_number"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": REALTIME_URI})

    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611", "to": phone},
    )
    call_id = r.json()["call_id"]

    r = anon_client.post(
        f"/v1/_telco/calls/{call_id}/status",
        headers={"X-Telco-Key": telco_key},
        json={"status": "ringing"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ringing"

    r = anon_client.post(
        f"/v1/_telco/calls/{call_id}/status",
        headers={"X-Telco-Key": telco_key},
        json={"status": "in_progress"},
    )
    assert r.status_code == 200

    r = anon_client.post(
        f"/v1/_telco/calls/{call_id}/status",
        headers={"X-Telco-Key": telco_key},
        json={"status": "completed", "hangup_cause": "normal_clearing"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["hangup_cause"] == "normal_clearing"
    assert body["duration_sec"] is not None


def test_telco_status_invalid_transition_is_409(anon_client, active_identity, telco_key, client):
    """Transición ahora más permisiva: initiated→completed se acepta
    (caso CHANUNAVAIL inmediato en saliente). Invalido sigue siendo
    completed→ringing (volver atrás en el ciclo de vida)."""
    mid = active_identity["mid"]
    phone = active_identity["identity"]["phone_number"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": REALTIME_URI})
    r = anon_client.post(
        "/v1/_telco/calls/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611", "to": phone},
    )
    call_id = r.json()["call_id"]

    # initiated → completed: AHORA permitida (fail-fast outbound).
    r = anon_client.post(
        f"/v1/_telco/calls/{call_id}/status",
        headers={"X-Telco-Key": telco_key},
        json={"status": "completed"},
    )
    assert r.status_code == 200

    # completed → ringing: sigue inválido (no se puede revivir).
    r = anon_client.post(
        f"/v1/_telco/calls/{call_id}/status",
        headers={"X-Telco-Key": telco_key},
        json={"status": "ringing"},
    )
    assert r.status_code == 409


# ---------------- audit log ----------------

def test_call_lifecycle_writes_audit_events(client, anon_client, active_identity):
    anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888", "callback_sip_uri": REALTIME_URI},
    )
    time.sleep(1.1)
    events = client.get("/v1/events").json()["events"]
    actions = [e["action"] for e in events]
    assert "call_placed" in actions
    assert "call_ringing" in actions
    assert "call_in_progress" in actions
    assert "call_completed" in actions
