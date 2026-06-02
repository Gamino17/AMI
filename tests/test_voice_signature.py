"""Tests unitarios del modulo de firma del webhook de voz de AMI.

Cubre el esquema AUTORITATIVO `X-AMI-Signature` (ya entregado a openclaw,
NO cambiar el formato) implementado en `ami_voice_streams`:

  Cabecera HTTP en el POST del webhook de voz:
      X-AMI-Signature: t=<unix_segundos>,v1=<hex_minuscula>

  - v1 = HMAC_SHA256(secret, signed_payload) en hex minuscula.
  - signed_payload = f"{t}.{raw_body}" donde raw_body son los BYTES EXACTOS
    del cuerpo POST (string form-urlencoded), decodificados utf-8. NO se firma
    la URL (robusto tras proxy/Funnel).
  - t = unix timestamp en SEGUNDOS al firmar.
  - secret = 'amiwhsec_' + 64 hex (token_hex(32)).
  - Anti-replay en el verificador: rechazar si |now - t| > 300s.
  - Comparacion en tiempo constante (hmac.compare_digest).

Diseno de los tests:
  - Funciones puras (sign / verify / build_header / new_signing_secret):
    vectores deterministas con `ts`/`now` inyectados, sin red.
  - dispatch_voice_webhook: mini-servidor HTTP local (loopback) que captura
    los headers + el RAW body EXACTO enviado, mismo patron que
    tests/test_voice_streams.py. NO depende del server HTTP de conftest.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import ami_voice_streams as vs


# Secreto de la demo (mid_0a7e2f9640) que openclaw verificara. Sirve de vector
# determinista; es publico para los tests (no es el secreto de produccion).
DEMO_SECRET = "amiwhsec_d0b569ae40f2d9096c8a5e8f0bfc9d2c2eb7d89dd7c302c3af9470ba70f1ec4f"

# Timestamp fijo para vectores deterministas.
FIXED_TS = 1700000000

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# ─────────────────── ami_webhook_signature: vector determinista ───────────────────

def test_ami_webhook_signature_known_vector():
    """Vector determinista: el hex debe coincidir EXACTAMENTE con el calculado
    por la recipe (cross-check contra hmac.new manual en el propio test)."""
    raw_body = "CallSid=call_demo&From=%2B34680628414&To=%2B573336033869"
    got = vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, raw_body)

    # Cross-check: recomputar con la recipe pura aqui mismo (independencia).
    signed_payload = f"{FIXED_TS}.{raw_body}".encode("utf-8")
    expected = hmac.new(DEMO_SECRET.encode("utf-8"), signed_payload,
                        hashlib.sha256).hexdigest()

    assert got == expected
    # Valor literal precomputado: blinda contra cambios accidentales de recipe.
    assert got == "e9b4b8ef5ee16cf4489738a3f91411058a6d2c025276a5c919b43582fa49df0c"
    assert _HEX64_RE.match(got), "v1 debe ser hex minuscula de 64 chars"


def test_ami_webhook_signature_accepts_bytes_and_str_equivalently():
    """raw_body como bytes o como str (utf-8) deben producir la MISMA firma:
    el caller firma el MISMO `data` que envia, venga como bytes o como str."""
    raw_str = "CallSid=call_demo&From=%2B34680628414"
    raw_bytes = raw_str.encode("utf-8")
    assert (vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, raw_bytes)
            == vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, raw_str))


def test_ami_webhook_signature_changes_with_ts_body_and_secret():
    """Cualquier cambio en t, body o secret cambia la firma (sanity)."""
    body = "CallSid=abc"
    base = vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, body)
    assert base != vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS + 1, body)
    assert base != vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, "CallSid=abd")
    other_secret = "amiwhsec_" + "0" * 64
    assert base != vs.ami_webhook_signature(other_secret, FIXED_TS, body)


# ─────────────────── build_ami_signature_header: formato ───────────────────

def test_build_header_format():
    """Cabecera bien formada `t=<seg>,v1=<hex>` con el hex == ami_webhook_signature."""
    body = "CallSid=call_demo&From=%2B34680628414"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)

    assert header.startswith(f"t={FIXED_TS},v1=")
    m = re.match(r"^t=(\d+),v1=([0-9a-f]{64})$", header)
    assert m, f"formato inesperado: {header!r}"
    t_part, v1 = int(m.group(1)), m.group(2)
    assert t_part == FIXED_TS
    assert v1 == vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, body)
    assert _HEX64_RE.match(v1)


def test_build_header_uses_now_when_ts_omitted(monkeypatch):
    """Sin `ts`, usa time.time() en SEGUNDOS (entero, no float)."""
    monkeypatch.setattr(vs.time, "time", lambda: 1700000123.987)
    header = vs.build_ami_signature_header(DEMO_SECRET, "CallSid=x")
    m = re.match(r"^t=(\d+),v1=([0-9a-f]{64})$", header)
    assert m
    assert int(m.group(1)) == 1700000123  # truncado a segundos enteros


# ─────────────────── round-trip sign -> verify ───────────────────

def test_sign_verify_round_trip():
    """Una cabecera recien firmada verifica True con el mismo secret/body/now."""
    body = "CallSid=call_demo&From=%2B34&To=%2B57"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)
    assert vs.verify_ami_signature(DEMO_SECRET, header, body, now=FIXED_TS) is True


def test_verify_round_trip_with_bytes_body():
    """verify acepta raw_body como bytes igual que el dispatch lo envia."""
    body_bytes = b"CallSid=call_demo&From=%2B34"
    header = vs.build_ami_signature_header(DEMO_SECRET, body_bytes, ts=FIXED_TS)
    assert vs.verify_ami_signature(DEMO_SECRET, header, body_bytes,
                                   now=FIXED_TS) is True


# ─────────────────── anti-replay 300s ───────────────────

def test_verify_rejects_old_timestamp():
    """Anti-replay: |now - t| > 300 -> rechazo; el borde 300 es inclusivo."""
    body = "CallSid=call_demo"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)

    # t = now - 301 -> fuera de ventana -> False.
    assert vs.verify_ami_signature(DEMO_SECRET, header, body,
                                   now=FIXED_TS + 301) is False
    # t = now - 300 -> borde inclusivo -> True.
    assert vs.verify_ami_signature(DEMO_SECRET, header, body,
                                   now=FIXED_TS + 300) is True


def test_verify_rejects_future_timestamp_beyond_tolerance():
    """Tambien rechaza t en el futuro mas alla de la tolerancia (|now - t|)."""
    body = "CallSid=call_demo"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)
    assert vs.verify_ami_signature(DEMO_SECRET, header, body,
                                   now=FIXED_TS - 301) is False
    assert vs.verify_ami_signature(DEMO_SECRET, header, body,
                                   now=FIXED_TS - 300) is True


def test_verify_custom_tolerance():
    """La tolerancia es configurable (default 300)."""
    body = "CallSid=call_demo"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)
    assert vs.verify_ami_signature(DEMO_SECRET, header, body,
                                   tolerance=10, now=FIXED_TS + 11) is False
    assert vs.verify_ami_signature(DEMO_SECRET, header, body,
                                   tolerance=10, now=FIXED_TS + 10) is True


# ─────────────────── firma / secreto / body manipulado ───────────────────

def test_verify_rejects_tampered_signature():
    """Flip de un char del v1 -> False (dentro de la ventana temporal)."""
    body = "CallSid=call_demo"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)
    m = re.match(r"^t=(\d+),v1=([0-9a-f]{64})$", header)
    t_part, v1 = m.group(1), m.group(2)
    flipped = ("0" if v1[0] != "0" else "1") + v1[1:]
    tampered = f"t={t_part},v1={flipped}"
    assert vs.verify_ami_signature(DEMO_SECRET, tampered, body,
                                   now=FIXED_TS) is False


def test_verify_rejects_tampered_body():
    """Body distinto al firmado -> False."""
    signed_body = "CallSid=call_demo&From=%2B34"
    header = vs.build_ami_signature_header(DEMO_SECRET, signed_body, ts=FIXED_TS)
    assert vs.verify_ami_signature(DEMO_SECRET, header,
                                   "CallSid=call_demo&From=%2B99",
                                   now=FIXED_TS) is False


def test_verify_rejects_wrong_secret():
    """Verificar con un secret distinto al que firmo -> False."""
    body = "CallSid=call_demo"
    header = vs.build_ami_signature_header(DEMO_SECRET, body, ts=FIXED_TS)
    other_secret = "amiwhsec_" + "a" * 64
    assert vs.verify_ami_signature(other_secret, header, body,
                                   now=FIXED_TS) is False


# ─────────────────── cabeceras malformadas ───────────────────

@pytest.mark.parametrize("header", [
    "",                       # vacio
    "t=1700000000",           # falta v1
    "v1=abc",                 # falta t
    "t=notint,v1=abc",        # t no es int
    "garbage",                # sin separadores
    "t=,v1=",                 # vacios
])
def test_verify_rejects_malformed_header(header):
    assert vs.verify_ami_signature(DEMO_SECRET, header, "CallSid=x",
                                   now=FIXED_TS) is False


def test_verify_rejects_none_header():
    """`None` (cabecera ausente) -> False sin reventar."""
    assert vs.verify_ami_signature(DEMO_SECRET, None, "CallSid=x",
                                   now=FIXED_TS) is False


def test_verify_tolerates_spaces_and_order():
    """El verificador tolera espacios y orden de los campos (k=v separados por ',')."""
    body = "CallSid=call_demo"
    v1 = vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, body)
    header = f" v1={v1} , t={FIXED_TS} "
    assert vs.verify_ami_signature(DEMO_SECRET, header, body, now=FIXED_TS) is True


# ─────────────────── new_signing_secret: formato ───────────────────

def test_new_signing_secret_format():
    """'amiwhsec_' + 64 hex; longitud 9+64; hex valido."""
    s = vs.new_signing_secret()
    assert s.startswith("amiwhsec_")
    assert len(s) == len("amiwhsec_") + 64
    body = s[len("amiwhsec_"):]
    assert _HEX64_RE.match(body), "el sufijo debe ser 64 hex minuscula"


def test_new_signing_secret_unique():
    """Dos llamadas devuelven secretos distintos (token_hex(32))."""
    a = vs.new_signing_secret()
    b = vs.new_signing_secret()
    assert a != b


def test_new_signing_secret_round_trips_through_signature():
    """Un secreto recien generado firma y verifica correctamente."""
    secret = vs.new_signing_secret()
    body = "CallSid=call_demo"
    header = vs.build_ami_signature_header(secret, body, ts=FIXED_TS)
    assert vs.verify_ami_signature(secret, header, body, now=FIXED_TS) is True


# ─────────────────── mini-server local para dispatch ───────────────────

class _MockSignedWebhook(BaseHTTPRequestHandler):
    """Servidor mock que simula el voice_url del cliente. A diferencia del de
    test_voice_streams.py, captura el RAW body EXACTO (bytes) y la cabecera
    X-AMI-Signature, imprescindible para verificar que se firma lo enviado."""
    captured: list = []
    response_twiml: bytes = b'<Response><Hangup/></Response>'

    def log_message(self, *a, **k):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.captured.append({
            "raw_body": raw,
            "raw_body_str": raw.decode("utf-8"),
            # `self.headers.get` es case-insensitive (email.message): captura
            # X-AMI-Signature aunque viaje con otra capitalizacion.
            "x_ami_signature": self.headers.get("X-AMI-Signature"),
            "content_type": self.headers.get("Content-Type"),
            "user_agent": self.headers.get("User-Agent"),
        })
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(self.response_twiml)))
        self.end_headers()
        self.wfile.write(self.response_twiml)


@pytest.fixture
def mock_voice_server():
    _MockSignedWebhook.captured = []
    _MockSignedWebhook.response_twiml = b'<Response><Hangup/></Response>'
    srv = HTTPServer(("127.0.0.1", 0), _MockSignedWebhook)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{port}/voice/webhook",
            "captured": _MockSignedWebhook.captured,
        }
    finally:
        srv.shutdown()
        srv.server_close()


def _payload() -> dict[str, str]:
    return vs.twilio_voice_payload({
        "id": "call_demo", "direction": "inbound",
        "from": "+34680628414", "to": "+573336033869", "status": "ringing",
    }, "+573336033869")


# ─────────────────── dispatch: añade cabecera bien formada con secret ───────────────────

def test_dispatch_adds_signature_header_when_secret(mock_voice_server):
    """Con signing_secret, el server recibe X-AMI-Signature bien formado y
    verify_ami_signature sobre el RAW body capturado es True. CLAVE: se verifica
    sobre el rawBody EXACTO recibido, no re-serializado."""
    status, _body, _actions = vs.dispatch_voice_webhook(
        mock_voice_server["url"], _payload(),
        signing_secret=DEMO_SECRET, sign_ts=FIXED_TS)
    assert status == 200

    cap = mock_voice_server["captured"][0]
    header = cap["x_ami_signature"]
    assert header is not None, "falta la cabecera X-AMI-Signature"
    assert re.match(r"^t=\d+,v1=[0-9a-f]{64}$", header), f"mal formada: {header!r}"
    assert header.startswith(f"t={FIXED_TS},v1=")

    # Verifica firmando los MISMOS bytes que recibio el server.
    assert vs.verify_ami_signature(DEMO_SECRET, header, cap["raw_body"],
                                   now=FIXED_TS) is True


def test_dispatch_signs_exact_bytes_sent(mock_voice_server):
    """Confirma que se firma el MISMO urlencode(payload) que se envia:
    recomputar la firma con el RAW body capturado y comparar con el header v1."""
    vs.dispatch_voice_webhook(
        mock_voice_server["url"], _payload(),
        signing_secret=DEMO_SECRET, sign_ts=FIXED_TS)

    cap = mock_voice_server["captured"][0]
    m = re.match(r"^t=(\d+),v1=([0-9a-f]{64})$", cap["x_ami_signature"])
    assert m
    t_part, v1 = int(m.group(1)), m.group(2)
    assert t_part == FIXED_TS

    # La firma esperada se calcula sobre los bytes EXACTOS recibidos.
    expected = vs.ami_webhook_signature(DEMO_SECRET, FIXED_TS, cap["raw_body"])
    assert v1 == expected

    # Y, de forma independiente, contra la recipe pura.
    signed_payload = f"{FIXED_TS}.{cap['raw_body_str']}".encode("utf-8")
    recipe = hmac.new(DEMO_SECRET.encode("utf-8"), signed_payload,
                      hashlib.sha256).hexdigest()
    assert v1 == recipe


def test_dispatch_default_sign_ts_is_now(mock_voice_server, monkeypatch):
    """Sin sign_ts inyectado, t = time.time() en segundos y verifica con ese now."""
    monkeypatch.setattr(vs.time, "time", lambda: 1700000500.5)
    vs.dispatch_voice_webhook(
        mock_voice_server["url"], _payload(), signing_secret=DEMO_SECRET)

    cap = mock_voice_server["captured"][0]
    m = re.match(r"^t=(\d+),v1=[0-9a-f]{64}$", cap["x_ami_signature"])
    assert m
    assert int(m.group(1)) == 1700000500
    assert vs.verify_ami_signature(DEMO_SECRET, cap["x_ami_signature"],
                                   cap["raw_body"], now=1700000500) is True


# ─────────────────── dispatch: omite cabecera sin secret ───────────────────

def test_dispatch_no_signature_header_when_no_secret(mock_voice_server):
    """Sin signing_secret, el server NO recibe la cabecera X-AMI-Signature
    (compat con el comportamiento de test_voice_streams.py)."""
    status, _body, _actions = vs.dispatch_voice_webhook(
        mock_voice_server["url"], _payload())
    assert status == 200
    cap = mock_voice_server["captured"][0]
    assert cap["x_ami_signature"] is None
    # Y sigue mandando el payload correcto (no rompe el dispatch base).
    assert cap["content_type"] == "application/x-www-form-urlencoded"
    assert "CallSid=call_demo" in cap["raw_body_str"]


def test_dispatch_empty_secret_does_not_sign(mock_voice_server):
    """signing_secret='' (falsy) tampoco firma — solo un secreto real activa la firma."""
    vs.dispatch_voice_webhook(
        mock_voice_server["url"], _payload(), signing_secret="")
    cap = mock_voice_server["captured"][0]
    assert cap["x_ami_signature"] is None


# ─────────────────── el secreto NO se loguea / expone ───────────────────

def test_secret_not_in_dispatch_outputs(mock_voice_server):
    """El secreto NUNCA debe aparecer en la cabecera, el body, ni los valores
    devueltos por dispatch_voice_webhook (solo viaja la firma derivada)."""
    status, body, actions = vs.dispatch_voice_webhook(
        mock_voice_server["url"], _payload(),
        signing_secret=DEMO_SECRET, sign_ts=FIXED_TS)

    cap = mock_voice_server["captured"][0]
    assert DEMO_SECRET not in cap["x_ami_signature"]
    assert DEMO_SECRET not in cap["raw_body_str"]
    assert DEMO_SECRET.encode("utf-8") not in cap["raw_body"]
    # Y tampoco en lo que devuelve dispatch al caller (status/body/actions).
    assert DEMO_SECRET.encode("utf-8") not in body
    assert DEMO_SECRET not in repr((status, actions))


def test_signature_does_not_leak_secret_to_logs(mock_voice_server, caplog):
    """dispatch_voice_webhook firmado no debe emitir el secreto a logging."""
    import logging
    with caplog.at_level(logging.DEBUG):
        vs.dispatch_voice_webhook(
            mock_voice_server["url"], _payload(),
            signing_secret=DEMO_SECRET, sign_ts=FIXED_TS)
    assert DEMO_SECRET not in caplog.text
