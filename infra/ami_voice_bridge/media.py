"""AMI Voice Bridge · capa de AUDIO (Paso 3) del puente de voz.

Este módulo implementa el puente RTP(UDP) <-> WebSocket Media Streams que en
el Paso 2 era el hook stub :func:`bridge.start_media_bridge`. AMI NO hace
IA/STT/TTS: el cliente (openclaw / Vapi / Retell / …) ya tiene su propio
OpenAI Realtime por su lado. AMI es SOLO la pipa de audio G.711 μ-law (PCMU):
mueve bytes, no transcodifica nada.

Diseño — lógica PURA separada de la I/O para poder testear sin red:

  * SECCIÓN 1: constantes del formato (PCMU PT=0, 8 kHz, 160 bytes / 20 ms).
  * SECCIÓN 2: RTP PURO — ``parse_rtp`` (tolera CSRC/extension/padding al
    parsear, defensivo, nunca lanza) y ``build_rtp`` (solo cabecera base de
    12 bytes; nunca emite CSRC/extension/padding).
  * SECCIÓN 3: PACER PURO — :class:`RtpSender` bufferiza μ-law del WS y
    ``next_packet`` emite un RTP de 160 B SOLO cuando hay ≥20 ms (seq+1,
    ts+160, marker bit en el primer paquete de un talkspurt).
  * SECCIÓN 4: frames Media Streams PUROS — encode/decode JSON EXACTO del
    contrato openclaw (start/media/mark/stop salientes; media/mark/clear
    entrantes), base64 μ-law passthrough.
  * SECCIÓN 5: LATCHING PURO — :class:`RtpLatch` aprende la addr del PRIMER
    datagrama RTP de Asterisk (RTP simétrico).
  * SECCIÓN 6: I/O UDP — :class:`_RtpProtocol` (asyncio.DatagramProtocol).
  * SECCIÓN 7: I/O orquestación — :class:`MediaBridge` por sesión: bind UDP,
    ARI externalMedia -> bridge mixing -> addChannel, cliente WS, tasks de
    fondo (recv-loop + send-loop + pacer-loop) y teardown idempotente.

El flujo de audio (μ-law passthrough):

    Asterisk --UDP RTP--> _RtpProtocol --parse--> Queue --WS--> cliente
    cliente  --WS media--> RtpSender.feed --pacer 20 ms--> build_rtp --UDP--> Asterisk

``bridge.py`` solo construye un :class:`MediaBridge`, llama a ``start()`` (que
arranca todo y RETORNA, sin bloquear ``handle_event``) y a ``close()`` en el
teardown. Toda la complejidad de audio queda aquí, importable y testeable.

Dependencias: ``websockets`` (YA es dep del bridge) + stdlib (asyncio, base64,
struct, random, uuid, json, urllib.parse). CERO deps nuevas.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import struct
import urllib.parse
from uuid import uuid4

log = logging.getLogger("ami_voice_bridge.media")


# ============================================================================
# SECCIÓN 1 — CONSTANTES
# ============================================================================
#
# G.711 μ-law (PCMU), 8 kHz mono. 1 byte por muestra (μ-law). Un frame de
# 20 ms son 160 muestras = 160 bytes. RTP payload type 0 = PCMU.

PCMU_PT: int = 0
SAMPLES_PER_FRAME: int = 160      # 20 ms @ 8 kHz; también el incremento de ts RTP
FRAME_BYTES: int = 160            # 160 muestras μ-law = 160 bytes (1 B/muestra)
FRAME_MS: int = 20                # duración de un frame en ms
PACER_INTERVAL: float = 0.020     # cadencia del pacer de salida (20 ms exactos)
RTP_VERSION: int = 2              # RTP versión 2 (RFC 3550)
RTP_HEADER_LEN: int = 12          # cabecera base sin CSRC/extension


# ============================================================================
# SECCIÓN 2 — RTP PURO (sin red, testeable)
# ============================================================================

def parse_rtp(pkt: bytes) -> "dict | None":
    """Parsea un paquete RTP y devuelve sus campos + el payload, o ``None``.

    DEFENSIVO: nunca lanza. En la red llega basura (escaneos, STUN, paquetes
    truncados); ante cualquier malformación devolvemos ``None`` y el caller lo
    descarta. Tolera CC/CSRC, cabecera de extensión (X) y padding (P) al
    PARSEAR (aunque nosotros nunca los emitamos en :func:`build_rtp`).

    Layout RTP (RFC 3550 §5.1)::

        0                   1                   2                   3
        |V=2|P|X| CC  |M|   PT    |       sequence number         |
        |                           timestamp                       |
        |             synchronization source (SSRC) identifier      |
        |            contributing source (CSRC) identifiers ...      |  CC*4 bytes
        |         (extension header si X=1: id + len + len*4 bytes)  |

    Devuelve ``{'version','marker','pt','seq','ts','ssrc','payload'}``.
    """
    try:
        if not pkt or len(pkt) < RTP_HEADER_LEN:
            return None
        byte0 = pkt[0]
        version = (byte0 >> 6) & 0x3
        if version != RTP_VERSION:
            return None  # no es RTPv2: descartar (STUN, ruido, versión vieja)
        padding = (byte0 >> 5) & 0x1
        ext = (byte0 >> 4) & 0x1
        cc = byte0 & 0x0F
        byte1 = pkt[1]
        marker = (byte1 >> 7) & 0x1
        pt = byte1 & 0x7F
        seq, ts, ssrc = struct.unpack(">HII", pkt[2:12])

        # Saltar la lista de CSRC (CC identificadores de 32 bits cada uno).
        offset = RTP_HEADER_LEN + cc * 4
        if offset > len(pkt):
            return None

        # Saltar la cabecera de extensión (X=1): uint16 id + uint16 len_words,
        # seguido de len_words palabras de 32 bits.
        if ext:
            if offset + 4 > len(pkt):
                return None
            _ext_id, len_words = struct.unpack(">HH", pkt[offset:offset + 4])
            offset += 4 + len_words * 4
            if offset > len(pkt):
                return None

        # Padding (P=1): el ÚLTIMO byte del paquete indica cuántos bytes de
        # padding (incluido ese byte) hay que recortar del final del payload.
        end = len(pkt)
        if padding:
            pad = pkt[-1]
            if pad <= 0 or pad > (end - offset):
                return None  # padding inconsistente -> paquete malformado
            end -= pad

        payload = pkt[offset:end]
        return {
            "version": version,
            "marker": marker,
            "pt": pt,
            "seq": seq,
            "ts": ts,
            "ssrc": ssrc,
            "payload": payload,
        }
    except Exception:  # noqa: BLE001  parse defensivo: jamás propagar
        return None


def build_rtp(payload: bytes, seq: int, ts: int, ssrc: int,
              marker: bool = False, pt: int = PCMU_PT) -> bytes:
    """Construye un paquete RTP con cabecera base de 12 bytes + payload.

    Nunca emitimos CSRC, extensión ni padding: solo la cabecera base
    (P=0, X=0, CC=0). Los toleramos AL PARSEAR (ver :func:`parse_rtp`), no al
    construir. seq/ts/ssrc se enmascaran a su ancho (uint16/uint32/uint32).
    """
    byte0 = (RTP_VERSION << 6)                       # V=2, P=0, X=0, CC=0
    byte1 = ((1 if marker else 0) << 7) | (pt & 0x7F)
    header = struct.pack(
        ">BBHII",
        byte0,
        byte1,
        seq & 0xFFFF,
        ts & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    return header + payload


def new_ssrc() -> int:
    """SSRC aleatorio de 32 bits. Se genera UNA vez por sesión y queda fijo."""
    return random.getrandbits(32)


# ============================================================================
# SECCIÓN 3 — PACER PURO (clase RtpSender)
# ============================================================================

class RtpSender:
    """Bufferiza μ-law del WS y emite paquetes RTP de 160 B a ritmo de 20 ms.

    Lógica PURA, sin red ni reloj real: el "reloj" es implícito (el caller
    invoca :meth:`next_packet` una vez por tick de 20 ms). seq arranca
    aleatorio (uint16) y ts arranca aleatorio (uint32) según RFC 3550 §5.1.
    El SSRC es fijo por sesión.

    Round-trip testeable: ``feed(N*160)`` seguido de N ``next_packet()`` da
    seq creciente, ts += 160, primer ``marker=1`` y el resto ``marker=0``;
    un ``clear()`` intermedio descarta lo pendiente y rearma el marker.
    """

    def __init__(self, ssrc: int) -> None:
        self.ssrc: int = ssrc & 0xFFFFFFFF
        self.seq: int = random.getrandbits(16)
        self.ts: int = random.getrandbits(32)
        self.outbuf: bytearray = bytearray()
        # El primer paquete tras arranque/clear lleva marker=1 (inicio de
        # talkspurt). Se rearma con clear().
        self._talkspurt_start: bool = True

    def feed(self, ulaw_bytes: bytes) -> None:
        """Acumula μ-law llegado del WS (puede venir en ráfaga / >160 B)."""
        if ulaw_bytes:
            self.outbuf.extend(ulaw_bytes)

    def clear(self) -> None:
        """Descarta el buffer pendiente y rearma el marker bit.

        Corresponde al evento ``{'event':'clear'}`` del cliente: tira lo que
        no se ha enviado (barge-in) y el próximo paquete reinicia talkspurt.
        """
        self.outbuf.clear()
        self._talkspurt_start = True

    def next_packet(self) -> "bytes | None":
        """Devuelve un RTP de 160 B si hay ≥20 ms bufferizados, si no ``None``.

        Es la unidad PURA que el pacer-task llama cada 20 ms. NO emite ráfagas
        ni medio-frames: si ``len(outbuf) < 160`` devuelve ``None`` (aún no hay
        un frame completo). marker=1 solo en el primer paquete del talkspurt.
        """
        if len(self.outbuf) < FRAME_BYTES:
            return None
        frame = bytes(self.outbuf[:FRAME_BYTES])
        del self.outbuf[:FRAME_BYTES]
        marker = self._talkspurt_start
        pkt = build_rtp(frame, self.seq, self.ts, self.ssrc, marker=marker)
        self.seq = (self.seq + 1) & 0xFFFF
        self.ts = (self.ts + SAMPLES_PER_FRAME) & 0xFFFFFFFF
        self._talkspurt_start = False
        return pkt

    def drain_padding(self) -> None:
        """Descarta un resto < 160 B al final (no se rellena con silencio).

        El contrato es passthrough μ-law puro: no inventamos muestras de
        relleno. Lo que no completa un frame de 20 ms se tira.
        """
        if len(self.outbuf) < FRAME_BYTES:
            self.outbuf.clear()


# ============================================================================
# SECCIÓN 4 — FRAMES MEDIA STREAMS PUROS (JSON EXACTO del contrato openclaw)
# ============================================================================
#
# Contrato (NO inventar variantes):
#   AMI -> cliente:
#     {"event":"start","start":{"streamSid":"...","callSid":"..."}}
#     {"event":"media","media":{"payload":"<b64>","timestamp":"...","track":"inbound"}}
#     {"event":"mark","mark":{"name":"..."}}
#     {"event":"stop"}
#   cliente -> AMI:
#     {"event":"media","streamSid":"...","media":{"payload":"<b64>"}}
#     {"event":"mark","streamSid":"...","mark":{"name":"..."}}
#     {"event":"clear","streamSid":"..."}
#
# callSid del start == AMI_CALL_ID (call_xxx), invariante DURA.
# streamSid == id nuevo por sesión Stream.

def encode_start(stream_sid: str, call_sid: str) -> str:
    """Frame ``start`` saliente. ``callSid`` DEBE ser el AMI_CALL_ID."""
    return json.dumps({
        "event": "start",
        "start": {"streamSid": stream_sid, "callSid": call_sid},
    })


def encode_media(payload_b64: str, timestamp_ms: int) -> str:
    """Frame ``media`` saliente (track inbound = audio del llamante).

    ``timestamp`` es ms-desde-inicio-del-stream como STRING (lo deriva el
    caller con ``n_inbound_frames * FRAME_MS``), NO el timestamp RTP crudo.
    """
    return json.dumps({
        "event": "media",
        "media": {
            "payload": payload_b64,
            "timestamp": str(timestamp_ms),
            "track": "inbound",
        },
    })


def encode_mark(name: str) -> str:
    """Frame ``mark`` saliente."""
    return json.dumps({"event": "mark", "mark": {"name": name}})


def encode_stop() -> str:
    """Frame ``stop`` saliente (cierre del stream)."""
    return json.dumps({"event": "stop"})


def decode_client_frame(raw: "str | bytes") -> "dict | None":
    """Decodifica un frame entrante del cliente. ``None`` si no es JSON-objeto.

    DEFENSIVO: ante JSON inválido o un valor que no sea un objeto devolvemos
    ``None`` (el caller lo ignora). Devuelve el dict crudo; el dispatch por
    ``ev['event'] in ('media','mark','clear')`` lo hace el caller.
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def b64_encode_ulaw(ulaw: bytes) -> str:
    """μ-law bytes -> base64 ASCII (passthrough, no transcodifica)."""
    return base64.b64encode(ulaw).decode("ascii")


def b64_decode_ulaw(payload: str) -> bytes:
    """base64 -> μ-law bytes (passthrough). DEFENSIVO: basura -> ``b''``.

    Los bytes b64 son YA PCMU; no se transcodifica nada: pasan directos a/desde
    el payload RTP.
    """
    try:
        return base64.b64decode(payload, validate=True)
    except Exception:  # noqa: BLE001  payload corrupto del cliente -> descartar
        return b""


# ============================================================================
# SECCIÓN 5 — LATCHING PURO (clase RtpLatch)
# ============================================================================

class RtpLatch:
    """Aprende la addr de origen del PRIMER datagrama RTP de Asterisk.

    El canal UnicastRTP de externalMedia NO es un endpoint PJSIP, así que el
    ``rtp_symmetric`` de pjsip.conf no le aplica: descubrimos a dónde devolver
    el audio observando de dónde llega el primer datagrama (latching). Una vez
    fijada, no cambia: datagramas posteriores de otra addr no la modifican.
    """

    def __init__(self) -> None:
        self.addr: "tuple | None" = None

    def learn(self, addr: "tuple") -> None:
        """Fija la addr en el primer datagrama; ignora los posteriores."""
        if self.addr is None and addr is not None:
            self.addr = addr

    def target(self) -> "tuple | None":
        """addr aprendida (a dónde enviar el RTP de salida) o ``None``."""
        return self.addr


# ============================================================================
# SECCIÓN 6 — I/O: UDP DatagramProtocol
# ============================================================================

class _RtpProtocol(asyncio.DatagramProtocol):
    """Protocolo UDP del socket RTP. Encola el payload μ-law entrante hacia WS.

    Cada datagrama: aprende la addr de origen (latching) y parsea el RTP. Si
    es válido y trae payload, invoca ``on_packet(payload)`` (corre EN el event
    loop, sin threads). DEFENSIVO ante basura de red: parse_rtp no lanza.
    """

    def __init__(self, on_packet, latch: RtpLatch) -> None:
        self._on_packet = on_packet
        self._latch = latch
        self.transport: "asyncio.DatagramTransport | None" = None

    def connection_made(self, transport) -> None:  # noqa: D401
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        # RTP simétrico: la primera addr de la que recibimos es a la que
        # devolveremos el audio de salida.
        self._latch.learn(addr)
        parsed = parse_rtp(data)
        if parsed and parsed["payload"]:
            try:
                self._on_packet(parsed["payload"])
            except Exception:  # noqa: BLE001  un fallo del callback no rompe el socket
                log.debug("on_packet falló para datagrama de %s", addr, exc_info=True)

    def error_received(self, exc) -> None:
        # ICMP port unreachable y similares: log y seguir, no romper.
        log.debug("RTP socket error_received: %s", exc)

    def connection_lost(self, exc) -> None:
        log.debug("RTP socket connection_lost: %s", exc)


# ============================================================================
# SECCIÓN 7 — I/O: MediaBridge (orquestador por sesión)
# ============================================================================

class MediaBridge:
    """Puente de audio de UNA sesión: RTP(UDP) <-> WebSocket Media Streams.

    Orquesta el ciclo de vida completo:
      1. Bind de un socket UDP local (``CONFIG.rtp_port``) para el RTP.
      2. ARI ``POST /channels/externalMedia`` -> canal UnicastRTP μ-law.
      3. ARI bridge mixing + addChannel (SIP channel + externalMedia channel).
      4. Cliente WebSocket al ``ws_url`` del cliente + handshake ``start``.
      5. Tasks de fondo: recv-loop (WS->RtpSender), send-loop (Queue->WS) y
         pacer-loop (RtpSender->UDP a 20 ms).

    ``start()`` arranca todo y RETORNA (no bloquea ``handle_event``): los
    loops corren como tasks. ``close()`` es idempotente/reentrante y limpia
    WS + UDP + ari_hangup(externalMedia) + DELETE /bridges/{id} sin fugas.

    El ``call_sid`` lo recibe YA resuelto (AMI_CALL_ID); el caller en
    ``bridge.py`` NO debe abrir audio si falta.
    """

    def __init__(self, channel_id: str, sip_channel_id: str, ws_url: str,
                 call_sid: str, params: "dict | None" = None) -> None:
        self.channel_id = channel_id
        self.sip_channel_id = sip_channel_id
        self.ws_url = ws_url
        self.call_sid = call_sid
        self.params = params or {}

        # streamSid libre por sesión (el callSid es el invariante, no éste).
        self.stream_sid = f"stream_{uuid4().hex}"
        self.ssrc = new_ssrc()
        self.sender = RtpSender(self.ssrc)
        self.latch = RtpLatch()

        # Estado de I/O (se rellena en start()).
        self.udp_transport: "asyncio.DatagramTransport | None" = None
        self.udp_protocol: "_RtpProtocol | None" = None
        self.rtp_port: "int | None" = None  # puerto UDP efímero real (tras bind)
        self.ws = None
        self.em_channel_id: "str | None" = None   # canal externalMedia (UnicastRTP)
        self.bridge_id: "str | None" = None        # bridge mixing ARI

        # Cola ACOTADA de frames inbound (μ-law->b64) hacia el WS: un solo task
        # la dripea. maxsize ~200 = ~4 s de audio; si el WS se atasca, _on_rtp_in
        # descarta el frame más viejo (drop-oldest) en vez de crecer sin tope.
        self._inbound_q: "asyncio.Queue[str]" = asyncio.Queue(maxsize=200)
        self._inbound_frames = 0

        self._closed = False
        self._tasks: set = set()

    # ---- helpers internos -------------------------------------------------

    def _spawn(self, coro) -> "asyncio.Task":
        """Lanza una task de fondo y la registra para el teardown."""
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    @staticmethod
    def _json_id(raw: bytes) -> "str | None":
        """Extrae ``id`` de un body JSON de ARI. ``None`` si no parsea."""
        try:
            return json.loads(raw.decode("utf-8", "replace")).get("id")
        except (ValueError, AttributeError):
            return None

    # ---- arranque ---------------------------------------------------------

    async def start(self) -> None:
        """Abre el puente completo y arranca las tasks de fondo. RETORNA.

        Cualquier fallo en los pasos 1-4 dispara ``close()`` (limpia canales
        huérfanos) y re-lanza la excepción para que el caller cuelgue el SIP.
        """
        import bridge  # diferido: evita ciclo y permite mockear CONFIG en tests

        try:
            loop = asyncio.get_running_loop()

            # 1) Bind del socket UDP donde Asterisk enviará el RTP. Puerto
            #    EFÍMERO por sesión (':0') para soportar varias llamadas
            #    concurrentes; leemos el puerto real y lo usamos en external_host.
            self.udp_transport, self.udp_protocol = await loop.create_datagram_endpoint(
                lambda: _RtpProtocol(self._on_rtp_in, self.latch),
                local_addr=("0.0.0.0", 0),
            )
            self.rtp_port = self.udp_transport.get_extra_info("sockname")[1]
            log.info(
                "media channel=%s UDP bind 0.0.0.0:%s ssrc=0x%08x stream_sid=%s",
                self.channel_id, self.rtp_port, self.ssrc, self.stream_sid,
            )

            base = bridge.CONFIG.ari_base_http

            # 2) ARI externalMedia: crea un canal UnicastRTP μ-law. Params en
            #    QUERY STRING (NO body); el body JSON se reserva para 'variables'.
            em_qs = urllib.parse.urlencode({
                "app": bridge.CONFIG.ari_app,
                "external_host": f"{bridge.CONFIG.rtp_host}:{self.rtp_port}",
                "format": "ulaw",
                "transport": "udp",
                "encapsulation": "rtp",
            })
            status, raw = await bridge._ari_request(
                "POST", f"{base}/channels/externalMedia?{em_qs}"
            )
            if not (200 <= status < 300):
                raise RuntimeError(
                    f"externalMedia status={status} body={raw[:200]!r}"
                )
            self.em_channel_id = self._json_id(raw)
            if not self.em_channel_id:
                raise RuntimeError(f"externalMedia sin id en respuesta: {raw[:200]!r}")
            log.info(
                "media channel=%s externalMedia em_channel=%s",
                self.channel_id, self.em_channel_id,
            )

            # 3) Bridge mixing + addChannel (SIP channel + externalMedia channel).
            status, raw = await bridge._ari_request("POST", f"{base}/bridges?type=mixing")
            if not (200 <= status < 300):
                raise RuntimeError(f"bridge create status={status} body={raw[:200]!r}")
            self.bridge_id = self._json_id(raw)
            if not self.bridge_id:
                raise RuntimeError(f"bridge create sin id: {raw[:200]!r}")
            add_qs = urllib.parse.urlencode([
                ("channel", self.sip_channel_id),
                ("channel", self.em_channel_id),
            ])  # múltiples channel=, NO CSV
            status, raw = await bridge._ari_request(
                "POST", f"{base}/bridges/{urllib.parse.quote(self.bridge_id)}/addChannel?{add_qs}"
            )
            if not (200 <= status < 300):
                raise RuntimeError(f"addChannel status={status} body={raw[:200]!r}")
            log.info(
                "media channel=%s bridge=%s addChannel(sip=%s, em=%s) OK",
                self.channel_id, self.bridge_id, self.sip_channel_id, self.em_channel_id,
            )

            # 4) Cliente WebSocket al cliente + handshake start.
            import websockets
            self.ws = await websockets.connect(self.ws_url, max_size=None)
            await self.ws.send(encode_start(self.stream_sid, self.call_sid))
            log.info(
                "media channel=%s WS conectado a %s (callSid=%s)",
                self.channel_id, self.ws_url, self.call_sid,
            )

            # 5) Tasks de fondo. start() retorna aquí: no bloquea handle_event.
            self._spawn(self._ws_recv_loop())
            self._spawn(self._ws_send_loop())
            self._spawn(self._pacer_loop())
        except Exception:
            # Fallo en cualquiera de los pasos 1-4: no dejar canales/sockets
            # huérfanos. close() es idempotente; re-lanzamos para que el caller
            # cuelgue el SIP channel.
            await self.close()
            raise

    # ---- RTP entrante (Asterisk -> bridge -> WS) --------------------------

    def _on_rtp_in(self, ulaw: bytes) -> None:
        """Callback del socket UDP (corre EN el loop). Encola el frame inbound.

        ``media.timestamp`` = ms-desde-inicio (n_frames * 20), contador propio,
        NO el timestamp RTP crudo. Encolamos en una Queue que dripea un único
        task (sin crear una task por datagrama -> backpressure natural).
        """
        self._inbound_frames += 1
        b64 = b64_encode_ulaw(ulaw)
        frame = encode_media(b64, self._inbound_frames * FRAME_MS)
        try:
            self._inbound_q.put_nowait(frame)
        except asyncio.QueueFull:
            # WS atascado: el audio en tiempo real prefiere descartar el frame
            # más viejo a acumular latencia/memoria sin tope (drop-oldest).
            try:
                self._inbound_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._inbound_q.put_nowait(frame)
            except asyncio.QueueFull:
                log.debug("inbound queue llena, frame descartado")

    async def _ws_send_loop(self) -> None:
        """Dripea los frames inbound de la Queue al WS (backpressure natural)."""
        try:
            while not self._closed:
                frame = await self._inbound_q.get()
                if self.ws is None:
                    continue
                await self.ws.send(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  WS caído u otra falla -> teardown
            if not self._closed:
                log.debug("media channel=%s _ws_send_loop terminó", self.channel_id,
                          exc_info=True)
                self._spawn(self.close())

    # ---- frames entrantes del cliente (WS -> RtpSender) -------------------

    async def _ws_recv_loop(self) -> None:
        """Consume frames del WS y los aplica al RtpSender.

        media -> ``sender.feed(decode)`` ; clear -> ``sender.clear()`` ;
        mark -> passthrough/registro. Al cerrar el cliente el WS, el ``async
        for`` termina y programamos el teardown.
        """
        try:
            async for raw in self.ws:
                f = decode_client_frame(raw)
                if f is None:
                    continue
                ev = f.get("event")
                if ev == "media":
                    media = f.get("media") or {}
                    self.sender.feed(b64_decode_ulaw(media.get("payload", "")))
                elif ev == "clear":
                    # Barge-in: descartar lo pendiente y rearmar marker.
                    self.sender.clear()
                elif ev == "mark":
                    mark = f.get("mark") or {}
                    log.debug("media channel=%s mark recibido name=%s",
                              self.channel_id, mark.get("name"))
                else:
                    log.debug("media channel=%s frame entrante ignorado event=%s",
                              self.channel_id, ev)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  WS cerrado/roto por el cliente
            log.debug("media channel=%s _ws_recv_loop terminó por error",
                      self.channel_id, exc_info=True)
        finally:
            # WS cerrado por el cliente (o error): cerrar el puente entero.
            if not self._closed:
                self._spawn(self.close())

    # ---- pacer de salida (RtpSender -> UDP a 20 ms) -----------------------

    async def _pacer_loop(self) -> None:
        """Emite 160 B de RTP cada 20 ms EXACTOS hacia la addr aprendida.

        Reloj monotónico anti-deriva: ``next_t += 0.020`` y dormimos hasta el
        próximo tick (clamp a 0). Cada tick ``sender.next_packet()`` da un
        paquete SOLO si hay ≥160 B bufferizados; si openclaw manda rápido el
        outbuf se va vaciando a este ritmo (dripeo, no ráfaga).
        """
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        try:
            while not self._closed:
                pkt = self.sender.next_packet()
                target = self.latch.target()
                if pkt and target and self.udp_transport is not None:
                    try:
                        self.udp_transport.sendto(pkt, target)
                    except Exception:  # noqa: BLE001  sendto puntual falla -> seguir
                        log.debug("media channel=%s sendto falló", self.channel_id,
                                  exc_info=True)
                next_t += PACER_INTERVAL
                await asyncio.sleep(max(0.0, next_t - loop.time()))
        except asyncio.CancelledError:
            raise

    # ---- teardown ---------------------------------------------------------

    async def close(self) -> None:
        """Cierra el puente. IDEMPOTENTE y REENTRANTE (flag ``_closed``).

        Orden, cada paso aislado en try/except para que un fallo no bloquee a
        los demás ni deje fugas: (a) stop best-effort + ws.close;
        (b) cancelar tasks; (c) cerrar socket UDP; (d) ari_hangup del
        externalMedia; (e) DELETE del bridge mixing. La 2ª llamada retorna
        inmediato.
        """
        if self._closed:
            return
        self._closed = True

        import bridge  # diferido: igual que en start()

        # (a) stop al cliente (best-effort) + cerrar WS.
        if self.ws is not None:
            try:
                await self.ws.send(encode_stop())
            except Exception:  # noqa: BLE001
                pass
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass

        # (b) Cancelar todas las tasks de fondo y esperar a que terminen.
        tasks = [t for t in self._tasks if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:  # noqa: BLE001
                pass

        # (c) Cerrar el socket UDP (libera el puerto).
        if self.udp_transport is not None:
            try:
                self.udp_transport.close()
            except Exception:  # noqa: BLE001
                pass

        # (d) Colgar el canal externalMedia (UnicastRTP).
        if self.em_channel_id:
            try:
                await bridge.ari_hangup(self.em_channel_id)
            except Exception:  # noqa: BLE001
                log.debug("close: ari_hangup(%s) falló", self.em_channel_id,
                          exc_info=True)

        # (e) Borrar el bridge mixing.
        if self.bridge_id:
            try:
                await bridge._ari_request(
                    "DELETE",
                    f"{bridge.CONFIG.ari_base_http}/bridges/"
                    f"{urllib.parse.quote(self.bridge_id)}",
                )
            except Exception:  # noqa: BLE001
                log.debug("close: DELETE bridge %s falló", self.bridge_id,
                          exc_info=True)

        log.info("media channel=%s puente cerrado (stream_sid=%s)",
                 self.channel_id, self.stream_sid)
