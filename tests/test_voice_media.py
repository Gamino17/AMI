"""Tests unit de la capa de AUDIO del `ami_voice_bridge` (Paso 3).

Estos tests ejercitan la lógica PURA del puente RTP <-> WebSocket Media Streams
SIN red: ni UDP, ni Asterisk/ARI, ni WebSocket real. Cubren:

  * RTP puro: ``build_rtp`` / ``parse_rtp`` round-trip, tolerancia a CSRC,
    extension header (X) y padding (P), descarte de versión != 2 y de paquetes
    cortos (< 12 bytes) sin lanzar.
  * Pacer puro: ``RtpSender`` (marker bit del primer paquete de un talkspurt,
    ``seq+1`` / ``ts+160`` por frame de 20 ms, cadencia y no ráfagas, wraparound
    uint16/uint32, ``clear()`` que vacía el buffer y rearma el marker).
  * Frames Media Streams (JSON EXACTO del contrato openclaw): ``encode_start`` /
    ``encode_media`` / ``encode_mark`` / ``encode_stop`` salientes y
    ``decode_client_frame`` entrante (media/clear/mark con ``streamSid`` raíz),
    base64 μ-law round-trip (passthrough, sin transcodificar).
  * Latching: ``RtpLatch`` aprende la addr del PRIMER datagrama y la mantiene.
  * recv-loop con un WebSocket FAKE en memoria: ``MediaBridge._ws_recv_loop``
    consume frames media/clear/mark de un async-iterator y aplica
    ``feed()`` / ``clear()`` al ``RtpSender`` sin tocar red/ARI/UDP.

Ubicación y mecánica idéntica a tests/test_voice_bridge.py: vive en tests/
porque pytest.ini tiene ``norecursedirs = ... infra``; añadimos el directorio
del bridge a sys.path e importamos ``media`` (la capa de audio) y ``bridge``
(para CONFIG). Se salta entero con ``importorskip('websockets')`` si el dev no
tiene la única dependencia externa del bridge instalada localmente. Los tests
async se ejecutan con ``asyncio.run(...)`` (sin pytest-asyncio), mismo patrón
que test_voice_bridge.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys

import pytest

# `websockets` es la ÚNICA dependencia externa del bridge. Si no está instalada
# localmente, todo el módulo se salta (igual que test_voice_bridge.py).
pytest.importorskip("websockets")

# media.py / bridge.py viven en infra/ami_voice_bridge/ (fuera de testpaths). Los
# importamos añadiendo ese directorio a sys.path, igual que el Dockerfile los
# coloca juntos en /app. La raíz del repo también, porque bridge.py importa
# ami_voice_streams desde allí.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "infra", "ami_voice_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

media = pytest.importorskip(
    "media",
    reason="infra/ami_voice_bridge/media.py aún no existe (Paso 3 en curso)",
)
bridge = pytest.importorskip(
    "bridge",
    reason="infra/ami_voice_bridge/bridge.py aún no existe (Paso 2 en curso)",
)


# ──────────────────────────────────────────────────────────────────────
#  Helpers de construcción robustos a la firma exacta de media.py
# ──────────────────────────────────────────────────────────────────────

def _new_sender(ssrc: int = 0x11223344):
    """Construye un RtpSender tolerando que el ssrc venga por kwarg, posicional
    o que el constructor lo genere solo. La lógica PURA del pacer no depende de
    cómo se inyecte el ssrc, así que probamos varias firmas."""
    for build in (
        lambda: media.RtpSender(ssrc=ssrc),
        lambda: media.RtpSender(ssrc),
        lambda: media.RtpSender(),
    ):
        try:
            return build()
        except TypeError:
            continue
    # Última opción: que pete con la traza original de la firma real.
    return media.RtpSender(ssrc=ssrc)


# ──────────────────────────────────────────────────────────────────────
#  1. RTP PURO — build_rtp / parse_rtp
# ──────────────────────────────────────────────────────────────────────

def test_build_parse_roundtrip():
    # build_rtp -> parse_rtp debe preservar seq/ts/ssrc/marker/pt/version y el
    # payload μ-law byte-a-byte (passthrough, NO transcodifica).
    payload = os.urandom(160)
    pkt = media.build_rtp(
        payload=payload, seq=1000, ts=160000, ssrc=0x11223344, marker=True)
    parsed = media.parse_rtp(pkt)

    assert parsed is not None
    assert parsed["seq"] == 1000
    assert parsed["ts"] == 160000
    assert parsed["ssrc"] == 0x11223344
    assert parsed["marker"] == 1
    assert parsed["pt"] == 0  # PCMU
    assert parsed["version"] == 2
    assert parsed["payload"] == payload


def test_parse_header_len_12():
    # Cabecera base = 12 bytes; con 160 bytes de payload el paquete mide 172.
    pkt = media.build_rtp(b"x" * 160, seq=1, ts=0, ssrc=1, marker=False)
    assert len(pkt) == 172  # 12 (header) + 160 (payload μ-law)


def test_build_marker_false_pt_default():
    # marker=False -> bit a 0; PT por defecto = 0 (PCMU). Nunca emite CSRC/X/P.
    pkt = media.build_rtp(b"y" * 160, seq=7, ts=99, ssrc=42, marker=False)
    parsed = media.parse_rtp(pkt)
    assert parsed is not None
    assert parsed["marker"] == 0
    assert parsed["pt"] == 0
    # byte0 debe ser exactamente version=2, P=0, X=0, CC=0 -> 0x80.
    assert pkt[0] == 0x80


def test_parse_tolera_csrc():
    # CC=2 -> 8 bytes de CSRC tras la cabecera base; parse_rtp los salta y
    # devuelve el payload correcto.
    payload = os.urandom(160)
    byte0 = (2 << 6) | 0x02  # version=2, P=0, X=0, CC=2
    byte1 = 0x00             # marker=0, PT=0
    header = struct.pack(">BBHII", byte0, byte1, 1234, 5678, 0xAABBCCDD)
    csrc = struct.pack(">II", 0x01010101, 0x02020202)  # 2 CSRC = 8 bytes
    pkt = header + csrc + payload

    parsed = media.parse_rtp(pkt)
    assert parsed is not None
    assert parsed["seq"] == 1234
    assert parsed["ts"] == 5678
    assert parsed["ssrc"] == 0xAABBCCDD
    assert parsed["payload"] == payload


def test_parse_tolera_extension():
    # X=1 -> tras la cabecera base hay un extension header:
    #   uint16 profile-id (0xBEDE) + uint16 length-in-words (=1) + 4 bytes.
    # parse_rtp debe saltar 4 (cabecera ext) + 1*4 (cuerpo) = 8 bytes.
    payload = os.urandom(160)
    byte0 = (2 << 6) | (1 << 4)  # version=2, P=0, X=1, CC=0
    byte1 = 0x00
    header = struct.pack(">BBHII", byte0, byte1, 4321, 8765, 0x12345678)
    ext = struct.pack(">HH", 0xBEDE, 1) + b"\xde\xad\xbe\xef"  # 4 + 4 bytes
    pkt = header + ext + payload

    parsed = media.parse_rtp(pkt)
    assert parsed is not None
    assert parsed["seq"] == 4321
    assert parsed["ts"] == 8765
    assert parsed["payload"] == payload


def test_parse_tolera_csrc_y_extension():
    # Caso combinado: CC=1 (4 bytes CSRC) + X=1 (ext de 1 word). parse_rtp debe
    # saltar ambos en orden (primero CSRC, luego extension) y devolver payload.
    payload = os.urandom(160)
    byte0 = (2 << 6) | (1 << 4) | 0x01  # version=2, X=1, CC=1
    byte1 = 0x80 | 0x00                 # marker=1, PT=0
    header = struct.pack(">BBHII", byte0, byte1, 11, 22, 0x33)
    csrc = struct.pack(">I", 0xCAFEBABE)            # 1 CSRC = 4 bytes
    ext = struct.pack(">HH", 0xBEDE, 1) + b"\x00\x01\x02\x03"  # 8 bytes
    pkt = header + csrc + ext + payload

    parsed = media.parse_rtp(pkt)
    assert parsed is not None
    assert parsed["marker"] == 1
    assert parsed["payload"] == payload


def test_parse_padding():
    # P=1 -> el último byte del paquete indica cuántos bytes de padding recortar
    # del final del payload.
    real = os.urandom(157)
    pad_n = 3
    padding = b"\x00\x00" + bytes([pad_n])  # 3 bytes, el último = N=3
    byte0 = (2 << 6) | (1 << 5)  # version=2, P=1, X=0, CC=0
    byte1 = 0x00
    header = struct.pack(">BBHII", byte0, byte1, 1, 2, 3)
    pkt = header + real + padding

    parsed = media.parse_rtp(pkt)
    assert parsed is not None
    assert parsed["payload"] == real  # los 3 bytes de padding se recortan


def test_parse_descarta_no_v2():
    # version != 2 -> None (no lanza). Construimos un paquete válido y le
    # forzamos version=1 en el byte0.
    pkt = bytearray(media.build_rtp(b"z" * 160, seq=1, ts=0, ssrc=1))
    pkt[0] = (1 << 6) | (pkt[0] & 0x3F)  # version=1
    assert media.parse_rtp(bytes(pkt)) is None


def test_parse_descarta_corto():
    # len < 12 -> None (no lanza). Probamos varios tamaños sub-cabecera.
    for n in (0, 1, 11):
        assert media.parse_rtp(b"\x80" * n) is None


def test_parse_no_lanza_con_basura():
    # Datagramas arbitrarios (en red llega de todo) -> None o dict, NUNCA excepción.
    for blob in (b"", b"\x00", os.urandom(7), os.urandom(13), os.urandom(200)):
        try:
            media.parse_rtp(blob)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"parse_rtp lanzó con {blob!r}: {exc!r}")


# ──────────────────────────────────────────────────────────────────────
#  2. PACER PURO — RtpSender (cadencia 20 ms, marker, seq/ts, clear)
# ──────────────────────────────────────────────────────────────────────

def test_pacer_marker_primer_paquete():
    # 2 frames (320 bytes): el 1er paquete del talkspurt lleva marker=1, el 2º
    # marker=0; seq y ts avanzan +1 y +160 respectivamente.
    s = _new_sender()
    s.feed(b"\x00" * 320)  # 2 frames de 160 bytes

    p1 = s.next_packet()
    p2 = s.next_packet()
    assert p1 is not None and p2 is not None

    a = media.parse_rtp(p1)
    b = media.parse_rtp(p2)
    assert a["marker"] == 1, "primer paquete de un talkspurt -> marker bit a 1"
    assert b["marker"] == 0, "paquetes siguientes -> marker bit a 0"
    assert b["seq"] == (a["seq"] + 1) & 0xFFFF
    assert b["ts"] == (a["ts"] + 160) & 0xFFFFFFFF
    # El payload son los 160 bytes μ-law passthrough.
    assert a["payload"] == b"\x00" * 160
    assert b["payload"] == b"\x00" * 160


def test_pacer_no_emite_si_menos_de_160():
    # < 160 bytes bufferizados -> next_packet() devuelve None: cadencia, no
    # ráfagas ni medio-frame. El buffer pendiente se conserva.
    s = _new_sender()
    s.feed(b"x" * 80)
    assert s.next_packet() is None
    # Al completar el frame (otros 80 bytes), ya emite.
    s.feed(b"x" * 80)
    pkt = s.next_packet()
    assert pkt is not None
    assert media.parse_rtp(pkt)["payload"] == b"x" * 160


def test_pacer_dripea_no_rafaga():
    # 3 frames de golpe (480 bytes) -> next_packet() los entrega de UNO en UNO
    # (480 // 160 == 3 paquetes), no en ráfaga; el 4º llamada da None.
    s = _new_sender()
    s.feed(b"a" * 480)
    out = [s.next_packet() for _ in range(4)]
    assert all(out[i] is not None for i in range(3))
    assert out[3] is None
    seqs = [media.parse_rtp(p)["seq"] for p in out[:3]]
    assert seqs[1] == (seqs[0] + 1) & 0xFFFF
    assert seqs[2] == (seqs[1] + 1) & 0xFFFF


def test_pacer_clear_vacia_y_reinicia_marker():
    # feed(320) -> emite 1 frame; clear() descarta lo pendiente y rearma el
    # marker; tras clear el buffer está vacío (None) hasta volver a feed; el
    # siguiente paquete tras feed(160) vuelve a llevar marker=1 (nuevo talkspurt).
    s = _new_sender()
    s.feed(b"\x00" * 320)
    assert s.next_packet() is not None  # consume el 1er frame
    s.clear()
    assert s.next_packet() is None, "clear() debe vaciar el buffer pendiente"

    s.feed(b"\x00" * 160)
    pkt = s.next_packet()
    assert pkt is not None
    assert media.parse_rtp(pkt)["marker"] == 1, (
        "tras clear, el siguiente paquete reinicia el talkspurt (marker=1)")


def test_pacer_ts_seq_wraparound():
    # Arrancando en el límite uint16/uint32, el wrap a 0 debe ser correcto.
    s = _new_sender()
    s.seq = 0xFFFF
    s.ts = 0xFFFFFFFF
    s.feed(b"\x00" * 320)  # 2 frames

    p1 = media.parse_rtp(s.next_packet())
    p2 = media.parse_rtp(s.next_packet())
    assert p1["seq"] == 0xFFFF
    assert p1["ts"] == 0xFFFFFFFF
    # seq: 0xFFFF + 1 -> 0 (wrap uint16); ts: 0xFFFFFFFF + 160 -> 159 (wrap uint32).
    assert p2["seq"] == 0x0000
    assert p2["ts"] == (0xFFFFFFFF + 160) & 0xFFFFFFFF
    assert p2["ts"] == 159


def test_pacer_ssrc_constante_por_sesion():
    # El SSRC es fijo por sesión: todos los paquetes lo comparten.
    s = _new_sender(ssrc=0x0BADF00D)
    s.feed(b"\x00" * 320)
    p1 = media.parse_rtp(s.next_packet())
    p2 = media.parse_rtp(s.next_packet())
    assert p1["ssrc"] == p2["ssrc"]


# ──────────────────────────────────────────────────────────────────────
#  3. FRAMES MEDIA STREAMS — encode/decode (JSON EXACTO del contrato openclaw)
# ──────────────────────────────────────────────────────────────────────

def test_encode_start():
    # callSid = AMI_CALL_ID (call_xxx), invariante DURA del contrato.
    raw = media.encode_start("stream_abc", "call_xxx")
    assert json.loads(raw) == {
        "event": "start",
        "start": {"streamSid": "stream_abc", "callSid": "call_xxx"},
    }


def test_encode_media():
    # timestamp como STRING (ms-desde-inicio), track 'inbound', payload b64.
    b64 = base64.b64encode(b"\x00" * 160).decode("ascii")
    raw = media.encode_media(b64, 20)
    obj = json.loads(raw)
    assert obj == {
        "event": "media",
        "media": {"payload": b64, "timestamp": "20", "track": "inbound"},
    }
    # El timestamp DEBE ser string, no int (contrato Twilio/openclaw).
    assert isinstance(obj["media"]["timestamp"], str)


def test_encode_mark():
    raw = media.encode_mark("done")
    assert json.loads(raw) == {"event": "mark", "mark": {"name": "done"}}


def test_encode_stop():
    raw = media.encode_stop()
    assert json.loads(raw) == {"event": "stop"}


def test_decode_client_media():
    # Entrante del cliente: media con streamSid en la RAÍZ + media.payload.
    b64 = base64.b64encode(b"hola").decode("ascii")
    incoming = json.dumps({
        "event": "media",
        "streamSid": "stream_abc",
        "media": {"payload": b64},
    })
    f = media.decode_client_frame(incoming)
    assert f is not None
    assert f.get("event") == "media"
    # El caller hace el dispatch; el dict crudo debe permitir extraer el payload.
    assert f["media"]["payload"] == b64
    assert media.b64_decode_ulaw(f["media"]["payload"]) == b"hola"


def test_decode_client_clear():
    # clear con streamSid en la raíz, sin payload.
    incoming = json.dumps({"event": "clear", "streamSid": "stream_abc"})
    f = media.decode_client_frame(incoming)
    assert f is not None
    assert f.get("event") == "clear"
    assert f.get("streamSid") == "stream_abc"


def test_decode_client_mark():
    incoming = json.dumps({
        "event": "mark",
        "streamSid": "stream_abc",
        "mark": {"name": "ping"},
    })
    f = media.decode_client_frame(incoming)
    assert f is not None
    assert f.get("event") == "mark"
    assert f["mark"]["name"] == "ping"


def test_decode_client_no_json():
    # Frame no-JSON -> None, sin lanzar (en red llega basura).
    assert media.decode_client_frame("<<no es json>>") is None
    assert media.decode_client_frame(b"\xff\xfe\x00") is None


def test_b64_roundtrip():
    # μ-law passthrough: b64_decode(b64_encode(x)) == x para varios tamaños.
    for data in (b"", b"\x00" * 160, os.urandom(160), os.urandom(53)):
        enc = media.b64_encode_ulaw(data)
        assert isinstance(enc, str)
        assert media.b64_decode_ulaw(enc) == data


def test_b64_decode_basura_no_lanza():
    # Contrato defensivo DURO: el payload del cliente puede venir corrupto y
    # b64_decode_ulaw NUNCA debe lanzar (passthrough robusto en red). Sobre
    # entradas que base64 rechaza de plano (p.ej. padding inválido) devuelve b''.
    for junk in ("@@@no-base64@@@", "###", "!", "AB==CD", "=", "garbage!!!",
                 "\x00\x01\x02"):
        try:
            out = media.b64_decode_ulaw(junk)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"b64_decode_ulaw lanzó con {junk!r}: {exc!r}")
        assert isinstance(out, bytes)  # nunca None, nunca excepción
    # Una entrada claramente no-base64 (padding roto) cae al except -> b''.
    assert media.b64_decode_ulaw("=") == b""


# ──────────────────────────────────────────────────────────────────────
#  4. LATCHING — RtpLatch aprende la addr del primer datagrama
# ──────────────────────────────────────────────────────────────────────

def test_latch_aprende_primer_datagrama():
    latch = media.RtpLatch()
    assert latch.target() is None  # aún no aprendido

    latch.learn(("1.2.3.4", 5000))
    assert latch.target() == ("1.2.3.4", 5000)

    # Un datagrama posterior de OTRA addr NO cambia el target (RTP simétrico:
    # nos quedamos con la primera fuente aprendida).
    latch.learn(("9.9.9.9", 6000))
    assert latch.target() == ("1.2.3.4", 5000)


# ──────────────────────────────────────────────────────────────────────
#  5. recv-loop con WebSocket FAKE en memoria (sin red, sin ARI, sin UDP)
# ──────────────────────────────────────────────────────────────────────

class _FakeWS:
    """WebSocket falso en memoria, async-iterable.

    Cede los frames JSON predefinidos (``frames``) uno a uno en ``async for`` y
    luego termina (StopAsyncIteration), simulando que el cliente cierra el WS.
    ``send`` graba los frames salientes; ``close`` marca el cierre. NO toca red.
    """

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent: list = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        raise StopAsyncIteration

    async def send(self, data):
        self.sent.append(data)

    async def close(self, *a, **k):
        self.closed = True


def _make_bridge_no_io():
    """Construye un MediaBridge SIN llamar a start() (cero I/O): el __init__ solo
    crea stream_sid/ssrc/RtpSender/RtpLatch/flags. Neutralizamos cualquier task
    de cierre y los recursos de red (no se abrieron) para que el teardown del
    recv-loop sea inofensivo."""
    mb = media.MediaBridge(
        channel_id="ch1",
        sip_channel_id="ch1",
        ws_url="wss://agent.example/voice/stream/tok123",
        call_sid="call_xxx",
        params={"account_id": "acct_x"},
    )
    # No se abrió nada: ARI/UDP quedan en None para que close() sea no-op seguro.
    mb.em_channel_id = None
    mb.bridge_id = None
    mb.udp_transport = None
    return mb


def test_ws_recv_loop_aplica_media_y_clear():
    # El recv-loop consume media -> sender.feed; clear -> sender.clear; mark ->
    # passthrough/registro (no rompe). Todo en memoria, sin red ni ARI ni UDP.
    pcmu = b"\x11" * 160
    b64 = base64.b64encode(pcmu).decode("ascii")

    frames = [
        json.dumps({"event": "media", "streamSid": "s1",
                    "media": {"payload": b64}}),
        json.dumps({"event": "mark", "streamSid": "s1",
                    "mark": {"name": "ping"}}),
        json.dumps({"event": "clear", "streamSid": "s1"}),
    ]

    async def _run():
        mb = _make_bridge_no_io()
        mb.ws = _FakeWS(frames)

        # Tras el primer 'media' el sender tiene 160 bytes en outbuf; el 'clear'
        # final los descarta. Comprobamos el efecto observable del recv-loop.
        await mb._ws_recv_loop()

        # Dar un tick para que cualquier task de cierre programada por el loop
        # (al terminar el async-for) drene sin dejar warnings de task pendiente.
        await asyncio.sleep(0)
        return mb

    mb = asyncio.run(_run())

    # El 'clear' final dejó el buffer de salida vacío (next_packet -> None).
    assert mb.sender.next_packet() is None
    # outbuf existe y está vacío tras el clear.
    assert len(mb.sender.outbuf) == 0


def test_ws_recv_loop_solo_media_bufferiza():
    # Sin 'clear' al final: el media recibido queda bufferizado y next_packet()
    # entrega el frame μ-law íntegro (passthrough) cuando hay >= 160 bytes.
    pcmu = b"\x22" * 160
    b64 = base64.b64encode(pcmu).decode("ascii")
    frames = [
        json.dumps({"event": "media", "streamSid": "s1",
                    "media": {"payload": b64}}),
    ]

    async def _run():
        mb = _make_bridge_no_io()
        mb.ws = _FakeWS(frames)
        await mb._ws_recv_loop()
        await asyncio.sleep(0)
        return mb

    mb = asyncio.run(_run())

    pkt = mb.sender.next_packet()
    assert pkt is not None, "el media recibido debe quedar bufferizado en el sender"
    parsed = media.parse_rtp(pkt)
    assert parsed is not None
    assert parsed["payload"] == pcmu  # passthrough byte-a-byte
    assert parsed["pt"] == 0
    assert parsed["marker"] == 1  # primer paquete del talkspurt


# ──────────────────────────────────────────────────────────────────────
#  6. Teardown idempotente — MediaBridge.close() reentrante (sin red)
# ──────────────────────────────────────────────────────────────────────

def test_close_idempotente_sin_io():
    # close() sin recursos abiertos (todo None) no debe lanzar, y una 2ª llamada
    # tampoco (flag _closed -> reentrante). No toca red ni ARI.
    async def _run():
        mb = _make_bridge_no_io()
        await mb.close()
        assert mb._closed is True
        # 2ª llamada: no relanza ni re-ejecuta cierres.
        await mb.close()
        assert mb._closed is True

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────
#  7. MediaBridge.start() — orquestación ARI + bind UDP efímero (I/O mockeada)
# ──────────────────────────────────────────────────────────────────────

class _BlockingWS:
    """WS falso para start(): bloquea en el async-for hasta close() (para que el
    recv-loop no termine y dispare un cierre durante el assert). Graba sends."""

    def __init__(self):
        self.sent: list = []
        self.closed = False
        self._ev = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._ev.wait()
        raise StopAsyncIteration

    async def send(self, data):
        self.sent.append(data)

    async def close(self, *a, **k):
        self.closed = True
        self._ev.set()


def test_start_orquesta_externalmedia_bridge_y_ws(monkeypatch):
    """start() con UDP bind REAL (puerto efímero) + ARI/WS mockeados. Valida:
    orden externalMedia -> bridge mixing -> addChannel -> WS 'start'; que el
    external_host lleva el puerto EFÍMERO real + format=ulaw/transport=udp; y
    que el frame 'start' lleva el callSid == AMI_CALL_ID."""
    import websockets

    ari_calls: list = []

    async def _fake_ari(method, url, body=None, timeout=10.0):
        ari_calls.append((method, url))
        if "/channels/externalMedia" in url:
            return 200, b'{"id": "UnicastRTP/em-1"}'
        if url.endswith("/bridges?type=mixing"):
            return 200, b'{"id": "br-1"}'
        return 200, b'{}'

    monkeypatch.setattr(bridge, "_ari_request", _fake_ari, raising=True)

    fake_ws = _BlockingWS()

    async def _fake_connect(url, **kw):
        fake_ws.url = url
        return fake_ws

    monkeypatch.setattr(websockets, "connect", _fake_connect, raising=True)

    async def _run():
        mb = media.MediaBridge(
            channel_id="ch_sip", sip_channel_id="ch_sip",
            ws_url="wss://agent.example/voice/stream/realtime/tok",
            call_sid="call_real_42", params={},
        )
        await mb.start()  # bind UDP real efímero + ARI/WS mock; lanza tasks
        snap = {
            "ari": list(ari_calls),
            "ws_url": getattr(fake_ws, "url", None),
            "sent": list(fake_ws.sent),
            "rtp_port": mb.rtp_port,
            "em": mb.em_channel_id,
            "bridge_id": mb.bridge_id,
        }
        await mb.close()  # cancela tasks + cierra UDP/WS -> sin warnings
        return snap

    snap = asyncio.run(_run())

    # Puerto UDP EFÍMERO real (no el fijo 12000): bind a :0 lo eligió el SO.
    assert isinstance(snap["rtp_port"], int) and snap["rtp_port"] > 0

    ari_urls = [u for _m, u in snap["ari"]]
    em_idx = next(i for i, u in enumerate(ari_urls) if "/channels/externalMedia" in u)
    br_idx = next(i for i, u in enumerate(ari_urls) if u.endswith("/bridges?type=mixing"))
    add_idx = next(i for i, u in enumerate(ari_urls) if "/addChannel" in u)
    # Orden de orquestación: externalMedia -> bridge -> addChannel.
    assert em_idx < br_idx < add_idx

    em_url = ari_urls[em_idx]
    assert "format=ulaw" in em_url
    assert "transport=udp" in em_url
    assert "encapsulation=rtp" in em_url
    # external_host = ami_voice_bridge:<puerto efímero> (':' url-encodeado).
    assert "ami_voice_bridge" in em_url
    assert str(snap["rtp_port"]) in em_url

    # addChannel: una llamada por canal (SIP y externalMedia entran ambos).
    add_urls = [u for u in ari_urls if "/addChannel" in u]
    assert any("ch_sip" in u for u in add_urls), "el canal SIP debe entrar al bridge"
    assert any("UnicastRTP" in u for u in add_urls), "el canal externalMedia debe entrar"

    # Frame 'start' al cliente con el callSid == AMI_CALL_ID.
    assert snap["sent"], "debe enviar el frame start al WS"
    start_obj = json.loads(snap["sent"][0])
    assert start_obj["event"] == "start"
    assert start_obj["start"]["callSid"] == "call_real_42"
    assert start_obj["start"]["streamSid"]  # streamSid presente
    assert snap["ws_url"] == "wss://agent.example/voice/stream/realtime/tok"

    asyncio.run(_run())
