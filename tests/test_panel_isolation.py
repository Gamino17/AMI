"""Regression tests del bug pre-multi-tenancy: el /panel debe filtrar los
MIDs por el account del cookie y NUNCA mostrar MIDs de otro customer.
"""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def admin(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_panel_test")
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": "Bearer admin_panel_test"}) as c:
        yield c


def _provision_mid_for(server_url, api_key, sample_customer_payload):
    """Hace un flujo completo de contratación con la api_key dada y devuelve mid."""
    h = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        sim = c.post("/v1/sim-requests", json={"country": "ES"}).json()
        c.post(f"/v1/offers/{sim['offer']['id']}/accept")
        cust = c.post(f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
                      json={"customer": sample_customer_payload}).json()["customer"]
        contract = c.post("/v1/contracts",
                          json={"offer_id": sim["offer"]["id"], "customer_id": cust["id"]}).json()
        c.post(f"/v1/contracts/{contract['id']}/mock-sign")
        identity = c.post("/v1/mobile-identities/activate",
                          json={"contract_id": contract["id"]}).json()
    return identity["id"], identity["phone_number"]


def test_panel_filters_mids_by_account(server_url, admin, sample_customer_payload):
    """Dos customers, dos MIDs. El panel de A solo muestra el MID de A."""
    a_key = admin.post("/v1/admin/customers", json={"name": "A"}).json()["api_key"]
    b_key = admin.post("/v1/admin/customers", json={"name": "B"}).json()["api_key"]

    a_mid, a_phone = _provision_mid_for(server_url, a_key, sample_customer_payload)
    b_mid, b_phone = _provision_mid_for(server_url, b_key, sample_customer_payload)

    # A se loguea en el panel
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": a_key}) as c:
        r = c.get("/panel")
    assert r.status_code == 200
    body = r.text
    assert a_mid in body, "A debe ver su propio MID"
    assert b_mid not in body, "A NO debe ver el MID de B (BUG si aparece)"
    assert a_phone in body
    assert b_phone not in body


def test_panel_mid_detail_404_for_other_account(server_url, admin, sample_customer_payload):
    """A intenta acceder al detalle del MID de B vía URL directa."""
    a_key = admin.post("/v1/admin/customers", json={"name": "A2"}).json()["api_key"]
    b_key = admin.post("/v1/admin/customers", json={"name": "B2"}).json()["api_key"]

    _provision_mid_for(server_url, a_key, sample_customer_payload)
    b_mid, _ = _provision_mid_for(server_url, b_key, sample_customer_payload)

    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": a_key}) as c:
        r = c.get(f"/panel/mid/{b_mid}")
    assert r.status_code == 404
    assert "Not found" in r.text


def test_panel_login_resolves_multi_tenant_key(server_url, admin):
    """La pantalla de login acepta cualquier api_key válida (no solo la
    legacy AMI_API_KEY). Es lo que permite que un customer creado por admin
    pueda entrar al panel con su propia key."""
    key = admin.post("/v1/admin/customers", json={"name": "Logger"}).json()["api_key"]
    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False) as c:
        r = c.post("/panel/login", data={"key": key})
    assert r.status_code == 302
    assert r.headers["location"] == "/panel"
    assert f"ami-panel-token={key}" in r.headers.get("set-cookie", "")


def test_panel_login_rejects_invalid_key(server_url):
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.post("/panel/login", data={"key": "not_a_real_key"})
    assert r.status_code == 401
    assert "Invalid" in r.text
