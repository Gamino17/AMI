"""Regression tests para los findings del audit de seguridad.

Cada test cubre UN hallazgo crítico que fue arreglado. Si en el futuro alguien
regresiona el fix, este test se vuelve rojo.
"""
from __future__ import annotations

import httpx
import pytest


# ---------------- Scoping cross-tenant: rotate-token, webhooks, inbound-config, limits ----------------

@pytest.fixture
def two_accounts(server_url, ami_api_module, monkeypatch, sample_customer_payload):
    """Crea dos customer-accounts (A y B). A provisiona un MID. Devuelve los
    dos api_keys y el mid de A."""
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "audit_admin_key")
    admin = {"Authorization": "Bearer audit_admin_key"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=admin) as c:
        a_key = c.post("/v1/admin/customers", json={"name": "A"}).json()["api_key"]
        b_key = c.post("/v1/admin/customers", json={"name": "B"}).json()["api_key"]
    # A provisiona MID
    h = {"Authorization": f"Bearer {a_key}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        sim = c.post("/v1/sim-requests", json={"country": "ES"}).json()
        c.post(f"/v1/offers/{sim['offer']['id']}/accept")
        cust = c.post(f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
                      json={"customer": sample_customer_payload}).json()["customer"]
        contract = c.post("/v1/contracts",
                          json={"offer_id": sim["offer"]["id"], "customer_id": cust["id"]}).json()
        c.post(f"/v1/contracts/{contract['id']}/mock-sign")
        a_mid = c.post("/v1/mobile-identities/activate",
                       json={"contract_id": contract["id"]}).json()["id"]
    return {"a_key": a_key, "b_key": b_key, "a_mid": a_mid}


def test_rotate_token_rejects_cross_account(server_url, two_accounts):
    """CRITICAL: B no puede rotar el agent_token del MID de A."""
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/rotate-token")
    assert r.status_code == 404
    assert r.json()["error"] == "mobile_identity_not_found"


def test_inbound_config_rejects_cross_account(server_url, two_accounts):
    """CRITICAL: B no puede setear el inbound_sip_uri del MID de A."""
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/inbound-config",
                   json={"inbound_sip_uri": "sip:attacker@evil.example"})
    assert r.status_code == 404


def test_create_webhook_rejects_cross_account(server_url, two_accounts):
    """CRITICAL: B no puede registrar un webhook sobre el MID de A."""
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/webhooks",
                   json={"url": "https://example.com/hijack", "events": ["sms.inbound"]})
    assert r.status_code == 404


def test_limits_post_rejects_cross_account(server_url, two_accounts):
    """HIGH: B no puede cambiar los límites del MID de A para DoSearlo."""
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/limits",
                   json={"sms_per_day": 0})
    assert r.status_code == 404


def test_limits_get_rejects_cross_account(server_url, two_accounts):
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.get(f"/v1/mobile-identities/{two_accounts['a_mid']}/limits")
    assert r.status_code == 404


def test_usage_get_rejects_cross_account(server_url, two_accounts):
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.get(f"/v1/mobile-identities/{two_accounts['a_mid']}/usage")
    assert r.status_code == 404


def test_events_filtered_by_account(server_url, two_accounts):
    """CRITICAL: GET /v1/events de B no muestra eventos de A."""
    h = {"Authorization": f"Bearer {two_accounts['b_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.get("/v1/events")
    assert r.status_code == 200
    events = r.json()["events"]
    # Ningún evento debe referenciar el MID de A
    for ev in events:
        eid = ev.get("entity_id")
        if ev.get("entity_type") == "mobile_identity":
            assert eid != two_accounts["a_mid"]


# ---------------- SSRF ----------------

def test_webhook_rejects_loopback_url(server_url, two_accounts):
    """CRITICAL: webhook URL apuntando a 127.0.0.1 directo (sin allowlist)."""
    # Sin la allowlist no debería pasar. El conftest setea la allowlist con
    # 127.0.0.1 — para este test específico, usamos una IP loopback no allowlisted.
    h = {"Authorization": f"Bearer {two_accounts['a_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/webhooks",
                   json={"url": "http://127.0.0.2/x", "events": ["*"]})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "invalid_url"
    assert body["detail"] == "blocked_ip_range"


def test_webhook_rejects_aws_metadata_endpoint(server_url, two_accounts):
    """CRITICAL: bloquear 169.254.169.254 (AWS/GCP IMDS)."""
    h = {"Authorization": f"Bearer {two_accounts['a_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/webhooks",
                   json={"url": "http://169.254.169.254/latest/meta-data/", "events": ["*"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "blocked_ip_range"


def test_webhook_rejects_private_rfc1918(server_url, two_accounts):
    h = {"Authorization": f"Bearer {two_accounts['a_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        r = c.post(f"/v1/mobile-identities/{two_accounts['a_mid']}/webhooks",
                   json={"url": "http://10.0.0.5/x", "events": ["*"]})
    assert r.status_code == 400


# ---------------- PII ----------------

def test_identity_page_does_not_leak_legal_name(server_url, two_accounts, ami_api_module):
    """HIGH: /identity/{mid} público NO debe exponer legal_name ni tax_id."""
    # Pegamos un legal_name fake en el customer y verificamos que no se ve
    identity = ami_api_module.STATE["mobile_identities"][two_accounts["a_mid"]]
    customer_id = identity.get("customer_id")
    if customer_id and customer_id in ami_api_module.STATE["customers"]:
        ami_api_module.STATE["customers"][customer_id]["legal_name"] = "SECRET LEGAL NAME"
        ami_api_module.STATE["customers"][customer_id]["tax_id"] = "SECRET-TAX-ID-12345"

    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.get(f"/identity/{two_accounts['a_mid']}")
    assert r.status_code == 200
    assert "SECRET LEGAL NAME" not in r.text
    assert "SECRET-TAX-ID-12345" not in r.text


def test_audit_customer_created_does_not_have_full_pii(server_url, two_accounts,
                                                         ami_api_module, sample_customer_payload):
    """HIGH: el evento customer_created NO debe contener tax_id, address, etc."""
    h = {"Authorization": f"Bearer {two_accounts['a_key']}"}
    with httpx.Client(base_url=server_url, timeout=5.0, headers=h) as c:
        # Provisionar otra SIMRequest para forzar customer_data submission fresh
        sim = c.post("/v1/sim-requests", json={"country": "ES"}).json()
        c.post(f"/v1/offers/{sim['offer']['id']}/accept")
        payload = dict(sample_customer_payload)
        payload["tax_id"] = "VERY-SECRET-TAX-ID"
        payload["address"] = "VERY SECRET ADDRESS"
        c.post(f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
               json={"customer": payload})

    # Mirar los events del state directamente
    for ev in ami_api_module.STATE["events"]:
        if ev["action"] == "customer_created":
            data = ev.get("data", {})
            assert "tax_id" not in data
            assert "address" not in data
            assert "billing_email" not in data


# ---------------- Headers de seguridad ----------------

def test_html_response_has_security_headers(server_url):
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.get("/spec")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "default-src" in (r.headers.get("Content-Security-Policy") or "")


def test_panel_cookie_secure_flag_when_proxied_https(server_url):
    """Si X-Forwarded-Proto=https, la cookie debe llevar Secure."""
    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False,
                      headers={"X-Forwarded-Proto": "https"}) as c:
        r = c.post("/panel/login", data={"key": "test_key_secret"})
    sc = r.headers.get("set-cookie", "")
    assert "Secure" in sc


def test_panel_cookie_no_secure_on_http(server_url):
    """Sin X-Forwarded-Proto, no se añade Secure (para localhost dev)."""
    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False) as c:
        r = c.post("/panel/login", data={"key": "test_key_secret"})
    sc = r.headers.get("set-cookie", "")
    assert "Secure" not in sc


# ---------------- Body limits ----------------

def test_body_too_large_rejected(server_url):
    """Content-Length gigante → no debe consumir memoria."""
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": "Bearer test_key_secret"}) as c:
        big = "x" * 2_000_000  # 2 MB de basura
        r = c.post("/v1/sim-requests", content=big,
                   headers={"Content-Type": "application/json"})
    # Acepta 400 (json parse failed por _BodyTooLarge atrapado por except generic)
    # o 413 si en algún momento se añade el handler específico.
    assert r.status_code in (400, 413)


# ---------------- Crypto / timing-safe ----------------

def test_telco_key_compares_with_constant_time(server_url, ami_api_module, monkeypatch):
    """Solo confirma que el endpoint responde correctamente; el timing-safe
    no es testeable de forma determinista pero el código debe usar compare_digest."""
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "secret_telco_key")
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.post("/v1/_telco/sms/inbound",
                   headers={"X-Telco-Key": "wrong"},
                   json={"from": "+34611", "to": "+34611", "body": "x"})
    assert r.status_code == 401
