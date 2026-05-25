"""Tests del flujo KYC (verificación humana de identidad).

Caso central:

  1) agente AI hace /customer-data
  2) agente AI hace /v1/sim-requests/{id}/kyc/initiate → recibe URL pública
  3) humano abre GET /kyc/{token} → ve formulario
  4) humano hace POST /kyc/{token}/submit con las 3 imágenes
  5) admin lista /v1/admin/kyc y verifica con POST /v1/admin/kyc/{id}/verify
  6) ahora se puede generar el contrato

Con AMI_KYC_REQUIRED=1, el contrato debe bloquearse hasta paso 5.
"""
from __future__ import annotations
import base64
import os
import pytest


# Imagen JPEG mínima válida en base64 (cabecera + EOI). Cumple _looks_like_b64.
_TINY_JPEG_B64 = "data:image/jpeg;base64," + (base64.b64encode(b"\xff\xd8\xff\xe0" + b"X" * 200 + b"\xff\xd9").decode())


@pytest.fixture
def admin_headers(ami_api_module):
    """Headers con el admin key (AMI_ADMIN_KEY) que el test setea."""
    os.environ["AMI_ADMIN_KEY"] = "admin_test_key"
    ami_api_module.ADMIN_KEY = "admin_test_key"
    return {"Authorization": "Bearer admin_test_key"}


@pytest.fixture
def accepted_offer_with_customer(client, sample_customer_payload):
    """Crea SIMRequest + acepta offer + envía customer-data. Devuelve los IDs."""
    r = client.post("/v1/sim-requests", json={"country": "ES", "sim_type": "eSIM"})
    sim = r.json()["sim_request"]
    offer = r.json()["offer"]
    client.post(f"/v1/offers/{offer['id']}/accept")
    cust = client.post(f"/v1/sim-requests/{sim['id']}/customer-data",
                       json={"customer": sample_customer_payload})
    return {
        "sim_request_id": sim["id"],
        "offer_id": offer["id"],
        "customer_id": cust.json()["customer"]["id"],
    }


# ─────────────────────────── initiate ───────────────────────────

def test_kyc_initiate_returns_token_and_url(client, accepted_offer_with_customer):
    r = client.post(f"/v1/sim-requests/{accepted_offer_with_customer['sim_request_id']}/kyc/initiate")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["kyc_id"].startswith("kyc_")
    assert body["verification_url"].endswith(body["verification_url"].split("/kyc/")[-1])
    assert "/kyc/" in body["verification_url"]


def test_kyc_initiate_idempotent_for_same_request(client, accepted_offer_with_customer):
    r1 = client.post(f"/v1/sim-requests/{accepted_offer_with_customer['sim_request_id']}/kyc/initiate")
    r2 = client.post(f"/v1/sim-requests/{accepted_offer_with_customer['sim_request_id']}/kyc/initiate")
    assert r1.json()["kyc_id"] == r2.json()["kyc_id"]
    assert r2.json().get("already_existed") is True


def test_kyc_initiate_fails_without_customer_data(client, fresh_sim_request):
    sim_id = fresh_sim_request["sim_request"]["id"]
    r = client.post(f"/v1/sim-requests/{sim_id}/kyc/initiate")
    assert r.status_code == 409
    assert r.json()["error"] == "customer_data_missing"


def test_kyc_initiate_404_for_unknown_sim_request(client):
    r = client.post("/v1/sim-requests/simreq_doesnotexist/kyc/initiate")
    assert r.status_code == 404


# ─────────────────────────── public form ───────────────────────────

def test_kyc_public_form_loads(client, anon_client, accepted_offer_with_customer):
    r = client.post(f"/v1/sim-requests/{accepted_offer_with_customer['sim_request_id']}/kyc/initiate")
    url = r.json()["verification_url"]
    path = "/" + url.split("//", 1)[1].split("/", 1)[1]  # /kyc/{token}
    r = anon_client.get(path)
    assert r.status_code == 200
    assert "verificación de identidad" in r.text.lower() or "verificacion" in r.text.lower()
    assert "DNI" in r.text


def test_kyc_public_form_404_for_unknown_token(anon_client):
    r = anon_client.get("/kyc/notarealtoken_xyz")
    assert r.status_code == 404
    assert "no válido" in r.text or "no valido" in r.text


# ─────────────────────────── submit ───────────────────────────

def _token_from(client, sim_id):
    r = client.post(f"/v1/sim-requests/{sim_id}/kyc/initiate")
    return r.json()["verification_url"].rsplit("/", 1)[-1]


def test_kyc_submit_happy_path(client, anon_client, accepted_offer_with_customer):
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    r = anon_client.post(f"/kyc/{token}/submit", json={
        "dni_front_b64": _TINY_JPEG_B64,
        "dni_back_b64":  _TINY_JPEG_B64,
        "selfie_b64":    _TINY_JPEG_B64,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "submitted"
    assert body["has_dni_front"] is True
    assert body["has_selfie"]    is True


def test_kyc_submit_requires_dni_front(client, anon_client, accepted_offer_with_customer):
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    r = anon_client.post(f"/kyc/{token}/submit", json={"selfie_b64": _TINY_JPEG_B64})
    assert r.status_code == 400
    assert r.json()["error"] == "dni_front_required"


def test_kyc_submit_rejects_huge_payload(client, anon_client, accepted_offer_with_customer):
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    huge = "data:image/jpeg;base64," + ("A" * 6_000_000)
    r = anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": huge})
    assert r.status_code == 400
    assert "too_large" in r.json()["error"]


def test_kyc_submit_blocks_double_submit(client, anon_client, accepted_offer_with_customer):
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    r1 = anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": _TINY_JPEG_B64})
    assert r1.status_code == 200
    r2 = anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": _TINY_JPEG_B64})
    assert r2.status_code == 409
    assert r2.json()["error"] == "kyc_already_submitted"


def test_kyc_submit_404_unknown_token(anon_client):
    r = anon_client.post("/kyc/nope/submit", json={"dni_front_b64": _TINY_JPEG_B64})
    assert r.status_code == 404


# ─────────────────────────── admin verify / reject ───────────────────────────

def test_kyc_admin_verify_flow(client, anon_client, server_url, accepted_offer_with_customer, admin_headers):
    import httpx
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": _TINY_JPEG_B64})

    with httpx.Client(base_url=server_url, headers=admin_headers, timeout=5.0) as admin:
        r = admin.get("/v1/admin/kyc")
        assert r.status_code == 200
        kycs = r.json()["kycs"]
        assert len(kycs) == 1 and kycs[0]["status"] == "submitted"
        kyc_id = kycs[0]["id"]

        r = admin.post(f"/v1/admin/kyc/{kyc_id}/verify", json={"reviewer": "daniel"})
        assert r.status_code == 200
        assert r.json()["status"] == "verified"
        assert r.json()["reviewer"] == "daniel"


def test_kyc_admin_reject_with_reason(client, anon_client, server_url, accepted_offer_with_customer, admin_headers):
    import httpx
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": _TINY_JPEG_B64})

    with httpx.Client(base_url=server_url, headers=admin_headers, timeout=5.0) as admin:
        kyc_id = admin.get("/v1/admin/kyc").json()["kycs"][0]["id"]
        r = admin.post(f"/v1/admin/kyc/{kyc_id}/reject",
                       json={"reason": "DNI ilegible", "reviewer": "ops1"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        assert r.json()["rejection_reason"] == "DNI ilegible"


def test_kyc_admin_list_requires_auth(anon_client):
    r = anon_client.get("/v1/admin/kyc")
    assert r.status_code == 401


def test_kyc_admin_verify_404_unknown(server_url, admin_headers):
    import httpx
    with httpx.Client(base_url=server_url, headers=admin_headers, timeout=5.0) as admin:
        r = admin.post("/v1/admin/kyc/kyc_nope/verify", json={})
        assert r.status_code == 404


# ─────────────────────────── gating de contratos ───────────────────────────

def test_contract_blocked_without_verified_kyc(client, accepted_offer_with_customer, monkeypatch):
    """Con AMI_KYC_REQUIRED=1 no se puede crear contrato sin KYC verified."""
    monkeypatch.setenv("AMI_KYC_REQUIRED", "1")
    r = client.post("/v1/contracts", json={
        "offer_id": accepted_offer_with_customer["offer_id"],
        "customer_id": accepted_offer_with_customer["customer_id"],
    })
    assert r.status_code == 409
    assert r.json()["error"] == "kyc_required"


def test_contract_allowed_once_kyc_verified(client, anon_client, server_url,
                                              accepted_offer_with_customer,
                                              admin_headers, monkeypatch):
    monkeypatch.setenv("AMI_KYC_REQUIRED", "1")
    import httpx
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": _TINY_JPEG_B64})
    with httpx.Client(base_url=server_url, headers=admin_headers, timeout=5.0) as admin:
        kyc_id = admin.get("/v1/admin/kyc").json()["kycs"][0]["id"]
        admin.post(f"/v1/admin/kyc/{kyc_id}/verify", json={})

    r = client.post("/v1/contracts", json={
        "offer_id": accepted_offer_with_customer["offer_id"],
        "customer_id": accepted_offer_with_customer["customer_id"],
    })
    assert r.status_code == 201, r.text


def test_contract_allowed_when_gate_disabled(client, accepted_offer_with_customer):
    """Sin AMI_KYC_REQUIRED, el flujo legacy sigue funcionando sin KYC."""
    r = client.post("/v1/contracts", json={
        "offer_id": accepted_offer_with_customer["offer_id"],
        "customer_id": accepted_offer_with_customer["customer_id"],
    })
    assert r.status_code == 201


# ─────────────────────────── panel HTML admin ───────────────────────────

def test_panel_kyc_requires_auth(anon_client):
    r = anon_client.get("/panel/kyc")
    assert r.status_code == 401


def test_panel_kyc_renders_when_authed_via_query(client, anon_client, accepted_offer_with_customer, admin_headers):
    _ = admin_headers  # fixture pone el ADMIN_KEY
    token = _token_from(client, accepted_offer_with_customer["sim_request_id"])
    anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": _TINY_JPEG_B64})

    r = anon_client.get("/panel/kyc?key=admin_test_key")
    assert r.status_code == 200
    assert "Panel KYC" in r.text
    assert "Verificar" in r.text  # botón visible para status=submitted
    assert "data:image/jpeg;base64," in r.text  # preview inline
