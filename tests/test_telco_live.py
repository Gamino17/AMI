"""Tests del LiveTelcoAdapter contra mocks HTTP locales (Kannel y Asterisk ARI).

Levantamos un HTTPServer en un puerto random que actúa como sendsms de Kannel
y como /channels de ARI. Verificamos que el adapter habla el protocolo correcto:
parámetros que envía, status que reporta al backend, manejo de errores.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _RecorderHandler(BaseHTTPRequestHandler):
    """HTTP server que graba toda request recibida y responde scripted."""
    captured: list = []
    next_response: tuple = (200, "0: Accepted for delivery")

    def log_message(self, *a, **kw): pass

    def do_GET(self):
        u = urlparse(self.path)
        self.__class__.captured.append({
            "method": "GET", "path": u.path,
            "query": {k: v[0] for k, v in parse_qs(u.query).items()},
            "headers": dict(self.headers),
        })
        status, body = self.__class__.next_response
        self._reply(status, body)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n).decode() if n else ""
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        self.__class__.captured.append({
            "method": "POST", "path": u.path,
            "query": {k: v[0] for k, v in parse_qs(u.query).items()},
            "body": parsed,
            "headers": dict(self.headers),
        })
        status, body = self.__class__.next_response
        self._reply(status, body)

    def do_DELETE(self):
        u = urlparse(self.path)
        self.__class__.captured.append({
            "method": "DELETE", "path": u.path,
            "headers": dict(self.headers),
        })
        self._reply(204, "")

    def _reply(self, status, body):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture
def telco_server():
    """Levanta el mock HTTP de Kannel/ARI y resetea la captura entre tests."""
    _RecorderHandler.captured = []
    _RecorderHandler.next_response = (200, "0: Accepted for delivery")
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _RecorderHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {"port": port, "captured": _RecorderHandler.captured,
               "set_response": lambda s, b: setattr(_RecorderHandler, "next_response", (s, b))}
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def live_adapter(ami_api_module, monkeypatch, telco_server):
    """Configura las env vars y devuelve el LiveTelcoAdapter ya inicializado."""
    port = telco_server["port"]
    monkeypatch.setenv("AMI_TELCO_MODE", "live")
    monkeypatch.setenv("AMI_KANNEL_SENDSMS_URL",
                       f"http://127.0.0.1:{port}/cgi-bin/sendsms")
    monkeypatch.setenv("AMI_KANNEL_USERNAME", "ami")
    monkeypatch.setenv("AMI_KANNEL_PASSWORD", "kannelpw")
    monkeypatch.setenv("AMI_KANNEL_DLR_URL",
                       "https://api.protocolami.com/v1/_telco/sms/dlr")
    monkeypatch.setenv("AMI_ARI_URL", f"http://127.0.0.1:{port}/ari")
    monkeypatch.setenv("AMI_ARI_USERNAME", "ami_ari")
    monkeypatch.setenv("AMI_ARI_PASSWORD", "aripw")
    monkeypatch.setenv("AMI_ARI_TRUNK", "PJSIP/trunk_partner")

    import ami_telco
    ami_telco.reset_active_adapter()
    yield ami_telco.get_active_adapter()
    ami_telco.reset_active_adapter()


# ---------------- Kannel HTTP ----------------

def test_live_send_sms_hits_kannel_with_right_params(live_adapter, telco_server, ami_api_module):
    msg = {
        "id": "msg_test1",
        "mid": "mid_x",
        "from": "+34600549832",
        "to": "+34611000000",
        "body": "Hola live",
        "status": "queued",
        "telco_ref": None,
    }
    ami_api_module.STATE["sms_messages"][msg["id"]] = msg
    live_adapter.send_sms(msg)

    # Una request capturada al sendsms de Kannel
    cap = telco_server["captured"]
    assert len(cap) == 1
    req = cap[0]
    assert req["path"] == "/cgi-bin/sendsms"
    assert req["query"]["username"] == "ami"
    assert req["query"]["password"] == "kannelpw"
    assert req["query"]["from"] == "+34600549832"
    assert req["query"]["to"] == "+34611000000"
    assert req["query"]["text"] == "Hola live"
    assert "dlr-url" in req["query"]
    assert "msg_id=msg_test1" in req["query"]["dlr-url"]

    # Y el backend marcó el estado a "sent" con el cuerpo de Kannel como telco_ref
    saved = ami_api_module.STATE["sms_messages"]["msg_test1"]
    assert saved["status"] == "sent"
    assert "Accepted" in saved["telco_ref"]


def test_live_send_sms_marks_failed_on_kannel_error(live_adapter, telco_server, ami_api_module):
    telco_server["set_response"](500, "Internal Server Error")
    msg = {
        "id": "msg_fail1", "mid": "mid_x",
        "from": "+34600549832", "to": "+34611000000",
        "body": "x", "status": "queued", "telco_ref": None,
    }
    ami_api_module.STATE["sms_messages"][msg["id"]] = msg
    live_adapter.send_sms(msg)
    assert ami_api_module.STATE["sms_messages"]["msg_fail1"]["status"] == "failed"


# ---------------- Asterisk ARI ----------------

def test_live_place_call_hits_ari_with_originate(live_adapter, telco_server, ami_api_module):
    telco_server["set_response"](200, json.dumps({"id": "channel-abc-123"}))
    call = {
        "id": "call_test1",
        "mid": "mid_x",
        "direction": "outbound",
        "from": "+34600549832",
        "to": "+34611000000",
        "callback_sip_uri": "sip:proj@sip.api.openai.com;transport=tls",
        "status": "initiated",
        "telco_ref": None,
    }
    ami_api_module.STATE["calls"][call["id"]] = call
    live_adapter.place_call(call)

    cap = telco_server["captured"]
    assert len(cap) == 1
    req = cap[0]
    assert req["method"] == "POST"
    assert req["path"] == "/ari/channels"
    body = req["body"]
    assert body["endpoint"].startswith("PJSIP/trunk_partner/sip:34611000000")
    assert body["channelId"] == "call_test1"
    assert body["app"] == "ami_voice"
    args = json.loads(body["appArgs"])
    assert args["callback_sip_uri"] == call["callback_sip_uri"]
    assert args["ami_call_id"] == "call_test1"

    # Y el backend grabó el telco_ref
    saved = ami_api_module.STATE["calls"]["call_test1"]
    assert saved["telco_ref"] == "channel-abc-123"


def test_live_hangup_hits_ari_delete(live_adapter, telco_server, ami_api_module):
    call = {"id": "call_hang1", "mid": "mid_x", "status": "in_progress"}
    ami_api_module.STATE["calls"][call["id"]] = call
    live_adapter.hangup_call(call)

    cap = telco_server["captured"]
    assert len(cap) == 1
    assert cap[0]["method"] == "DELETE"
    assert cap[0]["path"] == "/ari/channels/call_hang1"


def test_live_place_call_marks_failed_on_ari_error(live_adapter, telco_server, ami_api_module):
    telco_server["set_response"](401, json.dumps({"error": "unauthorized"}))
    call = {
        "id": "call_fail1", "mid": "mid_x", "direction": "outbound",
        "from": "+34600549832", "to": "+34611000000",
        "callback_sip_uri": "sip:proj@sip.api.openai.com",
        "status": "initiated", "telco_ref": None,
        # Campos requeridos por transition_call al marcar failed
        "started_at": None, "ended_at": None, "duration_sec": None,
        "hangup_cause": None,
        "created_at": ami_api_module.now(),
    }
    ami_api_module.STATE["calls"][call["id"]] = call
    live_adapter.place_call(call)
    assert ami_api_module.STATE["calls"]["call_fail1"]["status"] == "failed"
