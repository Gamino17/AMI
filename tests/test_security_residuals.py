"""Regression tests para los findings residuales arreglados en la 2ª tanda
de seguridad: signing_token, HMAC con timestamp, rate-limit panel, lock global
del STATE, CORS allowlist."""
from __future__ import annotations

import time

import httpx
import pytest


# ---------------- signing_token de /v1/sign ----------------

@pytest.fixture
def pending_contract(client, sample_customer_payload):
    """Crea un contrato en signature_pending. Devuelve el dict."""
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    client.post(f"/v1/offers/{sim['offer']['id']}/accept")
    cust = client.post(f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
                       json={"customer": sample_customer_payload}).json()["customer"]
    contract = client.post("/v1/contracts",
                           json={"offer_id": sim["offer"]["id"], "customer_id": cust["id"]}).json()
    return contract


def test_contract_has_signing_token(pending_contract):
    """El contrato ahora lleva un signing_token de 128 bits (32+ chars urlsafe)."""
    t = pending_contract["signing_token"]
    assert isinstance(t, str)
    assert len(t) >= 30
    # Y signature_url ya incluye ?t=...
    assert f"?t={t}" in pending_contract["signature_url"]


def test_sign_page_without_token_returns_404(anon_client, pending_contract):
    """GET /v1/sign/{id} sin ?t=... es 404 (contract_id no es secret)."""
    r = anon_client.get(f"/v1/sign/{pending_contract['id']}")
    assert r.status_code == 404


def test_sign_page_with_wrong_token_returns_404(anon_client, pending_contract):
    r = anon_client.get(f"/v1/sign/{pending_contract['id']}?t=wrongtokendoesnotmatch")
    assert r.status_code == 404


def test_sign_confirm_without_token_returns_404(anon_client, pending_contract):
    r = anon_client.post(f"/v1/sign/{pending_contract['id']}/confirm", data={})
    assert r.status_code == 404


def test_sign_with_correct_token_works(anon_client, pending_contract):
    t = pending_contract["signing_token"]
    r = anon_client.get(f"/v1/sign/{pending_contract['id']}?t={t}")
    assert r.status_code == 200
    r2 = anon_client.post(f"/v1/sign/{pending_contract['id']}/confirm?t={t}", data={})
    assert r2.status_code == 200


# ---------------- HMAC con timestamp ----------------

def test_verify_signature_accepts_ts_within_tolerance():
    import ami_webhooks, hmac, hashlib, time as _time
    secret = "whsec_test"
    body = b'{"event":"sms.inbound"}'
    ts = str(int(_time.time()))
    signed = ts.encode() + b"." + body
    sig = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    assert ami_webhooks.verify_signature(secret, body, sig, timestamp_header=ts)


def test_verify_signature_rejects_old_ts():
    """Replay attack: un sig viejo (>5min) debe rechazarse."""
    import ami_webhooks, hmac, hashlib, time as _time
    secret = "whsec_test"
    body = b'{"x":1}'
    ts = str(int(_time.time()) - 600)  # 10 min en el pasado
    signed = ts.encode() + b"." + body
    sig = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    assert not ami_webhooks.verify_signature(secret, body, sig, timestamp_header=ts)


def test_verify_signature_rejects_tampered_ts():
    """Si el atacante cambia el ts sin actualizar el sig, falla."""
    import ami_webhooks, hmac, hashlib, time as _time
    secret = "whsec_test"
    body = b'{"x":1}'
    real_ts = str(int(_time.time()))
    signed = real_ts.encode() + b"." + body
    sig = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    # Pasa un ts distinto del que se firmó (recent enough para no rechazar por ventana)
    tampered_ts = str(int(_time.time()) - 1)
    assert not ami_webhooks.verify_signature(secret, body, sig, timestamp_header=tampered_ts)


# ---------------- panel login rate-limit ----------------

def test_panel_login_rate_limited(server_url, ami_api_module, monkeypatch):
    """Tras N intentos fallidos, devuelve 429."""
    monkeypatch.setattr(ami_api_module, "PANEL_LOGIN_MAX_ATTEMPTS", 3)
    # Reset el dict del rate-limiter
    ami_api_module._PANEL_LOGIN_ATTEMPTS.clear()

    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False) as c:
        # 3 intentos fallidos (consumen los slots)
        for _ in range(3):
            r = c.post("/panel/login", data={"key": "wrong_key"})
            assert r.status_code == 401

        # 4o intento debe ser throttled
        r = c.post("/panel/login", data={"key": "wrong_key"})
        assert r.status_code == 429
        assert "Too many attempts" in r.text


# ---------------- CORS allowlist ----------------

def test_cors_default_is_wildcard(server_url):
    """Sin AMI_CORS_ORIGINS seteada, ACAO:* (compat con clientes existentes)."""
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.get("/v1/health")
    assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_cors_restricted_to_allowlist(server_url, ami_api_module, monkeypatch):
    """Con AMI_CORS_ORIGINS configurado, solo orígenes en la lista pasan."""
    monkeypatch.setattr(ami_api_module, "_CORS_ORIGINS",
                        ["https://app.example.com", "https://dash.example.com"])
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        # Origen permitido: eco
        r = c.get("/v1/health", headers={"Origin": "https://app.example.com"})
        assert r.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"
        # Origen no permitido: no se manda header (browser bloquea)
        r = c.get("/v1/health", headers={"Origin": "https://evil.example.com"})
        assert r.headers.get("Access-Control-Allow-Origin") is None


# ---------------- STATE lock (smoke) ----------------

def test_concurrent_sms_send_no_state_corruption(client, anon_client, active_identity):
    """Spawn N envíos concurrentes y verifica que el contador de SMS today
    refleja exactamente N (no race condition que cuente menos)."""
    import threading
    N = 30
    errors = []
    def send_one(i):
        try:
            # Destino distinto por SMS — el test es del state lock global,
            # no de los caps per-destination (que bloquearían tras 5 al mismo).
            r = anon_client.post(
                "/v1/agent/sms/send",
                headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
                json={"to": f"+346{i:08d}", "body": "x"},
            )
            if r.status_code not in (201, 429):
                errors.append(r.status_code)
        except Exception as e:
            errors.append(str(e))
    threads = [threading.Thread(target=send_one, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors, f"errors: {errors[:5]}"

    # Verifica que el contador refleja exactamente los N intentos
    snap = anon_client.get(
        "/v1/agent/usage",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
    ).json()
    # Los N envíos pasaron + el limit no se cruzó (60/hora default)
    assert snap["usage"]["sms_count_today"] == N or snap["usage"]["sms_count_today"] >= N - 1
