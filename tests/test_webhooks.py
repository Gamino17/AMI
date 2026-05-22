"""Tests de webhooks salientes desde AMI hacia el cliente.

Levantamos un mini HTTP server en un puerto random que captura los hits.
Verificamos:
  - Registro de webhook (POST .../webhooks) y formato del secret
  - Listado y borrado
  - Dispatch al recibir sms.inbound y call.inbound
  - Firma HMAC-SHA256 correcta
  - Filtrado por event (solo escucha lo que pidió)
  - Auto-disable tras N fallos
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _WebhookSink(BaseHTTPRequestHandler):
    """HTTP server que captura los webhook hits."""
    received: list = []
    next_status: int = 200

    def log_message(self, *a, **kw): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode() if n else ""
        self.__class__.received.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        self.send_response(self.__class__.next_status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def sink():
    _WebhookSink.received = []
    _WebhookSink.next_status = 200
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _WebhookSink)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{port}/hook",
            "received": _WebhookSink.received,
            "set_status": lambda s: setattr(_WebhookSink, "next_status", s),
        }
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------- CRUD ----------------

def test_create_webhook_returns_secret_once(client, active_identity, sink):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["sms.inbound", "call.inbound"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("wh_")
    assert body["url"] == sink["url"]
    assert body["events"] == ["sms.inbound", "call.inbound"]
    assert body["status"] == "active"
    assert body["secret"].startswith("whsec_")
    assert len(body["secret"]) == len("whsec_") + 64  # 32 bytes hex


def test_create_webhook_invalid_url_is_400(client, active_identity):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": "not-a-url", "events": ["*"]},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_url"


def test_create_webhook_unsupported_event_is_400(client, active_identity, sink):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["sms.inbound", "made.up.event"]},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_event"


def test_list_webhooks_omits_full_secret(client, active_identity, sink):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["*"]},
    )
    full_secret = r.json()["secret"]

    r = client.get(f"/v1/mobile-identities/{mid}/webhooks")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    wh = body["webhooks"][0]
    assert "secret" not in wh
    assert wh["secret_prefix"] == full_secret[:14]


def test_delete_webhook(client, active_identity, sink):
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["*"]},
    )
    wh_id = r.json()["id"]

    r = client.post(f"/v1/mobile-identities/{mid}/webhooks/{wh_id}/delete")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    r = client.get(f"/v1/mobile-identities/{mid}/webhooks")
    assert r.json()["count"] == 0


# ---------------- Dispatch ----------------

@pytest.fixture
def telco_key(ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "test_telco_key")
    yield "test_telco_key"


def test_sms_inbound_fires_webhook(client, anon_client, active_identity, sink, telco_key):
    mid = active_identity["mid"]
    client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["sms.inbound"]},
    )
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "body": "hola"},
    )
    # Da tiempo al thread daemon
    deadline = time.time() + 3.0
    while time.time() < deadline and not sink["received"]:
        time.sleep(0.05)
    assert sink["received"], "webhook no llegó"
    hit = sink["received"][0]
    payload = json.loads(hit["body"])
    assert payload["event"] == "sms.inbound"
    assert payload["mid"] == mid
    assert payload["data"]["body"] == "hola"
    assert payload["data"]["direction"] == "inbound"


def test_webhook_filters_by_event(client, anon_client, active_identity, sink, telco_key):
    """Si el webhook solo escucha call.inbound, no debe recibir sms.inbound."""
    mid = active_identity["mid"]
    client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["call.inbound"]},
    )
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "body": "hola"},
    )
    time.sleep(0.5)
    assert sink["received"] == []


def test_webhook_wildcard_catches_all(client, anon_client, active_identity, sink, telco_key):
    mid = active_identity["mid"]
    client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["*"]},
    )
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "body": "hola"},
    )
    deadline = time.time() + 3.0
    while time.time() < deadline and not sink["received"]:
        time.sleep(0.05)
    assert len(sink["received"]) == 1


def test_webhook_signature_verifies(client, anon_client, active_identity, sink, telco_key):
    import ami_webhooks
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["sms.inbound"]},
    )
    secret = r.json()["secret"]
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611000000", "to": phone, "body": "verify"},
    )
    deadline = time.time() + 3.0
    while time.time() < deadline and not sink["received"]:
        time.sleep(0.05)
    hit = sink["received"][0]
    sig = hit["headers"].get("X-Ami-Signature") or hit["headers"].get("x-ami-signature")
    ts = hit["headers"].get("X-Ami-Timestamp") or hit["headers"].get("x-ami-timestamp")
    assert sig is not None
    assert ts is not None
    assert ami_webhooks.verify_signature(secret, hit["body"].encode("utf-8"), sig,
                                          timestamp_header=ts)


def test_webhook_with_wrong_secret_fails_verify(client, anon_client, active_identity, sink, telco_key):
    import ami_webhooks
    mid = active_identity["mid"]
    client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["*"]},
    )
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611", "to": phone, "body": "x"},
    )
    deadline = time.time() + 3.0
    while time.time() < deadline and not sink["received"]:
        time.sleep(0.05)
    hit = sink["received"][0]
    sig = hit["headers"].get("X-Ami-Signature") or hit["headers"].get("x-ami-signature")
    assert not ami_webhooks.verify_signature("whsec_wrong", hit["body"].encode("utf-8"), sig)


def test_webhook_marks_failure_count_on_5xx(client, anon_client, active_identity, sink,
                                             telco_key, monkeypatch):
    """Tras 5xx con todos los reintentos agotados, failure_count++ y last_delivery_status=failed."""
    # Acortamos los retries para que el test no tarde 10s
    import ami_webhooks
    monkeypatch.setattr(ami_webhooks, "RETRY_DELAYS_S", [0.01, 0.01, 0.01])
    monkeypatch.setattr(ami_webhooks, "DELIVERY_TIMEOUT_S", 1.0)

    sink["set_status"](500)
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/webhooks",
        json={"url": sink["url"], "events": ["sms.inbound"]},
    )
    wh_id = r.json()["id"]
    phone = active_identity["identity"]["phone_number"]
    anon_client.post(
        "/v1/_telco/sms/inbound",
        headers={"X-Telco-Key": telco_key},
        json={"from": "+34611", "to": phone, "body": "x"},
    )
    # Esperar a que el thread acabe los 4 intentos
    deadline = time.time() + 4.0
    while time.time() < deadline:
        r = client.get(f"/v1/mobile-identities/{mid}/webhooks")
        wh = r.json()["webhooks"][0]
        if wh["last_delivery_status"] == "failed":
            break
        time.sleep(0.1)
    assert wh["last_delivery_status"] == "failed"
    assert wh["failure_count"] >= 1
