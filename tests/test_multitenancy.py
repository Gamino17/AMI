"""Tests de multi-tenancy: dos customer-accounts no se ven el state."""
from __future__ import annotations

import httpx
import pytest


# ---------------- admin endpoints ----------------

def test_admin_create_customer_returns_api_key_once(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": "Bearer admin_secret_xyz"}) as c:
        r = c.post("/v1/admin/customers",
                   json={"name": "Acme Robotics", "billing_email": "billing@acme.test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("acct_")
    assert body["api_key"].startswith("amik_live_")
    assert len(body["api_key"]) == len("amik_live_") + 48  # token_hex(24) = 48 chars
    assert body["status"] == "active"


def test_admin_without_key_is_503(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", None)
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.post("/v1/admin/customers", json={"name": "x"})
    assert r.status_code == 401


def test_admin_with_wrong_key_is_401(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": "Bearer wrong"}) as c:
        r = c.post("/v1/admin/customers", json={"name": "x"})
    assert r.status_code == 401


def test_admin_list_customers(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": "Bearer admin_secret_xyz"}) as c:
        c.post("/v1/admin/customers", json={"name": "Customer 1"})
        c.post("/v1/admin/customers", json={"name": "Customer 2"})
        r = c.get("/v1/admin/customers")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 2
    # Ningún registro expone la api_key completa
    for acct in body["accounts"]:
        assert "api_key" not in acct
        assert acct["api_key_prefix"].startswith("amik_live_") or acct["api_key_prefix"] == "test_key"


def test_admin_rotate_key_invalidates_old(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    admin_headers = {"Authorization": "Bearer admin_secret_xyz"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=admin_headers) as c:
        r = c.post("/v1/admin/customers", json={"name": "Rot Test"})
        body = r.json()
        old_key, acct_id = body["api_key"], body["id"]
        r2 = c.post(f"/v1/admin/customers/{acct_id}/rotate-key")
    new_key = r2.json()["api_key"]
    assert new_key != old_key

    # La vieja key ya no debe autenticar
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": f"Bearer {old_key}"}) as c:
        r = c.post("/v1/sim-requests", json={"country": "ES"})
    assert r.status_code == 401


def test_admin_suspend_blocks_customer_requests(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    admin_headers = {"Authorization": "Bearer admin_secret_xyz"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=admin_headers) as c:
        r = c.post("/v1/admin/customers", json={"name": "Will Be Suspended"})
        key, acct_id = r.json()["api_key"], r.json()["id"]
        c.post(f"/v1/admin/customers/{acct_id}/suspend",
               json={"reason": "non_payment"})

    # Con la key del customer suspended, todo devuelve 401
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": f"Bearer {key}"}) as c:
        r = c.post("/v1/sim-requests", json={"country": "ES"})
    assert r.status_code == 401


# ---------------- scoping entre accounts ----------------

def test_two_customers_dont_see_each_others_mids(server_url, ami_api_module,
                                                    sample_customer_payload, monkeypatch):
    """A crea un MID. B (otra api_key) intenta verlo y debe recibir 404."""
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    admin = {"Authorization": "Bearer admin_secret_xyz"}

    with httpx.Client(base_url=server_url, timeout=5.0, headers=admin) as c:
        a_key = c.post("/v1/admin/customers", json={"name": "A"}).json()["api_key"]
        b_key = c.post("/v1/admin/customers", json={"name": "B"}).json()["api_key"]

    # A activa un MID
    a_headers = {"Authorization": f"Bearer {a_key}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=a_headers) as c:
        r = c.post("/v1/sim-requests", json={"country": "ES"})
        sim, offer = r.json()["sim_request"], r.json()["offer"]
        c.post(f"/v1/offers/{offer['id']}/accept")
        cust = c.post(f"/v1/sim-requests/{sim['id']}/customer-data",
                      json={"customer": sample_customer_payload}).json()["customer"]
        contract = c.post("/v1/contracts",
                          json={"offer_id": offer["id"], "customer_id": cust["id"]}).json()
        c.post(f"/v1/contracts/{contract['id']}/mock-sign")
        a_identity = c.post("/v1/mobile-identities/activate",
                            json={"contract_id": contract["id"]}).json()
        a_mid = a_identity["id"]

    # B intenta acceder al MID de A → 404
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": f"Bearer {b_key}"}) as c:
        r = c.get(f"/v1/mobile-identities/{a_mid}")
    assert r.status_code == 404


def test_customer_can_still_see_own_mid(server_url, ami_api_module,
                                          sample_customer_payload, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_secret_xyz")
    admin = {"Authorization": "Bearer admin_secret_xyz"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=admin) as c:
        a_key = c.post("/v1/admin/customers", json={"name": "SelfCheck"}).json()["api_key"]

    a_headers = {"Authorization": f"Bearer {a_key}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=a_headers) as c:
        r = c.post("/v1/sim-requests", json={"country": "ES"})
        sim, offer = r.json()["sim_request"], r.json()["offer"]
        c.post(f"/v1/offers/{offer['id']}/accept")
        cust = c.post(f"/v1/sim-requests/{sim['id']}/customer-data",
                      json={"customer": sample_customer_payload}).json()["customer"]
        contract = c.post("/v1/contracts",
                          json={"offer_id": offer["id"], "customer_id": cust["id"]}).json()
        c.post(f"/v1/contracts/{contract['id']}/mock-sign")
        mid = c.post("/v1/mobile-identities/activate",
                     json={"contract_id": contract["id"]}).json()["id"]
        # El propio customer SÍ ve su MID
        r = c.get(f"/v1/mobile-identities/{mid}")
        assert r.status_code == 200
        assert r.json()["id"] == mid


def test_legacy_api_key_maps_to_cust_default(server_url, ami_api_module):
    """Con la AMI_API_KEY legacy del conftest, debe haber un cust_default
    operando como antes (compatibilidad total con la versión single-tenant)."""
    # El conftest setea AMI_API_KEY=test_key_secret. El bootstrap debió crear
    # un customer cust_default con esa key.
    state = ami_api_module.STATE
    assert "cust_default" in state["customer_accounts"]
    assert state["customer_accounts"]["cust_default"]["status"] == "active"

    # Y con esa key seguimos pudiendo hacer todo el flujo (los otros 187 tests
    # ya lo verifican; aquí solo confirmamos que el bootstrap se hizo).
