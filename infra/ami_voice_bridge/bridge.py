"""AMI Voice Bridge · capa SIGNALING del puente de voz Twilio-compatible.

Este proceso es el "Paso 2/3" del container ``ami_voice_bridge``. Cubre la
capa de SEÑALIZACIÓN entre Asterisk (vía ARI) y el cliente AI (openclaw /
Vapi / Retell / …) usando el contrato Twilio Voice + Media Streams. La capa
de AUDIO (RTP <-> WebSocket Media Streams) NO se implementa aquí todavía: el
verbo TwiML ``stream`` solo invoca el hook :func:`start_media_bridge`, que
deja documentado el contrato openclaw para el Paso 3.

Flujo (Paso 2):

    PSTN -> SIP -> Partner CO SBC -> SIP -> Asterisk (VPS)
        -- Stasis app ``ami_voice`` --> ARI WebSocket  (este proceso)
        -> HTTP webhook form-urlencoded estilo Twilio -> cliente AI
        <- TwiML  (Connect/Stream, Say, Hangup, Reject, Pause)
        -> ejecución de acciones contra ARI REST.

Diseño:

  * UN solo event loop ``asyncio`` (``asyncio.run(ari_events_loop())``).
  * ``ari_events_loop`` mantiene un WebSocket persistente contra
    ``ws://asterisk:8088/ari/events?app=ami_voice&api_key=user:pass`` con
    reconexión (backoff fijo 3 s) porque Asterisk puede no estar listo al
    arrancar el container.
  * Por cada frame JSON del WS se lanza ``asyncio.create_task(handle_event)``
    para no bloquear la recepción mientras se resuelven las vars del channel
    y el webhook.
  * Las llamadas HTTP bloqueantes (urllib a ARI REST y al voice_url del
    cliente) se delegan a ``loop.run_in_executor`` para no congelar el loop.
    Reutilizamos EXACTAMENTE el patrón Basic-auth de ``ami_telco/live.py``,
    evitando añadir httpx/aiohttp.
  * El servidor ``/health`` corre en un ``threading.Thread`` daemon aparte
    (http.server bloqueante), mismo patrón que ``ami_api.py``.

Reutiliza ``ami_voice_streams`` (Paso 1) para toda la lógica de
signaling Twilio: ``twilio_voice_payload``, ``dispatch_voice_webhook``,
``parse_twiml`` (vía dispatch) e ``is_safe_wss_url``. Ese módulo se copia
junto a este fichero en la imagen Docker, de modo que el import es directo.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

# ----------------------------------------------------------------------------
# Reutilizar el módulo de signaling del Paso 1.
#
# El Dockerfile copia ``ami_voice_streams.py`` junto a este fichero (en /app),
# así que está importable directamente. Para correr/test desde el repo, donde
# vive en la raíz, insertamos también la raíz del repo en sys.path. Importar
# ``ami_voice_streams`` NO arrastra ``ami_api``: ``new_voice_config`` (la única
# función que importa ``ami_api``) lo hace en un import diferido DENTRO de la
# función y el bridge NO la llama. Las cuatro funciones que sí usamos
# (twilio_voice_payload, dispatch_voice_webhook, parse_twiml, is_safe_wss_url)
# no tocan ami_api.
# ----------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Raíz del repo (infra/ami_voice_bridge -> infra -> repo) para correr en dev.
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import ami_voice_streams as vs  # noqa: E402  (import tras ajustar sys.path)


log = logging.getLogger("ami_voice_bridge")


# ============================ CONFIG ============================

class Config:
    """Configuración del bridge, cargada de env. Inmutable tras load_config()."""

    def __init__(self) -> None:
        self.ari_base_http: str = (
            os.environ.get("AMI_ARI_URL", "http://asterisk:8088/ari").rstrip("/")
        )
        self.ari_user: str = os.environ.get("AMI_ARI_USERNAME", "")
        self.ari_pass: str = os.environ.get("AMI_ARI_PASSWORD", "")
        self.ari_app: str = os.environ.get("AMI_ARI_APP", "ami_voice")
        self.webhook_timeout: float = float(
            os.environ.get("AMI_VOICE_WEBHOOK_TIMEOUT", "5")
        )
        self.health_port: int = int(
            os.environ.get("AMI_VOICE_BRIDGE_HEALTH_PORT", "8090")
        )
        self.log_level: str = os.environ.get("AMI_VOICE_LOG_LEVEL", "INFO")


# Config global del proceso (se inicializa en main(); los tests la setean
# directamente o usan la default). Tener un default permite que helpers que la
# leen funcionen en tests sin llamar a main().
CONFIG: Config = Config()


def load_config() -> Config:
    """(Re)lee la configuración desde el entorno y la fija como CONFIG global."""
    global CONFIG
    CONFIG = Config()
    return CONFIG


def _events_ws_url(http_url: str, app: str, user: str, password: str) -> str:
    """Deriva la URL del WebSocket de eventos ARI a partir de la base REST.

    ``http://asterisk:8088/ari``  ->  ``ws://asterisk:8088/ari/events?...``
    ``https://host/ari``          ->  ``wss://host/ari/events?...``

    Añade el querystring ``app=<app>&api_key=<user>:<pass>&subscribeAll=false``
    de modo que Asterisk entregue solo los eventos de la Stasis app indicada.
    El ``api_key`` lleva ``user:pass`` (Basic en query, formato de ARI).
    """
    base = http_url.rstrip("/")
    parts = urllib.parse.urlsplit(base)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    # La ruta base suele terminar en '/ari'; el endpoint de eventos es
    # '<ruta>/events'. Si por lo que sea no termina en /ari, igualmente
    # añadimos '/events' a la ruta existente.
    path = parts.path.rstrip("/") + "/events"
    qs = urllib.parse.urlencode({
        "app": app,
        "api_key": f"{user}:{password}",
        "subscribeAll": "false",
    })
    return urllib.parse.urlunsplit((scheme, parts.netloc, path, qs, ""))


# ===================== ESTADO POR CHANNEL =====================
#
# SESSIONS mapea channel_id -> dict de sesión runtime. NO persiste: es estado
# puro de proceso, espejo del diseño stateless de live.py, pero aquí SÍ
# mantenemos la sesión mientras la llamada vive. Solo se accede desde el event
# loop, así que no hace falta lock.
SESSIONS: dict[str, dict] = {}

# Flag global de salud del WS de eventos (lo lee /health).
_ARI_WS_CONNECTED: bool = False


# ===================== HELPERS ARI REST =====================
#
# Basic auth + urllib stdlib, replicando el patrón de ami_telco/live.py para no
# introducir una dependencia HTTP nueva. Las llamadas son bloqueantes, así que
# las ejecutamos siempre vía loop.run_in_executor para no congelar el loop.

def _ari_auth_header() -> str:
    """'Basic <base64(user:pass)>' — igual que live.py:hangup_call."""
    token = base64.b64encode(
        f"{CONFIG.ari_user}:{CONFIG.ari_pass}".encode()
    ).decode()
    return "Basic " + token


def _blocking_request(method: str, url: str,
                      body: "dict | None" = None,
                      timeout: float = 10.0) -> "tuple[int, bytes]":
    """Ejecuta una request ARI REST bloqueante. Devuelve (status, body_bytes).

    status == 0 indica fallo de red/transport (host inalcanzable, timeout, …).
    """
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", _ari_auth_header())
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if e.fp else b"")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e).encode("utf-8", "replace")


async def _ari_request(method: str, url: str,
                       body: "dict | None" = None,
                       timeout: float = 10.0) -> "tuple[int, bytes]":
    """Versión async: corre _blocking_request en el executor por defecto."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _blocking_request(method, url, body, timeout)
    )


async def ari_get_var(channel_id: str, var: str) -> "str | None":
    """GET /channels/{id}/variable?variable=<var>.

    ARI responde ``{"value": "..."}``. Devuelve el value, o None si la variable
    no existe (404) o si hay error de transporte. No lanza.
    """
    url = (
        f"{CONFIG.ari_base_http}/channels/{urllib.parse.quote(channel_id)}"
        f"/variable?variable={urllib.parse.quote(var)}"
    )
    status, raw = await _ari_request("GET", url)
    if status == 200:
        try:
            return json.loads(raw.decode("utf-8", "replace")).get("value")
        except (ValueError, AttributeError):
            return None
    if status == 404:
        # Variable no seteada en el channel: caso normal, no es error.
        return None
    log.warning("ari_get_var(%s, %s) status=%s", channel_id, var, status)
    return None


async def ari_play(channel_id: str, media: str) -> None:
    """POST /channels/{id}/play?media=<media>.

    ARI define ``media`` como query param de la operación play; el body JSON se
    reserva para la clave ``variables``. Si se envía en el body, Asterisk
    responde 400 "Missing parameter media" y nunca reproduce. STUB v1: loguea y
    hace el POST best-effort. El TTS real (síntesis de la frase de <Say>) es v2;
    aquí solo reproducimos un placeholder/sound.
    """
    url = (
        f"{CONFIG.ari_base_http}/channels/{urllib.parse.quote(channel_id)}/play"
        f"?media={urllib.parse.quote(media)}"
    )
    log.info("ari_play (stub v1) channel=%s media=%s", channel_id, media)
    status, raw = await _ari_request("POST", url)
    if not (200 <= status < 300):
        log.warning(
            "ari_play channel=%s media=%s -> status=%s body=%s",
            channel_id, media, status, raw[:200],
        )


async def ari_hangup(channel_id: str) -> None:
    """DELETE /channels/{id} — provoca BYE/HANGUP. Idempotente, swallow errores
    (igual que live.py:hangup_call)."""
    url = f"{CONFIG.ari_base_http}/channels/{urllib.parse.quote(channel_id)}"
    status, _ = await _ari_request("DELETE", url)
    log.info("ari_hangup channel=%s status=%s", channel_id, status)


async def ari_continue(channel_id: str) -> None:
    """POST /channels/{id}/continue — devuelve el channel al dialplan.

    Usado como alternativa a colgar cuando no hay nada que hacer en Stasis
    (p.ej. falta voice_url). Idempotente, swallow errores.
    """
    url = (
        f"{CONFIG.ari_base_http}/channels/{urllib.parse.quote(channel_id)}/continue"
    )
    status, _ = await _ari_request("POST", url, body={})
    log.info("ari_continue channel=%s status=%s", channel_id, status)


# ===================== WEBHOOK DISPATCH =====================

async def fetch_twiml(voice_url: str, call_dict: dict,
                      mid_phone: str) -> "list[dict]":
    """Despacha el webhook estilo Twilio al voice_url y devuelve las acciones.

    Reutiliza vs.twilio_voice_payload (arma CallSid/From/To/Direction/…) y
    vs.dispatch_voice_webhook (POST form-urlencoded + parse_twiml interno).

    Si el cliente no responde 2xx o el body no es TwiML válido (acciones
    vacías), devuelve un hangup implícito ``[{'verb': 'hangup'}]`` para no
    dejar la llamada en limbo, tal como documenta dispatch_voice_webhook.
    """
    payload = vs.twilio_voice_payload(call_dict, mid_phone)
    loop = asyncio.get_running_loop()
    status, body, actions = await loop.run_in_executor(
        None,
        lambda: vs.dispatch_voice_webhook(
            voice_url, payload, "POST", CONFIG.webhook_timeout
        ),
    )
    if not (200 <= status < 300) or not actions:
        log.warning(
            "fetch_twiml voice_url=%s status=%s actions=%d -> hangup implícito "
            "(body=%s)",
            voice_url, status, len(actions), body[:200],
        )
        return [{"verb": "hangup"}]
    log.info(
        "fetch_twiml voice_url=%s status=%s -> %d acciones %s",
        voice_url, status, len(actions), [a.get("verb") for a in actions],
    )
    return actions


# ===================== EJECUTOR DE ACCIONES =====================

async def run_actions(channel_id: str, actions: "list[dict]",
                      session: dict) -> None:
    """Ejecuta las acciones TwiML en orden, parando al primer verbo terminal.

    Terminales (hacen ``break``): reject, hangup, stream.
    No terminales (continúan): say, pause.
    """
    for a in actions:
        verb = a.get("verb")
        if verb == "reject":
            # En v1 reject == colgar. El dialplan ya rechaza con 21 si no hay
            # endpoint; aquí simplemente cerramos el channel en Stasis.
            log.info("run_actions channel=%s verb=reject -> hangup", channel_id)
            await ari_hangup(channel_id)
            break
        elif verb == "hangup":
            log.info("run_actions channel=%s verb=hangup", channel_id)
            await ari_hangup(channel_id)
            break
        elif verb == "say":
            # STUB v1: no hay TTS. Reproducimos un placeholder y SEGUIMOS
            # (Say es no-terminal en TwiML).
            log.info(
                "run_actions channel=%s verb=say text=%r -> TTS no implementado "
                "v1, reproduciendo placeholder",
                channel_id, (a.get("text") or "")[:80],
            )
            await ari_play(channel_id, "sound:tt-monkeys")
            continue
        elif verb == "pause":
            length = float(a.get("length_s", 1) or 0)
            log.info("run_actions channel=%s verb=pause %.1fs", channel_id, length)
            if length > 0:
                await asyncio.sleep(length)
            continue
        elif verb == "stream":
            url = a.get("url", "")
            if not vs.is_safe_wss_url(url):
                log.error(
                    "run_actions channel=%s verb=stream url no segura (%r) -> "
                    "hangup", channel_id, url,
                )
                await ari_hangup(channel_id)
                break
            # Terminal: cede el channel al puente de audio (hook Paso 3).
            await start_media_bridge(
                channel_id, url, a.get("params", {}), session
            )
            break
        else:
            log.debug("run_actions channel=%s verbo ignorado %r", channel_id, verb)
            continue


# ===================== HOOK PASO 3 (AUDIO) =====================

async def start_media_bridge(channel_id: str, ws_url: str,
                             params: dict, session: dict) -> None:
    """HOOK Paso 3 — capa de AUDIO. En Paso 2 SOLO registra la intención.

    En Paso 2 esta función no abre ni RTP ni el WebSocket Media Streams: solo
    guarda la intención en la sesión y loguea. El channel queda en Stasis
    hasta que cuelguen — aceptable porque sin audio la llamada no progresa;
    esto es un PoC de signaling.
    """
    session["media"] = {"ws_url": ws_url, "params": params, "stream_sid": None}
    log.info(
        "start_media_bridge HOOK (TODO Paso 3): channel=%s call_sid=%s ws=%s "
        "params=%s",
        channel_id, session.get("call_id"), ws_url, params,
    )
    # TODO Paso 3 (contrato openclaw, NO implementar aquí):
    #   1. ARI POST /channels/externalMedia  (NO es sub-recurso del channel;
    #        crea un canal UnicastRTP nuevo). Params en QUERY STRING:
    #          ?app=ami_voice&external_host=<bridge_rtp_ip:port>&format=ulaw
    #          &transport=udp&encapsulation=rtp
    #        Devuelve el id del UnicastRTP channel μ-law 8 kHz. El body JSON se
    #        reserva para 'variables'.
    #   2. Crear un bridge ARI mixing (POST /bridges) y añadirle el SIP channel
    #        + el externalMedia channel (POST /bridges/{id}/addChannel).
    #   3. Abrir cliente WS hacia ws_url (wss del cliente openclaw). Handshake
    #        Media Streams Twilio 1:1:
    #          AMI->cliente: {'event':'start','start':{
    #              'streamSid':<id de sesión>,
    #              'callSid':<AMI_CALL_ID = call_xxx, ÚNICO por llamada>}}
    #   4. Loop bidireccional μ-law base64:
    #        RTP(asterisk) -> b64 ->
    #          {'event':'media','media':{'payload':<b64>,'timestamp':..,
    #           'track':'inbound'}} -> cliente
    #        cliente ->
    #          {'event':'media','streamSid':..,'media':{'payload':<b64>}}
    #          -> decode -> RTP a externalMedia
    #        soportar {'event':'mark'} y {'event':'clear'} entrantes del cliente;
    #        emitir {'event':'mark'} y {'event':'stop'} al cerrar.
    #   5. PACER de salida: 160 bytes (20 ms) de μ-law por frame; ritmo 20 ms
    #        (no ráfagas).
    #   El callSid del 'start' DEBE ser el AMI_CALL_ID (call_xxx) leído del
    #   channel; streamSid puede ser cualquier id nuevo de sesión.
    #   Toda esta lógica RTP<->WS NO se escribe en Paso 2.


# ===================== MANEJO DE EVENTOS ARI =====================

async def handle_event(ev: dict) -> None:
    """Despacha un evento ARI recibido por el WebSocket de eventos."""
    t = ev.get("type")
    try:
        if t == "StasisStart":
            await _on_stasis_start(ev)
        elif t in ("StasisEnd", "ChannelDestroyed"):
            _on_channel_gone(ev)
        else:
            log.debug("evento ARI ignorado type=%s", t)
    except Exception:  # noqa: BLE001  un handler no debe tumbar el loop
        log.exception("handle_event falló type=%s", t)


async def _on_stasis_start(ev: dict) -> None:
    ch = ev.get("channel") or {}
    cid = ch.get("id")
    if not cid:
        log.warning("StasisStart sin channel.id: %s", ev)
        return

    # Variables seteadas por el dialplan en la rama Stasis (ver ami_changes).
    voice_url = await ari_get_var(cid, "CALLBACK_VOICE_URL")
    mid_phone = await ari_get_var(cid, "MID_PHONE")
    call_id = await ari_get_var(cid, "AMI_CALL_ID")
    frm = await ari_get_var(cid, "AMI_FROM")

    if not voice_url:
        # Nada que hacer en Stasis: devolvemos el channel al dialplan.
        log.warning(
            "StasisStart channel=%s sin CALLBACK_VOICE_URL -> continue", cid
        )
        await ari_continue(cid)
        return

    caller_num = (ch.get("caller") or {}).get("number", "")
    call_dict = {
        "id": call_id or cid,
        "from": frm or caller_num,
        "to": mid_phone or "",
        "direction": "inbound",
        "status": "ringing",
    }
    SESSIONS[cid] = {
        "call_id": call_id or cid,
        "mid_phone": mid_phone,
        "voice_url": voice_url,
        "from": call_dict["from"],
        "started": ev.get("timestamp"),
        "media": None,
    }
    log.info(
        "StasisStart channel=%s call_id=%s from=%s to=%s voice_url=%s",
        cid, call_dict["id"], call_dict["from"], call_dict["to"], voice_url,
    )
    actions = await fetch_twiml(voice_url, call_dict, mid_phone or "")
    await run_actions(cid, actions, SESSIONS[cid])


def _on_channel_gone(ev: dict) -> None:
    cid = (ev.get("channel") or {}).get("id")
    if not cid:
        return
    sess = SESSIONS.pop(cid, None)
    if sess is None:
        return
    if sess.get("media"):
        # TODO Paso 3: cerrar el WebSocket Media Streams y el RTP/externalMedia
        # asociados a esta sesión antes de descartarla.
        log.info("cleanup channel=%s (tenía media activa, TODO Paso 3)", cid)
    else:
        log.info("cleanup channel=%s", cid)


# ===================== LOOP PRINCIPAL (reconexión) =====================

async def ari_events_loop() -> None:
    """Mantiene el WebSocket de eventos ARI con reconexión indefinida.

    Asterisk puede no estar listo al arrancar el container, así que ante
    cualquier fallo reintentamos con un backoff fijo de 3 s.
    """
    global _ARI_WS_CONNECTED
    url = _events_ws_url(
        CONFIG.ari_base_http, CONFIG.ari_app, CONFIG.ari_user, CONFIG.ari_pass
    )
    # Redacta credenciales en el log.
    safe_url = url.split("api_key=", 1)[0] + "api_key=***"
    while True:
        try:
            async with websockets.connect(
                url, ping_interval=20, ping_timeout=20
            ) as ws:
                _ARI_WS_CONNECTED = True
                log.info(
                    "conectado a ARI events app=%s (%s)", CONFIG.ari_app, safe_url
                )
                async for raw in ws:
                    try:
                        ev = json.loads(raw)
                    except (ValueError, TypeError):
                        log.warning("frame ARI no-JSON ignorado: %r", raw[:200])
                        continue
                    asyncio.create_task(handle_event(ev))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  reconexión robusta
            log.warning("ARI WS caído, reintento en 3s: %s", e)
        finally:
            _ARI_WS_CONNECTED = False
        await asyncio.sleep(3)


# ===================== /HEALTH =====================

class _HealthHandler(BaseHTTPRequestHandler):
    """Servidor /health para el healthcheck de Docker. Bloqueante; corre en un
    thread daemon aparte del event loop (mismo patrón http.server que
    ami_api.py)."""

    def do_GET(self):  # noqa: N802  (firma de BaseHTTPRequestHandler)
        if self.path == "/health":
            payload = json.dumps({
                "status": "ok",
                "app": CONFIG.ari_app,
                "sessions": len(SESSIONS),
                "ari_ws_connected": _ARI_WS_CONNECTED,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args, **kwargs):  # silenciar logs de acceso HTTP
        pass


def start_health_server(port: int) -> ThreadingHTTPServer:
    """Arranca el servidor /health en un thread daemon y devuelve la instancia."""
    srv = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, name="health", daemon=True)
    t.start()
    log.info("health server escuchando en :%d/health", port)
    return srv


# ===================== MAIN =====================

def main() -> None:
    load_config()
    logging.basicConfig(
        level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start_health_server(CONFIG.health_port)
    log.info(
        "ami_voice_bridge arrancando · ARI=%s app=%s",
        CONFIG.ari_base_http, CONFIG.ari_app,
    )
    asyncio.run(ari_events_loop())


if __name__ == "__main__":
    main()
