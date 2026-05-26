"""Tests para webhooks account-scoped (kyc.*, system.*, etc.)."""
from __future__ import annotations
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


_RECEIVED: list[dict] = []


class _Sink(BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        _RECEIVED.append({"path": self.path,
                          "headers": dict(self.headers),
                          "body": body.decode("utf-8", "replace")})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def sink_url():
    _RECEIVED.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/wh"
    finally:
        srv.shutdown()
        srv.server_close()


def test_create_account_webhook_returns_secret(client, sink_url):
    r = client.post("/v1/account/webhooks", json={"url": sink_url, "events": ["kyc.verified"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scope"] == "account"
    assert body["secret"].startswith("whsec_")
    assert body["account_id"]
    assert body["mid"] is None


def test_list_account_webhooks_omits_secret(client, sink_url):
    client.post("/v1/account/webhooks", json={"url": sink_url, "events": ["kyc.verified"]})
    r = client.get("/v1/account/webhooks")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert "secret" not in body["webhooks"][0]
    assert body["webhooks"][0]["secret_prefix"].startswith("whsec_")


def test_delete_account_webhook(client, sink_url):
    wh_id = client.post("/v1/account/webhooks",
                        json={"url": sink_url, "events": ["*"]}).json()["id"]
    r = client.post(f"/v1/account/webhooks/{wh_id}/delete")
    assert r.status_code == 200
    assert client.get("/v1/account/webhooks").json()["count"] == 0


def test_account_webhook_rejects_unsupported_event(client, sink_url):
    r = client.post("/v1/account/webhooks",
                    json={"url": sink_url, "events": ["not.an.event"]})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_event"


def test_account_webhook_receives_kyc_verified_event(
        client, anon_client, server_url, ami_api_module, sink_url, sample_customer_payload):
    """Flujo entero: subscribe webhook → KYC → admin verifica → webhook recibe payload."""
    # 1. Subscribe webhook account-scoped a kyc.verified
    wh_resp = client.post("/v1/account/webhooks",
                          json={"url": sink_url, "events": ["kyc.verified"]}).json()
    secret = wh_resp["secret"]

    # 2. Setup flow hasta tener KYC submitted
    sim = client.post("/v1/sim-requests", json={"country": "ES", "sim_type": "eSIM"}).json()
    sim_id = sim["sim_request"]["id"]
    offer_id = sim["offer"]["id"]
    client.post(f"/v1/offers/{offer_id}/accept")
    client.post(f"/v1/sim-requests/{sim_id}/customer-data",
                json={"customer": sample_customer_payload})
    init = client.post(f"/v1/sim-requests/{sim_id}/kyc/initiate").json()
    token = init["verification_url"].rsplit("/", 1)[-1]
    b64 = "data:image/jpeg;base64," + "A" * 200
    anon_client.post(f"/kyc/{token}/submit", json={"dni_front_b64": b64})

    # 3. Admin verifica con admin key
    import httpx, os
    os.environ["AMI_ADMIN_KEY"] = "adm_key_acc_test"
    ami_api_module.ADMIN_KEY = "adm_key_acc_test"
    with httpx.Client(base_url=server_url,
                      headers={"Authorization": "Bearer adm_key_acc_test"},
                      timeout=5) as admin:
        kyc_id = admin.get("/v1/admin/kyc").json()["kycs"][0]["id"]
        admin.post(f"/v1/admin/kyc/{kyc_id}/verify", json={"reviewer": "test"})

    # 4. Esperar a que el thread pool entregue el webhook
    deadline = time.time() + 3
    while time.time() < deadline and not _RECEIVED:
        time.sleep(0.05)
    assert _RECEIVED, "webhook nunca recibió kyc.verified"

    received = _RECEIVED[0]
    assert "X-Ami-Signature" in received["headers"] or "x-ami-signature" in {k.lower() for k in received["headers"]}
    import json as _json
    payload = _json.loads(received["body"])
    assert payload["event"] == "kyc.verified"
    assert payload["data"]["kyc_id"] == kyc_id


def test_account_webhook_does_not_leak_to_other_accounts(
        client, server_url, ami_api_module, sink_url, sample_customer_payload):
    """Webhook de account A no debe recibir eventos de account B."""
    # Setup admin para crear segundo account
    import os, httpx
    os.environ["AMI_ADMIN_KEY"] = "adm_isol_test"
    ami_api_module.ADMIN_KEY = "adm_isol_test"

    # Account A: el del client (cust_default con la test key)
    client.post("/v1/account/webhooks", json={"url": sink_url, "events": ["kyc.verified"]})

    # Account B: crear via admin
    with httpx.Client(base_url=server_url,
                      headers={"Authorization": "Bearer adm_isol_test"},
                      timeout=5) as admin:
        b = admin.post("/v1/admin/customers",
                       json={"name": "AcmeB", "billing_email": "b@x.test"})
        b_key = b.json()["api_key"]

    # Account B dispara KYC entero (no debe llegar al webhook de A)
    with httpx.Client(base_url=server_url,
                      headers={"Authorization": f"Bearer {b_key}"},
                      timeout=5) as cli_b:
        sim = cli_b.post("/v1/sim-requests", json={"country": "ES", "sim_type": "eSIM"}).json()
        cli_b.post(f"/v1/offers/{sim['offer']['id']}/accept")
        cli_b.post(f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
                   json={"customer": sample_customer_payload})
        init = cli_b.post(f"/v1/sim-requests/{sim['sim_request']['id']}/kyc/initiate").json()

    # Verificar el KYC de B con admin
    with httpx.Client(base_url=server_url,
                      headers={"Authorization": "Bearer adm_isol_test"},
                      timeout=5) as admin:
        kyc_id = init["kyc_id"]
        admin.post(f"/v1/admin/kyc/{kyc_id}/verify", json={"reviewer": "t"})

    # Dar tiempo al thread pool
    time.sleep(0.5)
    # El webhook de A NO debe haber recibido nada
    assert _RECEIVED == [], f"leak: A recibió eventos de B: {_RECEIVED}"
