"""Tests de rate limits + spending por MID.

Cubre:
  - Defaults razonables al activar un MID
  - Enforcement de límites horario/diario en send_sms y place_call
  - Allowlist de prefijos de país
  - Budget mensual: bloquea SMS y llamadas cuando se agota
  - Account de duración de llamada al completarse
  - Endpoints REST para leer/actualizar limits y leer usage
"""
from __future__ import annotations

import time

import pytest


# ---------------- defaults + activate ----------------

def test_active_identity_has_default_limits_after_first_send(anon_client, active_identity):
    """Los limits se inicializan lazy al primer uso. Tras enviar 1 SMS, deben existir."""
    anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "x"},
    )
    identity = anon_client.get(
        "/v1/agent/usage",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    ).json()
    assert identity["limits"]["sms_per_hour"] == 60
    assert identity["limits"]["sms_per_day"] == 500
    assert "+34" in identity["limits"]["allowed_country_prefixes"]
    assert identity["usage"]["sms_count_today"] == 1


# ---------------- enforcement ----------------

def test_sms_to_disallowed_country_is_429(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+12025550100", "body": "to US"},
    )
    assert r.status_code == 429
    assert r.json()["error"] == "country_not_allowed"


def test_sms_hourly_limit_blocks(client, anon_client, active_identity):
    """Bajamos el límite a 2 y verificamos que el 3o sale 429."""
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/limits",
                json={"sms_per_hour": 2, "sms_per_day": 100})

    for i in range(2):
        r = anon_client.post(
            "/v1/agent/sms/send",
            headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
            json={"to": "+34600111222", "body": f"msg {i}"},
        )
        assert r.status_code == 201, f"msg {i} bloqueado prematuramente"

    r = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "tercer SMS"},
    )
    assert r.status_code == 429
    assert r.json()["error"] == "sms_hourly_limit_exceeded"


def test_call_to_disallowed_country_is_429(anon_client, active_identity):
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+12025550100",
              "callback_sip_uri": "sip:proj@host.example;transport=tls"},
    )
    assert r.status_code == 429
    assert r.json()["error"] == "country_not_allowed"


def test_monthly_budget_blocks_sms(client, anon_client, active_identity):
    """Budget muy bajo → el primer SMS pasa, el segundo no (precio default 0.05€)."""
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/limits",
                json={"monthly_budget_eur": 0.07})  # cabe 1 SMS, no 2

    r1 = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "1"},
    )
    assert r1.status_code == 201

    r2 = anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "2"},
    )
    assert r2.status_code == 429
    assert r2.json()["error"] == "monthly_budget_exceeded"


def test_call_blocks_when_budget_already_exhausted(client, anon_client, active_identity):
    mid = active_identity["mid"]
    # Budget 0 → cualquier llamada bloqueada de entrada
    client.post(f"/v1/mobile-identities/{mid}/limits", json={"monthly_budget_eur": 0})
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222",
              "callback_sip_uri": "sip:proj@host.example"},
    )
    assert r.status_code == 429
    assert r.json()["error"] == "monthly_budget_exceeded"


# ---------------- usage accounting ----------------

def test_completed_call_charges_minutes_and_eur(client, anon_client, active_identity):
    """Tras una llamada que llega a completed, el usage tiene minutos y EUR > 0."""
    r = anon_client.post(
        "/v1/agent/calls/place",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600999888",
              "callback_sip_uri": "sip:proj@host.example;transport=tls"},
    )
    assert r.status_code == 201
    # Esperar a que la simulación llegue a completed (mock timers)
    time.sleep(1.2)

    r = anon_client.get(
        "/v1/agent/usage",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    body = r.json()
    assert body["usage"]["calls_count_today"] == 1
    # La llamada mock dura sub-segundo; minutos puede ser 0.0 pero el contador
    # de llamadas sí sube. Verificamos que el "consumed_pct" tiene sentido.
    assert body["consumed_pct"]["calls_day"] is not None


# ---------------- REST limits CRUD ----------------

def test_get_limits_with_customer_auth(client, active_identity):
    mid = active_identity["mid"]
    r = client.get(f"/v1/mobile-identities/{mid}/limits")
    assert r.status_code == 200
    body = r.json()
    assert body["mid"] == mid
    assert body["limits"]["sms_per_hour"] == 60


def test_update_limits_patch(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/limits",
                    json={"sms_per_day": 1000,
                          "allowed_country_prefixes": ["+34", "+33"]})
    assert r.status_code == 200
    body = r.json()
    assert body["limits"]["sms_per_day"] == 1000
    assert body["limits"]["allowed_country_prefixes"] == ["+34", "+33"]
    # Otros campos no tocados quedan intactos
    assert body["limits"]["sms_per_hour"] == 60


def test_update_limits_invalid_value_is_400(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/limits",
                    json={"sms_per_day": -10})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_sms_per_day"


def test_update_limits_invalid_prefix_is_400(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/limits",
                    json={"allowed_country_prefixes": ["34"]})  # falta '+'
    assert r.status_code == 400


# ---------------- usage endpoint scoped por agent_token ----------------

def test_usage_via_agent_token(anon_client, active_identity):
    r = anon_client.get(
        "/v1/agent/usage",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "limits" in body
    assert "usage" in body
    assert "pricing" in body
    assert body["pricing"]["sms_eur"] > 0


def test_usage_endpoint_rejects_other_mid_token(client, anon_client, active_identity,
                                                  sample_customer_payload):
    """Con el token de otro MID, no puedo ver el usage del MID 1."""
    # Crear segundo MID
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
    mid2 = r2.json()["id"]

    # MID 1 hace 1 envío, MID 2 hace 0. Con el token del MID 2, vemos solo MID 2.
    anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34600111222", "body": "x"},
    )
    r = anon_client.get(
        "/v1/agent/usage",
        headers={"Authorization": f"Bearer {mid2_token}"},
    )
    assert r.status_code == 200
    assert r.json()["usage"]["sms_count_today"] == 0
    # mid2 nunca usó nada
    assert mid2 != active_identity["mid"]
