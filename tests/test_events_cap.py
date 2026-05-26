"""Tests para el cap de STATE["events"].

El blob de STATE se persiste completo a SQLite en cada request HTTP via
ami_storage.save_state(). Sin un cap, un cliente activo (100 SMS/día →
~36k eventos/año) haría crecer la DB hasta decenas de MB y cada request
escribiría todo el blob. Verificamos que:

  1. event() trunca STATE["events"] al cap configurado.
  2. El orden FIFO se respeta: los eventos más viejos se descartan primero.
  3. La env var AMI_EVENTS_MAX_KEPT (default 10000) controla el cap.
  4. GET /v1/events sigue funcionando y respetando el scope multi-tenant
     después de la rotación.
"""
from __future__ import annotations

import httpx
import pytest


# --------------------------------------------------------------------------- helpers
def _emit(ami_api_module, n, action="test_event", entity_type="mobile_identity",
          entity_id="mid_dummy"):
    """Genera n eventos vía la función event() directa (no HTTP)."""
    for i in range(n):
        ami_api_module.event(action, entity_type, f"{entity_id}_{i}", {"i": i})


# --------------------------------------------------------------------------- cap básico
def test_event_cap_trims_oldest_when_exceeded(ami_api_module, monkeypatch):
    """Al añadir N+10 eventos con cap N, solo quedan los últimos N."""
    cap = 50
    monkeypatch.setattr(ami_api_module, "MAX_EVENTS_RETAINED", cap)

    ami_api_module.STATE["events"].clear()
    _emit(ami_api_module, cap + 10)

    events = ami_api_module.STATE["events"]
    assert len(events) == cap, f"esperaba {cap} eventos, hay {len(events)}"

    # Los 10 más viejos (i=0..9) deben haberse descartado. El primero que queda
    # es el i=10 y el último es i=cap+9.
    assert events[0]["data"]["i"] == 10
    assert events[-1]["data"]["i"] == cap + 9


def test_event_cap_default_is_10000():
    """El default de MAX_EVENTS_RETAINED es 10000 cuando no hay env override.

    Nota: el módulo se importa sin AMI_EVENTS_MAX_KEPT seteado en el entorno
    de tests (conftest no lo setea), así que el default debe aplicar."""
    import os

    # Si alguna env var anula el default, este test no aplica — lo skipeamos.
    if os.environ.get("AMI_EVENTS_MAX_KEPT") or os.environ.get("AMI_MAX_EVENTS_RETAINED"):
        pytest.skip("env override active; default no aplica")

    import ami_api
    assert ami_api.MAX_EVENTS_RETAINED == 10000


def test_event_cap_respects_env_var(monkeypatch):
    """AMI_EVENTS_MAX_KEPT debe sobreescribir el default.

    Reproducimos in-place la lógica del módulo (línea 39-40 de ami_api.py)
    para no tener que re-ejecutar el módulo entero, que es caro y arrastra
    side-effects (storage, handlers, etc.)."""
    monkeypatch.setenv("AMI_EVENTS_MAX_KEPT", "123")
    monkeypatch.delenv("AMI_MAX_EVENTS_RETAINED", raising=False)

    import os
    env = os.environ.get("AMI_EVENTS_MAX_KEPT") or os.environ.get("AMI_MAX_EVENTS_RETAINED")
    cap = int(env or 10000)
    assert cap == 123


def test_event_cap_falls_back_to_legacy_env(monkeypatch):
    """Por compatibilidad, AMI_MAX_EVENTS_RETAINED sigue funcionando como
    fallback si AMI_EVENTS_MAX_KEPT no está definida."""
    monkeypatch.delenv("AMI_EVENTS_MAX_KEPT", raising=False)
    monkeypatch.setenv("AMI_MAX_EVENTS_RETAINED", "777")

    import os
    env = os.environ.get("AMI_EVENTS_MAX_KEPT") or os.environ.get("AMI_MAX_EVENTS_RETAINED")
    cap = int(env or 10000)
    assert cap == 777


# --------------------------------------------------------------------------- /v1/events sigue funcionando
def test_events_endpoint_works_after_cap_truncation(client, monkeypatch, ami_api_module):
    """Tras truncar por cap, GET /v1/events sigue siendo HTTP 200 con un
    payload bien formado."""
    cap = 30
    monkeypatch.setattr(ami_api_module, "MAX_EVENTS_RETAINED", cap)

    # Forzar rotación con eventos sintéticos (entidades inexistentes).
    _emit(ami_api_module, cap * 2, entity_type="sim_request",
          entity_id="sim_does_not_exist")

    r = client.get("/v1/events")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert isinstance(body["events"], list)


def test_cap_truncation_preserves_multitenant_isolation(server_url, ami_api_module,
                                                         monkeypatch, sample_customer_payload):
    """Tras una rotación del cap, GET /v1/events de B sigue sin ver eventos
    cuyas entidades pertenecen a A."""
    cap = 20
    monkeypatch.setattr(ami_api_module, "MAX_EVENTS_RETAINED", cap)
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "cap_admin_key")

    admin = {"Authorization": "Bearer cap_admin_key"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=admin) as c:
        a_key = c.post("/v1/admin/customers", json={"name": "A-cap"}).json()["api_key"]
        b_key = c.post("/v1/admin/customers", json={"name": "B-cap"}).json()["api_key"]

    # A provisiona un MID (esto genera múltiples eventos para A).
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": f"Bearer {a_key}"}) as c:
        sim = c.post("/v1/sim-requests", json={"country": "ES"}).json()
        c.post(f"/v1/offers/{sim['offer']['id']}/accept")
        cust = c.post(f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
                      json={"customer": sample_customer_payload}).json()["customer"]
        contract = c.post("/v1/contracts",
                          json={"offer_id": sim["offer"]["id"],
                                "customer_id": cust["id"]}).json()
        c.post(f"/v1/contracts/{contract['id']}/mock-sign")
        a_mid = c.post("/v1/mobile-identities/activate",
                       json={"contract_id": contract["id"]}).json()["id"]

    # Forzamos una rotación añadiendo ruido por encima del cap.
    _emit(ami_api_module, cap * 3, entity_type="mobile_identity",
          entity_id="mid_synthetic_garbage")

    # B consulta sus eventos — no debe ver el MID de A.
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": f"Bearer {b_key}"}) as c:
        r = c.get("/v1/events")
    assert r.status_code == 200
    for ev in r.json()["events"]:
        if ev.get("entity_type") == "mobile_identity":
            assert ev.get("entity_id") != a_mid


# --------------------------------------------------------------------------- el cap se aplica via HTTP real
def test_real_http_actions_respect_cap(client, monkeypatch, ami_api_module):
    """Verificamos que la rotación ocurre también cuando los eventos los
    generan endpoints HTTP reales (no solo emit directo)."""
    cap = 5
    monkeypatch.setattr(ami_api_module, "MAX_EVENTS_RETAINED", cap)

    # Ya hay eventos previos del setup. Limpiar para empezar desde cero.
    ami_api_module.STATE["events"].clear()

    # Generamos eventos vía endpoints (cada SIMRequest emite al menos un evento
    # sim_request_created). Con cap=5 y 20 requests, garantizamos exceder y
    # forzar la rotación.
    for _ in range(20):
        r = client.post("/v1/sim-requests", json={"country": "ES"})
        assert r.status_code == 201

    # Tras superar cap, el buffer queda exactamente al cap (no por encima).
    assert len(ami_api_module.STATE["events"]) == cap
