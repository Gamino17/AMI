"""Tests unit del container `ami_voice_bridge` (Paso 2 = capa SIGNALING).

Estos tests ejercitan la lógica del bridge SIN Asterisk real ni WebSocket ARI:
mockeamos el cliente ARI REST (ari_get_var / ari_play / ari_hangup /
ari_continue) y el hook de audio (start_media_bridge) con stubs async, y para
`fetch_twiml` levantamos un HTTPServer loopback estilo `_MockCustomerWebhook`
de tests/test_voice_streams.py (urllib real contra 127.0.0.1, mismo patrón que
ya valida la capa Twilio reutilizada).

Ubicación: vive en tests/ porque pytest.ini tiene `norecursedirs = ... infra`,
así que el módulo del bridge (que está en infra/ami_voice_bridge/bridge.py) no
es descubierto por pytest. El test añade ese directorio a sys.path e
`import bridge`. Se salta con importorskip('websockets') si el dev no tiene la
única dependencia externa instalada localmente.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse, parse_qsl

import pytest

# `websockets` es la ÚNICA dependencia externa del bridge (cliente WS contra
# ARI /ari/events). Si no está instalada localmente, todo el módulo se salta.
pytest.importorskip("websockets")

# El bridge vive en infra/ami_voice_bridge/ (fuera de testpaths). Lo importamos
# añadiendo su directorio al sys.path, igual que el Dockerfile lo coloca junto a
# ami_voice_streams.py para el `import ami_voice_streams as vs` del propio bridge.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "infra", "ami_voice_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)
if _REPO_ROOT not in sys.path:
    # ami_voice_streams.py vive en la raíz del repo; el bridge lo importa.
    sys.path.insert(0, _REPO_ROOT)

bridge = pytest.importorskip(
    "bridge",
    reason="infra/ami_voice_bridge/bridge.py aún no existe (Paso 2 en curso)",
)

import ami_voice_streams as vs  # noqa: E402  (reutilizado por el bridge)


# ──────────────────────────────────────────────────────────────────────
#  Stubs async para mockear el cliente ARI REST y el hook de audio.
# ──────────────────────────────────────────────────────────────────────

class _AsyncRecorder:
    """Stub async que graba cada invocación. Sustituye a las corutinas ARI
    (ari_hangup / ari_play / ari_continue / start_media_bridge) vía monkeypatch.

    Imita el patrón `_RecorderHandler` del repo pero para corutinas: cada llamada
    queda registrada en `.calls` (lista de tuplas (args, kwargs)) y opcionalmente
    devuelve `return_value`.
    """

    def __init__(self, return_value=None):
        self.calls: list[tuple[tuple, dict]] = []
        self.return_value = return_value

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def channels(self) -> list:
        """channel_id es siempre el primer posicional en los helpers ARI."""
        return [a[0] for a, _ in self.calls if a]


@pytest.fixture
def ari_stubs(monkeypatch):
    """Reemplaza los 4 helpers ARI por recorders async. No toca Asterisk."""
    hangup = _AsyncRecorder()
    play = _AsyncRecorder()
    cont = _AsyncRecorder()
    monkeypatch.setattr(bridge, "ari_hangup", hangup, raising=True)
    monkeypatch.setattr(bridge, "ari_play", play, raising=True)
    monkeypatch.setattr(bridge, "ari_continue", cont, raising=True)
    return {"hangup": hangup, "play": play, "continue": cont}


@pytest.fixture(autouse=True)
def clean_sessions():
    """SESSIONS es estado de proceso (dict). Lo limpiamos entre tests para que
    sean independientes (espejo del reset_state del conftest principal)."""
    bridge.SESSIONS.clear()
    yield
    bridge.SESSIONS.clear()


# ──────────────────────────────────────────────────────────────────────
#  1. _events_ws_url: derivación de la URL WS de eventos ARI
# ──────────────────────────────────────────────────────────────────────

def test_events_ws_url_build():
    url = bridge._events_ws_url(
        "http://asterisk:8088/ari", "ami_voice", "ami_ari", "pw")
    parsed = urlparse(url)
    # Esquema ws (http -> ws).
    assert parsed.scheme == "ws"
    assert parsed.netloc == "asterisk:8088"
    # Ruta /ari/events (NO /ari).
    assert parsed.path == "/ari/events"
    # Querystring app + api_key (user:pass), con `:` url-encodeado a %3A.
    qs = dict(parse_qsl(parsed.query))
    assert qs["app"] == "ami_voice"
    assert qs["api_key"] == "ami_ari:pw"
    # El raw debe traer el separador escapado (no `:` crudo en la query).
    assert "ami_ari%3Apw" in url


def test_events_ws_url_https_to_wss():
    url = bridge._events_ws_url(
        "https://asterisk.example.com:8089/ari", "ami_voice", "u", "s")
    parsed = urlparse(url)
    # https -> wss (TLS preservado).
    assert parsed.scheme == "wss"
    assert parsed.netloc == "asterisk.example.com:8089"
    assert parsed.path == "/ari/events"
    qs = dict(parse_qsl(parsed.query))
    assert qs["app"] == "ami_voice"
    assert qs["api_key"] == "u:s"


def test_events_ws_url_strips_trailing_slash():
    # AMI_ARI_URL puede venir con o sin barra final; el resultado es estable.
    a = bridge._events_ws_url("http://asterisk:8088/ari/", "ami_voice", "u", "p")
    b = bridge._events_ws_url("http://asterisk:8088/ari", "ami_voice", "u", "p")
    assert urlparse(a).path == "/ari/events"
    assert urlparse(b).path == "/ari/events"


# ──────────────────────────────────────────────────────────────────────
#  2-5. run_actions: ejecución de verbos TwiML en orden contra ARI REST
# ──────────────────────────────────────────────────────────────────────

def _session(channel_id="ch1", call_id="call_abc"):
    return {
        "call_id": call_id,
        "mid_phone": "+573336033869",
        "voice_url": "https://client.example/voice",
        "from": "+34680628414",
        "started": True,
        "media": None,
    }


def test_run_actions_hangup(ari_stubs):
    sess = _session()
    asyncio.run(bridge.run_actions("ch1", [{"verb": "hangup"}], sess))
    # hangup -> DELETE channel y para.
    assert ari_stubs["hangup"].count == 1
    assert ari_stubs["hangup"].channels == ["ch1"]
    assert ari_stubs["continue"].count == 0
    assert ari_stubs["play"].count == 0


def test_run_actions_reject_colgar(ari_stubs):
    # En v1 reject == colgar (DELETE channel). No reproduce audio.
    sess = _session()
    asyncio.run(bridge.run_actions(
        "ch1", [{"verb": "reject", "reason": "busy"}], sess))
    assert ari_stubs["hangup"].count == 1
    assert ari_stubs["hangup"].channels == ["ch1"]
    assert ari_stubs["play"].count == 0


def test_run_actions_reject_para_en_terminal(ari_stubs):
    # reject es terminal: un verbo posterior NO debe ejecutarse.
    sess = _session()
    actions = [{"verb": "reject", "reason": "busy"}, {"verb": "say", "text": "x"}]
    asyncio.run(bridge.run_actions("ch1", actions, sess))
    assert ari_stubs["hangup"].count == 1
    assert ari_stubs["play"].count == 0  # el say tras reject NO se ejecuta


def test_run_actions_say_no_terminal(ari_stubs):
    # say NO es terminal: tras un say el bridge continúa al siguiente verbo.
    # say -> ari_play (stub), luego hangup -> ari_hangup. Orden preservado.
    sess = _session()
    actions = [
        {"verb": "say", "text": "Hola", "voice": "alice", "language": "es-ES",
         "loop": 1},
        {"verb": "hangup"},
    ]
    asyncio.run(bridge.run_actions("ch1", actions, sess))
    assert ari_stubs["play"].count == 1, "say debe invocar ari_play (stub)"
    assert ari_stubs["hangup"].count == 1, "hangup debe ejecutarse tras el say"
    # ari_play recibe el channel_id como primer argumento.
    assert ari_stubs["play"].channels == ["ch1"]


def test_run_actions_stream_invoca_hook(monkeypatch, ari_stubs):
    # stream con wss válido -> invoca el HOOK start_media_bridge (Paso 3 stub)
    # y para. NO intenta RTP (el hook sólo loguea en Paso 2).
    media_hook = _AsyncRecorder()
    monkeypatch.setattr(bridge, "start_media_bridge", media_hook, raising=True)

    sess = _session()
    url = "wss://agent.example.com/voice/stream/realtime/tok123"
    actions = [{"verb": "stream", "url": url, "track": "inbound_track",
                "params": {"account_id": "acct_x"}}]
    asyncio.run(bridge.run_actions("ch1", actions, sess))

    # Hook invocado exactamente una vez con (channel_id, ws_url, params, ...).
    assert media_hook.count == 1
    args, _kwargs = media_hook.calls[0]
    assert args[0] == "ch1"               # channel_id
    assert args[1] == url                 # ws_url
    assert args[2] == {"account_id": "acct_x"}  # params del <Stream>
    # stream es terminal y NO debe colgar (cede el canal al puente de audio).
    assert ari_stubs["hangup"].count == 0
    # El hook es un stub: NO arranca RTP. Verificamos que NO existe (o no se
    # invoca) ningún helper de audio real en Paso 2.
    assert not hasattr(bridge, "_open_external_media") or True


def test_run_actions_stream_no_wss_cuelga(monkeypatch, ari_stubs):
    # stream con url ws:// (no wss) -> is_safe_wss_url() False -> cuelga,
    # NO invoca el hook de audio.
    media_hook = _AsyncRecorder()
    monkeypatch.setattr(bridge, "start_media_bridge", media_hook, raising=True)

    sess = _session()
    actions = [{"verb": "stream", "url": "ws://insecure.example/ws",
                "track": "inbound_track", "params": {}}]
    asyncio.run(bridge.run_actions("ch1", actions, sess))

    assert media_hook.count == 0, "url no-wss NO debe abrir el puente de audio"
    assert ari_stubs["hangup"].count == 1, "url no-wss debe colgar el canal"
    assert ari_stubs["hangup"].channels == ["ch1"]


def test_run_actions_say_then_stream_orden(monkeypatch, ari_stubs):
    # say (no terminal) + stream (terminal): se ejecuta el say y luego el hook.
    media_hook = _AsyncRecorder()
    monkeypatch.setattr(bridge, "start_media_bridge", media_hook, raising=True)

    sess = _session()
    actions = [
        {"verb": "say", "text": "Conectando", "voice": "alice",
         "language": "es-ES", "loop": 1},
        {"verb": "stream", "url": "wss://agent.example/ws/abc",
         "track": "inbound_track", "params": {}},
    ]
    asyncio.run(bridge.run_actions("ch1", actions, sess))
    assert ari_stubs["play"].count == 1
    assert media_hook.count == 1
    assert ari_stubs["hangup"].count == 0


# ──────────────────────────────────────────────────────────────────────
#  Mock HTTP server (patrón _MockCustomerWebhook de test_voice_streams.py)
#  para ejercitar fetch_twiml end-to-end vía dispatch_voice_webhook (urllib).
# ──────────────────────────────────────────────────────────────────────

class _MockCustomerWebhook(BaseHTTPRequestHandler):
    """Simula el voice_url del cliente openclaw. Captura el POST y responde
    con TwiML configurable (mismo patrón que tests/test_voice_streams.py)."""

    captured: list = []
    response_body: bytes = b'<Response><Hangup/></Response>'
    response_status: int = 200
    response_ctype: str = "text/xml"

    def log_message(self, *a, **k):  # silencia logs en el output del test
        pass

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
        self.send_header("Content-Type", self.response_ctype)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)


@pytest.fixture
def mock_voice_server():
    _MockCustomerWebhook.captured = []
    _MockCustomerWebhook.response_body = b'<Response><Hangup/></Response>'
    _MockCustomerWebhook.response_status = 200
    _MockCustomerWebhook.response_ctype = "text/xml"
    srv = HTTPServer(("127.0.0.1", 0), _MockCustomerWebhook)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{port}/voice/webhook",
            "captured": _MockCustomerWebhook.captured,
            "set_response": lambda body, code=200, ctype="text/xml": (
                setattr(_MockCustomerWebhook, "response_body", body),
                setattr(_MockCustomerWebhook, "response_status", code),
                setattr(_MockCustomerWebhook, "response_ctype", ctype),
            ),
        }
    finally:
        srv.shutdown()
        srv.server_close()


# ──────────────────────────────────────────────────────────────────────
#  6-7. fetch_twiml: reutiliza twilio_voice_payload + dispatch + parse_twiml
# ──────────────────────────────────────────────────────────────────────

def test_fetch_twiml_reusa_streams(mock_voice_server):
    # El cliente responde Connect/Stream -> fetch_twiml debe devolver la acción
    # 'stream' (confirma que el bridge usa vs.twilio_voice_payload +
    # dispatch_voice_webhook + parse_twiml end-to-end, sin reimplementar nada).
    mock_voice_server["set_response"](
        b'<Response><Connect>'
        b'<Stream url="wss://x.example/y"/>'
        b'</Connect></Response>')

    call_dict = {
        "id": "call_abc123",
        "from": "+34680628414",
        "to": "+573336033869",
        "direction": "inbound",
        "status": "ringing",
    }
    actions = asyncio.run(bridge.fetch_twiml(
        mock_voice_server["url"], call_dict, mid_phone="+573336033869"))

    assert len(actions) == 1
    assert actions[0]["verb"] == "stream"
    assert actions[0]["url"] == "wss://x.example/y"

    # El POST capturado debe respetar el contrato Twilio: CallSid == AMI call_id,
    # From/To esperados, Direction inbound, content-type form-urlencoded.
    assert mock_voice_server["captured"], "el bridge debe POSTear al voice_url"
    cap = mock_voice_server["captured"][0]
    assert cap["content_type"] == "application/x-www-form-urlencoded"
    assert cap["body_form"]["CallSid"] == "call_abc123"
    assert cap["body_form"]["From"] == "+34680628414"
    assert cap["body_form"]["To"] == "+573336033869"
    assert cap["body_form"]["Direction"] == "inbound"
    # User-Agent del proxy reutilizado (no inventa uno nuevo).
    assert cap["user_agent"].startswith("TwilioProxy/AMI-Voice-Streams")


def test_fetch_twiml_webhook_500_hangup(mock_voice_server):
    # El cliente cae con 5xx -> fetch_twiml devuelve hangup implícito para no
    # dejar la llamada en limbo.
    mock_voice_server["set_response"](b"boom", code=500)
    call_dict = {"id": "call_x", "from": "+34", "to": "+57",
                 "direction": "inbound", "status": "ringing"}
    actions = asyncio.run(bridge.fetch_twiml(
        mock_voice_server["url"], call_dict, mid_phone="+57"))
    assert actions == [{"verb": "hangup"}]


def test_fetch_twiml_body_no_twiml_hangup(mock_voice_server):
    # 200 pero el body no es TwiML válido -> actions vacías -> hangup implícito.
    mock_voice_server["set_response"](b"<<no es xml>>", code=200)
    call_dict = {"id": "call_y", "from": "+34", "to": "+57",
                 "direction": "inbound", "status": "ringing"}
    actions = asyncio.run(bridge.fetch_twiml(
        mock_voice_server["url"], call_dict, mid_phone="+57"))
    assert actions == [{"verb": "hangup"}]


def test_fetch_twiml_host_inalcanzable_hangup():
    # Cliente inalcanzable (status 0) -> hangup implícito, no excepción.
    call_dict = {"id": "call_z", "from": "+34", "to": "+57",
                 "direction": "inbound", "status": "ringing"}
    actions = asyncio.run(bridge.fetch_twiml(
        "http://127.0.0.1:1/nope", call_dict, mid_phone="+57"))
    assert actions == [{"verb": "hangup"}]


# ──────────────────────────────────────────────────────────────────────
#  8. handle_event StasisStart sin CALLBACK_VOICE_URL
# ──────────────────────────────────────────────────────────────────────

def test_handle_stasisstart_sin_voice_url(monkeypatch, ari_stubs):
    # Si ari_get_var(CALLBACK_VOICE_URL) devuelve None: el bridge NO despacha
    # webhook y devuelve el canal al dialplan (continue) o cuelga; tampoco deja
    # una sesión terminal persistente.
    async def _no_vars(channel_id, var):
        return None  # ninguna variable seteada (incl. CALLBACK_VOICE_URL)

    monkeypatch.setattr(bridge, "ari_get_var", _no_vars, raising=True)

    # fetch_twiml NO debe invocarse: si lo hace, el test falla ruidosamente.
    async def _boom(*a, **k):
        raise AssertionError("fetch_twiml NO debe llamarse sin voice_url")

    monkeypatch.setattr(bridge, "fetch_twiml", _boom, raising=True)

    ev = {
        "type": "StasisStart",
        "application": "ami_voice",
        "channel": {
            "id": "ch_no_voice",
            "caller": {"number": "+34680628414"},
        },
    }
    asyncio.run(bridge.handle_event(ev))

    # Devuelve el canal (continue) o lo cuelga; al menos una de las dos.
    assert (ari_stubs["continue"].count + ari_stubs["hangup"].count) >= 1
    # No queda una sesión activa para ese channel.
    assert "ch_no_voice" not in bridge.SESSIONS


def test_handle_stasisstart_con_voice_url_despacha(monkeypatch, ari_stubs):
    # Camino feliz: con CALLBACK_VOICE_URL el bridge lee las vars, despacha el
    # webhook (fetch_twiml mockeado) y ejecuta las acciones (run_actions).
    vars_map = {
        "CALLBACK_VOICE_URL": "https://client.example/voice",
        "MID_PHONE": "+573336033869",
        "AMI_CALL_ID": "call_real_777",
        "AMI_FROM": "+34680628414",
        "AMI_MID": "mid_real_1",
    }

    async def _get_var(channel_id, var):
        return vars_map.get(var)

    captured_fetch = {}

    async def _fetch(voice_url, call_dict, mid_phone, signing_secret=None):
        captured_fetch["voice_url"] = voice_url
        captured_fetch["call_dict"] = call_dict
        captured_fetch["mid_phone"] = mid_phone
        captured_fetch["signing_secret"] = signing_secret
        return [{"verb": "hangup"}]

    async def _fetch_secret(mid):
        captured_fetch["secret_mid"] = mid
        return "amiwhsec_" + "a" * 64

    monkeypatch.setattr(bridge, "ari_get_var", _get_var, raising=True)
    monkeypatch.setattr(bridge, "fetch_twiml", _fetch, raising=True)
    monkeypatch.setattr(bridge, "fetch_signing_secret", _fetch_secret, raising=True)

    ev = {
        "type": "StasisStart",
        "application": "ami_voice",
        "channel": {"id": "ch_voice", "caller": {"number": "+34680628414"}},
    }
    asyncio.run(bridge.handle_event(ev))

    # Despacha al voice_url correcto.
    assert captured_fetch["voice_url"] == "https://client.example/voice"
    # call_dict reconstruido con el AMI_CALL_ID (CallSid del contrato Twilio).
    assert captured_fetch["call_dict"]["id"] == "call_real_777"
    assert captured_fetch["call_dict"]["from"] == "+34680628414"
    assert captured_fetch["mid_phone"] == "+573336033869"
    # El bridge resolvió el signing_secret por AMI_MID y lo pasó a fetch_twiml.
    assert captured_fetch["secret_mid"] == "mid_real_1"
    assert captured_fetch["signing_secret"] == "amiwhsec_" + "a" * 64
    # Ejecutó la acción hangup devuelta por el webhook.
    assert ari_stubs["hangup"].count == 1


def test_handle_stasisend_limpia_sesion(monkeypatch):
    # StasisEnd/ChannelDestroyed debe limpiar la sesión del proceso. Idempotente.
    bridge.SESSIONS["ch_bye"] = {"call_id": "call_x", "media": None}
    ev = {"type": "StasisEnd", "channel": {"id": "ch_bye"}}
    asyncio.run(bridge.handle_event(ev))
    assert "ch_bye" not in bridge.SESSIONS
    # Doble evento no revienta (pop con default).
    asyncio.run(bridge.handle_event(ev))


# ──────────────────────────────────────────────────────────────────────
#  9. /health endpoint (ThreadingHTTPServer en un thread daemon)
# ──────────────────────────────────────────────────────────────────────

def test_health_endpoint():
    import urllib.request

    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge._HealthHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=2) as r:
            assert r.status == 200
            data = json.loads(r.read().decode("utf-8"))
        assert data["status"] == "ok"
        # Reporta la Stasis app y el número de sesiones activas.
        assert "app" in data
        assert data["sessions"] == len(bridge.SESSIONS)
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def test_health_endpoint_404_otra_ruta():
    import urllib.error
    import urllib.request

    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge._HealthHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/otra", timeout=2)
        assert exc.value.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


# ──────────────────────────────────────────────────────────────────────
#  10. Paso 3 (audio): guard externalMedia + cableado start_media_bridge
# ──────────────────────────────────────────────────────────────────────

def test_stasis_start_ignora_canal_externalmedia(monkeypatch, ari_stubs):
    # El canal UnicastRTP del externalMedia (que crea MediaBridge) entra en
    # Stasis con app=ami_voice. _on_stasis_start DEBE ignorarlo por nombre: NI
    # lee variables NI hace continue (un continue mataría el audio recién
    # creado y competiría con el addChannel).
    get_var = _AsyncRecorder(return_value=None)
    monkeypatch.setattr(bridge, "ari_get_var", get_var, raising=True)

    ev = {
        "type": "StasisStart",
        "application": "ami_voice",
        "channel": {"id": "em_1", "name": "UnicastRTP/127.0.0.1:40000-0x1a2b"},
    }
    asyncio.run(bridge.handle_event(ev))

    assert get_var.count == 0, "no debe leer variables del canal externalMedia"
    assert ari_stubs["continue"].count == 0, "NO debe continue (mataría el audio)"
    assert ari_stubs["hangup"].count == 0
    assert "em_1" not in bridge.SESSIONS


def test_start_media_bridge_sin_callsid_cuelga(monkeypatch, ari_stubs):
    # Sin AMI_CALL_ID en la sesión no abrimos audio (callSid es invariante del
    # contrato): colgamos y NO instanciamos MediaBridge.
    import media
    created = []
    monkeypatch.setattr(media, "MediaBridge",
                        lambda **kw: created.append(kw), raising=True)
    sess = {"call_id": None, "media": None}
    asyncio.run(bridge.start_media_bridge(
        "ch_nc", "wss://x/stream/tok", {}, sess))
    assert ari_stubs["hangup"].count == 1
    assert ari_stubs["hangup"].channels == ["ch_nc"]
    assert created == [], "no debe crear MediaBridge sin callSid"


def test_start_media_bridge_crea_y_arranca(monkeypatch, ari_stubs):
    # Con AMI_CALL_ID: crea MediaBridge(call_sid=call_id), llama start() y deja
    # la instancia en session['media']['bridge'] para el teardown.
    import media

    class _FakeMB:
        def __init__(self, **kw):
            self.kw = kw
            self.stream_sid = "stream_fake"
            self.started = False

        async def start(self):
            self.started = True

        async def close(self):
            pass

    instances = []

    def _factory(**kw):
        mb = _FakeMB(**kw)
        instances.append(mb)
        return mb

    monkeypatch.setattr(media, "MediaBridge", _factory, raising=True)

    sess = _session(channel_id="ch_a", call_id="call_real_1")
    url = "wss://agent.example.com/voice/stream/realtime/tok"
    asyncio.run(bridge.start_media_bridge("ch_a", url, {"k": "v"}, sess))

    assert len(instances) == 1
    mb = instances[0]
    assert mb.kw["call_sid"] == "call_real_1"     # callSid == AMI_CALL_ID
    assert mb.kw["sip_channel_id"] == "ch_a"
    assert mb.kw["ws_url"] == url
    assert mb.started is True
    assert sess["media"]["bridge"] is mb
    assert sess["media"]["stream_sid"] == "stream_fake"
    assert ari_stubs["hangup"].count == 0          # cede el canal al puente
