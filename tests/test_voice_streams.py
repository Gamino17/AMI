"""Tests de la capa signaling del bridge Twilio-compatible de AMI."""
from __future__ import annotations
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

import ami_voice_streams as vs


# ─────────────────── parse_twiml ───────────────────

def test_parse_twiml_connect_stream():
    twiml = b'''<?xml version="1.0"?>
    <Response>
      <Connect>
        <Stream url="wss://example.com/voice/stream/abc123" track="inbound_track">
          <Parameter name="account_id" value="acct_xxx"/>
          <Parameter name="lang" value="es"/>
        </Stream>
      </Connect>
    </Response>'''
    actions = vs.parse_twiml(twiml)
    assert len(actions) == 1
    a = actions[0]
    assert a["verb"] == "stream"
    assert a["url"] == "wss://example.com/voice/stream/abc123"
    assert a["track"] == "inbound_track"
    assert a["params"] == {"account_id": "acct_xxx", "lang": "es"}


def test_parse_twiml_say_voice_lang():
    twiml = b'''<Response><Say voice="alice" language="es-ES">Hola</Say></Response>'''
    actions = vs.parse_twiml(twiml)
    assert actions == [{"verb": "say", "text": "Hola", "voice": "alice",
                         "language": "es-ES", "loop": 1}]


def test_parse_twiml_hangup_reject_pause():
    twiml = b'''<Response>
      <Pause length="2"/>
      <Reject reason="busy"/>
      <Hangup/>
    </Response>'''
    actions = vs.parse_twiml(twiml)
    assert actions == [
        {"verb": "pause", "length_s": 2},
        {"verb": "reject", "reason": "busy"},
        {"verb": "hangup"},
    ]


def test_parse_twiml_combined_say_then_connect():
    twiml = b'''<Response>
      <Say>Conectando con el agente</Say>
      <Connect><Stream url="wss://agent.example/ws"/></Connect>
    </Response>'''
    actions = vs.parse_twiml(twiml)
    assert len(actions) == 2
    assert actions[0]["verb"] == "say"
    assert actions[1]["verb"] == "stream"


def test_parse_twiml_ignores_unsupported_verbs_silently():
    twiml = b'''<Response>
      <Gather action="/x"/>
      <Record maxLength="10"/>
      <Hangup/>
    </Response>'''
    # Gather/Record no soportados todavía — los saltamos sin error.
    actions = vs.parse_twiml(twiml)
    assert actions == [{"verb": "hangup"}]


def test_parse_twiml_invalid_root_raises():
    with pytest.raises(ValueError, match="twiml_root_must_be_Response"):
        vs.parse_twiml(b"<Wrong/>")


def test_parse_twiml_invalid_xml_raises():
    with pytest.raises(ValueError, match="invalid_twiml"):
        vs.parse_twiml(b"<not xml")


def test_parse_twiml_empty_returns_empty_list():
    assert vs.parse_twiml(b"") == []
    assert vs.parse_twiml(b"   ") == []


# ─────────────────── twilio_voice_payload ───────────────────

def test_twilio_voice_payload_inbound_call():
    call = {
        "id": "call_abc",
        "direction": "inbound",
        "from": "+34680628414",
        "to": "+573336033869",
        "status": "ringing",
        "account_id": "acct_xxx",
    }
    p = vs.twilio_voice_payload(call, mid_phone="+573336033869")
    assert p["CallSid"] == "call_abc"
    assert p["From"] == "+34680628414"
    assert p["To"] == "+573336033869"
    assert p["Direction"] == "inbound"
    assert p["CallStatus"] == "ringing"
    assert p["ApiVersion"] == "2010-04-01"


def test_twilio_voice_payload_outbound_call():
    call = {"id": "call_x", "direction": "outbound", "from": "+57333",
            "to": "+34722", "status": "initiated"}
    p = vs.twilio_voice_payload(call, mid_phone="+57333")
    assert p["Direction"] == "outbound-api"


def test_call_status_mapping_keys():
    assert vs._map_call_status("in_progress") == "in-progress"
    assert vs._map_call_status("no_answer")   == "no-answer"
    assert vs._map_call_status("cancelled")   == "canceled"
    assert vs._map_call_status("completed")   == "completed"
    assert vs._map_call_status("unknown")     == "ringing"


# ─────────────────── is_safe_wss_url ───────────────────

@pytest.mark.parametrize("url,expected", [
    ("wss://example.com/path", True),
    ("wss://example.com:8443/path", True),
    ("wss://sub.example.com/voice/stream/abc", True),
    ("wss://", False),
    ("ws://example.com/path", False),       # solo wss en v1
    ("https://example.com/path", False),
    ("wss://example.com", False),           # falta path
    ("", False),
])
def test_is_safe_wss_url(url, expected):
    assert vs.is_safe_wss_url(url) is expected


# ─────────────────── dispatch_voice_webhook (con HTTP server real) ───────────────────

class _MockCustomerWebhook(BaseHTTPRequestHandler):
    """Servidor mock que simula el voice_url del cliente. Captura el body
    POST y responde con TwiML configurable."""
    captured: list = []
    response_twiml: bytes = b'<Response><Hangup/></Response>'
    response_status: int = 200

    def log_message(self, *a, **k): pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        self.captured.append({
            "path": self.path,
            "body_form": {k: v[0] for k, v in parse_qs(body).items()},
            "content_type": self.headers.get("Content-Type"),
            "user_agent": self.headers.get("User-Agent"),
        })
        self.send_response(self.response_status)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(self.response_twiml)))
        self.end_headers()
        self.wfile.write(self.response_twiml)


@pytest.fixture
def mock_voice_server():
    _MockCustomerWebhook.captured = []
    _MockCustomerWebhook.response_twiml = b'<Response><Hangup/></Response>'
    _MockCustomerWebhook.response_status = 200
    srv = HTTPServer(("127.0.0.1", 0), _MockCustomerWebhook)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{port}/voice/webhook",
            "captured": _MockCustomerWebhook.captured,
            "set_response": lambda twiml, code=200: (
                setattr(_MockCustomerWebhook, "response_twiml", twiml),
                setattr(_MockCustomerWebhook, "response_status", code),
            ),
        }
    finally:
        srv.shutdown()
        srv.server_close()


def test_dispatch_voice_webhook_basic(mock_voice_server):
    payload = vs.twilio_voice_payload({
        "id": "call_abc", "direction": "inbound",
        "from": "+34680", "to": "+57333", "status": "ringing"
    }, "+57333")
    status, body, actions = vs.dispatch_voice_webhook(mock_voice_server["url"], payload)
    assert status == 200
    assert actions == [{"verb": "hangup"}]
    cap = mock_voice_server["captured"][0]
    assert cap["content_type"] == "application/x-www-form-urlencoded"
    assert cap["body_form"]["CallSid"] == "call_abc"
    assert cap["body_form"]["From"] == "+34680"
    assert cap["body_form"]["To"] == "+57333"
    assert cap["user_agent"].startswith("TwilioProxy/AMI-Voice-Streams")


def test_dispatch_voice_webhook_with_connect_stream(mock_voice_server):
    mock_voice_server["set_response"](
        b'''<Response>
              <Connect>
                <Stream url="wss://agent.example.com/ws/abc"/>
              </Connect>
            </Response>''')
    payload = vs.twilio_voice_payload({
        "id": "call_xyz", "direction": "inbound",
        "from": "+34", "to": "+57", "status": "ringing"
    }, "+57")
    status, body, actions = vs.dispatch_voice_webhook(mock_voice_server["url"], payload)
    assert status == 200
    assert len(actions) == 1
    assert actions[0]["verb"] == "stream"
    assert actions[0]["url"] == "wss://agent.example.com/ws/abc"


def test_dispatch_voice_webhook_invalid_twiml_returns_empty_actions(mock_voice_server):
    mock_voice_server["set_response"](b'<<not xml>>', code=200)
    payload = vs.twilio_voice_payload({"id": "x", "direction": "inbound",
                                        "from": "+34", "to": "+57", "status": "ringing"}, "+57")
    status, body, actions = vs.dispatch_voice_webhook(mock_voice_server["url"], payload)
    assert status == 200
    assert actions == []


def test_dispatch_voice_webhook_5xx_propagates_status(mock_voice_server):
    mock_voice_server["set_response"](b'down', code=502)
    payload = vs.twilio_voice_payload({"id": "x", "direction": "inbound",
                                        "from": "+34", "to": "+57", "status": "ringing"}, "+57")
    status, body, actions = vs.dispatch_voice_webhook(mock_voice_server["url"], payload)
    assert status == 502
    assert actions == []


def test_dispatch_voice_webhook_unreachable_host_returns_0():
    status, body, actions = vs.dispatch_voice_webhook(
        "http://127.0.0.1:1/nope", {"x": "y"}, timeout=1.0)
    assert status == 0
    assert actions == []


# ─────────────────── new_voice_config ───────────────────

def test_new_voice_config_defaults(ami_api_module):
    cfg = vs.new_voice_config("https://example.com/webhook")
    assert cfg["voice_url"] == "https://example.com/webhook"
    assert cfg["voice_method"] == "POST"
    assert cfg["status_callback_url"] is None
    assert cfg["status_callback_method"] == "POST"
    assert cfg["id"].startswith("vcfg_")
    assert cfg["created_at"]
