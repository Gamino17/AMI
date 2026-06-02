"""Tests unit de la capa de AUDIO del `ami_voice_bridge` (Paso 3).

Estos tests ejercitan la lógica PURA del puente RTP <-> WebSocket Media Streams
SIN red: ni UDP, ni Asterisk/ARI, ni WebSocket real. Cubren:

  * RTP puro: ``build_rtp`` / ``parse_rtp`` round-trip, tolerancia a CSRC,
    extension header (X) y padding (P), descarte de versión != 2 y de paquetes
    cortos (< 12 bytes) sin lanzar.
  * Pacer puro (API histórica ``next_packet``): ``RtpSender`` (marker bit del
    primer paquete de un talkspurt, ``seq+1`` / ``ts+160`` por frame de 20 ms,
    cadencia y no ráfagas, wraparound uint16/uint32, ``clear()`` que vacía el
    buffer y rearma el marker). Esta API NO consulta la FSM del jitter buffer.
  * Jitter buffer (API nueva ``tick``): FSM ``buffering`` -> ``draining``
    (prebuffer configurable), comfort noise (silencio μ-law 0xFF) en underrun,
    seq/ts CONTINUOS a 20 ms (avanzan por tick, no solo cuando hay audio),
    marker en el resume tras silencio, wraparound, y los contadores de
    instrumentación (deterministas, reloj implícito = nº de llamadas a tick()).
  * Frames Media Streams (JSON EXACTO del contrato openclaw): ``encode_start`` /
    ``encode_media`` / ``encode_mark`` / ``encode_stop`` salientes y
    ``decode_client_frame`` entrante (media/clear/mark con ``streamSid`` raíz),
    base64 μ-law round-trip (passthrough, sin transcodificar).
  * Latching: ``RtpLatch`` aprende la addr del PRIMER datagrama y la mantiene.
  * recv-loop con un WebSocket FAKE en memoria: ``MediaBridge._ws_recv_loop``
    consume frames media/clear/mark de un async-iterator y aplica
    ``feed()`` / ``clear()`` al ``RtpSender`` sin tocar red/ARI/UDP.
  * Instrumentación por sesión: contador de drops de la cola inbound, log de
    stats en ``close()`` (sin secreto/token/audio), pacer continuo vía tick(),
    y wiring del prebuffer desde ``AMI_VOICE_JITTER_MS``.

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
import logging
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


def _sender_prebuffer(prebuffer_frames: int, ssrc: int = 0x11223344):
    """RtpSender con un prebuffer EXPLÍCITO (frames) para tests deterministas
    del jitter buffer sin reloj real ni env. El prebuffer se inyecta por el
    ctor: 1 frame = arranque inmediato al primer feed completo; N frames =
    acumula N antes de pasar a 'draining'."""
    return media.RtpSender(ssrc=ssrc, prebuffer_frames=prebuffer_frames)


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
#  2. PACER PURO — RtpSender.next_packet (cadencia 20 ms, marker, seq/ts, clear)
#
#  next_packet es la API histórica de BAJO NIVEL: "dame un frame de audio o
#  None". NO participa de la FSM del jitter buffer; estos 7 tests garantizan
#  que su semántica NO cambió al añadir tick().
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
    # next_packet NO consulta la FSM, así que sigue dando None en buffer vacío.
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


def test_next_packet_intacto_no_usa_fsm():
    # Regresión EXPLÍCITA: next_packet() ignora self.state. Aunque forcemos el
    # sender a 'draining', con el buffer vacío next_packet sigue devolviendo
    # None (no inventa silencio: ESO lo hace tick()). Garantiza que los tests
    # históricos de next_packet no cambian de semántica al añadir el jitter buffer.
    s = _new_sender()
    s.state = "draining"          # forzamos el estado de drenaje
    assert s.next_packet() is None, "next_packet en buffer vacío -> None (no FSM)"
    # Con un frame completo emite audio real (no silencio), igual que siempre.
    s.feed(b"\x33" * 160)
    pkt = s.next_packet()
    assert pkt is not None
    assert media.parse_rtp(pkt)["payload"] == b"\x33" * 160
    assert media.parse_rtp(pkt)["payload"] != media.SILENCE_FRAME


# ──────────────────────────────────────────────────────────────────────
#  2b. JITTER BUFFER — RtpSender.tick (FSM buffering->draining, comfort noise)
#
#  tick() es la API que el pacer usa EN PRODUCCIÓN. Reloj implícito: 1 tick =
#  20 ms = una llamada. Tests deterministas con prebuffer EXPLÍCITO por ctor.
# ──────────────────────────────────────────────────────────────────────

def test_tick_buffering_no_emite_hasta_prebuffer():
    # prebuffer = 3 frames (~60 ms). Mientras outbuf < 3*160 el sender está en
    # 'buffering' y tick() devuelve None (el stream RTP aún no ha arrancado:
    # esto es prebuffer inicial, NO underrun). Al alcanzar el prebuffer pasa a
    # 'draining' y emite el primer frame de audio.
    s = _sender_prebuffer(3)
    assert s.state == "buffering"

    s.feed(b"\x01" * 160)
    assert s.tick() is None, "1<3 frames: aún buffering"
    assert s.state == "buffering"

    s.feed(b"\x01" * 160)
    assert s.tick() is None, "2<3 frames: aún buffering"
    assert s.state == "buffering"

    s.feed(b"\x01" * 160)  # ahora hay 3 frames acumulados
    pkt = s.tick()
    assert pkt is not None, "alcanzado el prebuffer: pasa a draining y emite"
    assert s.state == "draining"
    # Los ticks None del prebuffer NO cuentan como underrun.
    assert s.n_underruns == 0
    # El primer paquete emitido es AUDIO real, no silencio.
    assert media.parse_rtp(pkt)["payload"] == b"\x01" * 160


def test_tick_primer_paquete_marker_y_ts_continuo():
    # Con el prebuffer alcanzado, el primer tick de audio lleva marker=1
    # (talkspurt); los siguientes con buffer suficiente marker=0; seq +1 y ts
    # +160 por tick. parse_rtp como oráculo.
    s = _sender_prebuffer(1)  # arranque inmediato al primer frame completo
    s.feed(b"\x07" * (160 * 3))  # 3 frames listos de golpe

    pkts = [s.tick() for _ in range(3)]
    assert all(p is not None for p in pkts)
    a, b, c = (media.parse_rtp(p) for p in pkts)

    assert a["marker"] == 1, "primer paquete del talkspurt -> marker=1"
    assert b["marker"] == 0
    assert c["marker"] == 0
    assert b["seq"] == (a["seq"] + 1) & 0xFFFF
    assert c["seq"] == (b["seq"] + 1) & 0xFFFF
    assert b["ts"] == (a["ts"] + 160) & 0xFFFFFFFF
    assert c["ts"] == (b["ts"] + 160) & 0xFFFFFFFF
    # 3 frames de audio real emitidos, cero silencio.
    assert s.n_frames_emitted == 3
    assert s.n_comfort_frames == 0


def test_tick_underrun_inyecta_silencio_continuo():
    # prebuffer = 1. Tras emitir 1 frame de audio, sin feed el siguiente tick
    # NO devuelve None (como haría next_packet) sino un paquete de COMFORT NOISE
    # (silencio μ-law 0xFF*160, marker=0) para mantener el flujo RTP continuo.
    s = _sender_prebuffer(1)
    s.feed(b"\x09" * 160)

    audio = s.tick()
    assert audio is not None
    assert media.parse_rtp(audio)["marker"] == 1  # primer audio del talkspurt

    silence = s.tick()  # underrun: no hay más audio bufferizado
    assert silence is not None, "en draining tick() NUNCA es None: inyecta silencio"
    parsed = media.parse_rtp(silence)
    assert parsed["payload"] == media.SILENCE_FRAME, "comfort noise = 0xFF*160"
    assert parsed["payload"] == bytes([media.ULAW_SILENCE_BYTE]) * 160
    assert parsed["marker"] == 0, "el silencio no abre talkspurt"
    assert s.n_underruns == 1
    assert s.n_comfort_frames == 1
    assert s.n_frames_emitted == 1  # solo 1 frame de AUDIO real


def test_tick_ts_seq_continuos_en_underrun():
    # CLAVE del fix: 1 frame audio + 3 ticks de underrun + 1 frame audio
    # (re-feed). Los 5 paquetes tienen seq consecutivos (+1 cada uno) y ts +160
    # monótono SIN saltos ni rezago -> el RTP es continuo y el ts avanza por
    # tiempo real (no solo cuando hay audio), que es lo que Asterisk espera.
    s = _sender_prebuffer(1)
    s.feed(b"\x10" * 160)

    pkts = [s.tick()]              # audio #1
    pkts += [s.tick() for _ in range(3)]  # 3 underruns -> silencio
    s.feed(b"\x11" * 160)
    pkts += [s.tick()]            # audio #2 (resume)

    assert all(p is not None for p in pkts), "draining: ningún tick es None"
    parsed = [media.parse_rtp(p) for p in pkts]

    # seq consecutivos +1 sin huecos.
    for i in range(1, len(parsed)):
        assert parsed[i]["seq"] == (parsed[i - 1]["seq"] + 1) & 0xFFFF, (
            f"seq no consecutivo en {i}")
        # ts monótono +160 por tick (wall-clock), incluido durante el silencio.
        assert parsed[i]["ts"] == (parsed[i - 1]["ts"] + 160) & 0xFFFFFFFF, (
            f"ts no continuo en {i}")

    # Los 3 huecos se rellenaron con silencio; 2 frames de audio real.
    assert s.n_underruns == 3
    assert s.n_comfort_frames == 3
    assert s.n_frames_emitted == 2


def test_tick_marker_resume_tras_silencio():
    # audio -> silencio(s) -> nuevo feed: el primer paquete de audio tras el
    # silencio lleva marker=1 (resume talkspurt, RFC 3550) y _in_silence vuelve
    # a False. Es la señal que evita que Asterisk malinterprete el salto.
    s = _sender_prebuffer(1)
    s.feed(b"\x20" * 160)

    a = media.parse_rtp(s.tick())          # audio #1, marker=1
    assert a["marker"] == 1

    sil1 = media.parse_rtp(s.tick())       # underrun
    sil2 = media.parse_rtp(s.tick())       # underrun
    assert sil1["marker"] == 0 and sil2["marker"] == 0
    assert s._in_silence is True

    s.feed(b"\x21" * 160)
    resume = media.parse_rtp(s.tick())     # audio tras silencio
    assert resume["marker"] == 1, "resume de talkspurt tras silencio -> marker=1"
    assert resume["payload"] == b"\x21" * 160
    assert s._in_silence is False
    # Tras el resume, si sigue habiendo audio el marker vuelve a 0.
    s.feed(b"\x22" * 160)
    cont = media.parse_rtp(s.tick())
    assert cont["marker"] == 0


def test_tick_clear_vuelve_a_buffering():
    # En draining, clear() (barge-in) resetea la FSM a 'buffering': el siguiente
    # tick es None hasta re-acumular el prebuffer, y el primer audio tras
    # re-prebuffer vuelve a llevar marker=1. Verifica que el barge-in
    # re-prebufferiza el nuevo talkspurt del agente.
    s = _sender_prebuffer(2)
    s.feed(b"\x30" * (160 * 2))
    assert s.tick() is not None
    assert s.state == "draining"

    s.clear()
    assert s.state == "buffering", "clear() vuelve a buffering (re-prebuffer)"
    assert s.tick() is None, "tras clear, buffering hasta re-acumular el prebuffer"

    s.feed(b"\x31" * 160)
    assert s.tick() is None, "1<2 frames: aún buffering"
    s.feed(b"\x31" * 160)
    pkt = s.tick()
    assert pkt is not None
    assert s.state == "draining"
    assert media.parse_rtp(pkt)["marker"] == 1, (
        "primer audio tras re-prebuffer reinicia talkspurt (marker=1)")


def test_tick_seq_ts_wraparound():
    # Como test_pacer_ts_seq_wraparound pero vía tick(): seq=0xFFFF y
    # ts=0xFFFFFFFF en el límite -> wrap correcto a 0 / 159 al emitir, INCLUIDO
    # cuando el segundo paquete es un frame de silencio (underrun).
    s = _sender_prebuffer(1)
    s.seq = 0xFFFF
    s.ts = 0xFFFFFFFF
    s.feed(b"\x40" * 160)

    p1 = media.parse_rtp(s.tick())   # audio en el límite
    p2 = media.parse_rtp(s.tick())   # underrun -> silencio, debe wrap igual
    assert p1["seq"] == 0xFFFF
    assert p1["ts"] == 0xFFFFFFFF
    assert p2["seq"] == 0x0000, "wrap uint16 también en el frame de silencio"
    assert p2["ts"] == 159, "wrap uint32 también en el frame de silencio"
    assert p2["payload"] == media.SILENCE_FRAME


def test_tick_contadores_instrumentacion():
    # Secuencia conocida de feeds/ticks -> contadores EXACTOS y deterministas:
    #   - prebuffer = 2 frames.
    #   - feed 3 frames de golpe; max_outbuf_depth debe registrar 3*160.
    #   - tick #1: alcanza prebuffer -> draining + emite audio (1 transición).
    #   - ticks #2,#3: audio (quedan 2 frames tras el #1).
    #   - tick #4: underrun -> silencio.
    #   - clear(): vuelve a buffering (2ª transición).
    s = _sender_prebuffer(2)
    s.feed(b"\x50" * (160 * 3))
    assert s.max_outbuf_depth == 160 * 3

    assert s.tick() is not None   # #1 audio (prebuffer alcanzado)
    assert s.tick() is not None   # #2 audio
    assert s.tick() is not None   # #3 audio
    assert s.tick() is not None   # #4 underrun -> silencio

    assert s.n_frames_emitted == 3
    assert s.n_underruns == 1
    assert s.n_comfort_frames == 1
    assert s.n_state_transitions == 1  # buffering -> draining

    s.clear()
    assert s.n_state_transitions == 2  # draining -> buffering (barge-in)
    # max_outbuf_depth no decrece (es un máximo histórico para diagnóstico).
    assert s.max_outbuf_depth == 160 * 3


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


def test_close_loguea_stats_sin_secreto(caplog):
    # close() emite UNA línea log.info con los contadores por sesión (rtp_in /
    # ws_out / underruns / comfort / inbound_drops / max_outbuf / latch / state)
    # y NUNCA el ws_url/token ni payloads. Da DATOS sin exponer secretos.
    async def _run():
        mb = _make_bridge_no_io()
        # Simulamos un poco de actividad para que los contadores sean != 0.
        mb._inbound_frames = 5
        mb._inbound_drops = 2
        mb.sender.feed(b"\x55" * 160)
        mb.sender.prebuffer_bytes = 160  # arranque inmediato
        mb.sender.tick()                 # 1 frame de audio
        mb.sender.tick()                 # 1 underrun -> comfort noise
        await mb.close()
        return mb

    with caplog.at_level(logging.INFO, logger="ami_voice_bridge.media"):
        mb = asyncio.run(_run())

    stats_lines = [r.getMessage() for r in caplog.records if "stats:" in r.getMessage()]
    assert stats_lines, "close() debe emitir una línea de stats por sesión"
    line = stats_lines[0]
    # Substrings clave presentes (instrumentación medible).
    for token in ("rtp_in=", "ws_out=", "underruns=", "comfort=",
                  "inbound_drops=", "max_outbuf=", "latch=", "state="):
        assert token in line, f"falta {token!r} en la línea de stats"
    # Contadores reflejan la actividad simulada.
    assert "rtp_in=5" in line
    assert "inbound_drops=2" in line
    assert "ws_out=1" in line
    assert "underruns=1" in line
    # SEGURIDAD: ni el token del ws_url ni el b64 del audio aparecen en NINGÚN log.
    all_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "tok123" not in all_logs, "el token del ws_url NO debe loguearse"
    assert base64.b64encode(b"\x55" * 160).decode("ascii") not in all_logs, (
        "el payload de audio NO debe loguearse")


# ──────────────────────────────────────────────────────────────────────
#  7. Instrumentación path ENTRANTE — drop-oldest de la cola inbound
# ──────────────────────────────────────────────────────────────────────

def test_inbound_drops_contador():
    # _on_rtp_in encola hacia el WS; si la cola (maxsize=200) está llena dropea
    # el frame MÁS VIEJO (drop-oldest) e incrementa _inbound_drops. Llenamos la
    # cola y verificamos que cada datagrama extra cuenta como un drop, sin que
    # crezca por encima del maxsize (sin latencia/memoria sin tope).
    async def _run():
        mb = _make_bridge_no_io()
        maxsize = mb._inbound_q.maxsize
        # Llenar la cola exacta hasta el tope con datagramas válidos.
        for _ in range(maxsize):
            mb._on_rtp_in(b"\x66" * 160)
        assert mb._inbound_q.full()
        assert mb._inbound_drops == 0, "aún no se ha dropeado nada"

        # Cada datagrama extra fuerza un drop-oldest.
        for _ in range(5):
            mb._on_rtp_in(b"\x66" * 160)

        assert mb._inbound_drops == 5
        assert mb._inbound_q.qsize() == maxsize, (
            "la cola NO crece por encima del maxsize (drop-oldest)")
        return mb

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────
#  8. Pacer continuo — _pacer_loop emite un sendto por tick (incl. silencio)
# ──────────────────────────────────────────────────────────────────────

class _FakeUDP:
    """Transport UDP falso: graba cada sendto(pkt, addr) sin tocar red."""

    def __init__(self):
        self.sent: list = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def test_pacer_loop_emite_continuo():
    # Con el latch ya aprendido y un transport UDP falso, el _pacer_loop debe
    # producir un sendto por tick UNA VEZ en 'draining' — incluido el frame de
    # silencio en underrun (continuidad RTP). Forzamos PACER_INTERVAL a ~0 para
    # que el loop avance rápido y lo cancelamos tras recoger varios paquetes.
    async def _run(monkeypatch_interval):
        mb = _make_bridge_no_io()
        # Prebuffer mínimo: arranca al primer frame; un solo frame de audio y el
        # resto serán comfort noise (underrun) -> el pacer DEBE seguir enviando.
        mb.sender.prebuffer_bytes = 160
        mb.sender.feed(b"\x77" * 160)
        mb.latch.learn(("1.2.3.4", 5000))  # target conocido
        udp = _FakeUDP()
        mb.udp_transport = udp

        # Acelerar el reloj del pacer: sin esto el test tardaría 20 ms/tick.
        media.PACER_INTERVAL = 0.0

        task = asyncio.create_task(mb._pacer_loop())
        # Ceder control varias veces para que el loop ejecute >=5 ticks.
        for _ in range(50):
            await asyncio.sleep(0)
            if len(udp.sent) >= 5:
                break
        mb._closed = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return udp, mb

    saved_interval = media.PACER_INTERVAL
    try:
        udp, mb = asyncio.run(_run(None))
    finally:
        media.PACER_INTERVAL = saved_interval

    assert len(udp.sent) >= 5, "el pacer debe emitir un paquete por tick en draining"
    # El primer paquete es audio real; los siguientes son silencio (underrun)
    # pero TODOS se envían (RTP continuo, no huecos).
    first = media.parse_rtp(udp.sent[0][0])
    assert first["payload"] == b"\x77" * 160
    later = media.parse_rtp(udp.sent[1][0])
    assert later["payload"] == media.SILENCE_FRAME
    # seq consecutivos en lo enviado (sin huecos de secuencia).
    seqs = [media.parse_rtp(pkt)["seq"] for pkt, _addr in udp.sent]
    for i in range(1, len(seqs)):
        assert seqs[i] == (seqs[i - 1] + 1) & 0xFFFF
    # Hubo al menos un underrun absorbido por comfort noise.
    assert mb.sender.n_underruns >= 1


def test_pacer_loop_no_emite_sin_latch():
    # Sin addr aprendida (latch.target() is None) el pacer NO debe enviar nada,
    # aunque el sender tenga audio listo: el RTP solo sale cuando sabemos a
    # dónde devolverlo. Verifica la guarda del sendto.
    async def _run():
        mb = _make_bridge_no_io()
        mb.sender.prebuffer_bytes = 160
        mb.sender.feed(b"\x88" * 320)
        udp = _FakeUDP()
        mb.udp_transport = udp
        # NO llamamos latch.learn -> target() is None.

        media.PACER_INTERVAL = 0.0
        task = asyncio.create_task(mb._pacer_loop())
        for _ in range(30):
            await asyncio.sleep(0)
        mb._closed = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return udp

    saved_interval = media.PACER_INTERVAL
    try:
        udp = asyncio.run(_run())
    finally:
        media.PACER_INTERVAL = saved_interval

    assert udp.sent == [], "sin latch aprendido el pacer no envía RTP"


# ──────────────────────────────────────────────────────────────────────
#  9. Wiring de la config del jitter — AMI_VOICE_JITTER_MS -> prebuffer_frames
# ──────────────────────────────────────────────────────────────────────

def test_prebuffer_configurable_env(monkeypatch):
    # MediaBridge deriva el prebuffer del sender de AMI_VOICE_JITTER_MS:
    # prebuffer_frames = round(jitter_ms / FRAME_MS) (FRAME_MS=20). 40 -> 2,
    # 60 -> 3, 100 -> 5. prebuffer_bytes = frames * 160.
    def _bridge_with_jitter(ms):
        monkeypatch.setenv("AMI_VOICE_JITTER_MS", str(ms))
        return media.MediaBridge(
            channel_id="ch", sip_channel_id="ch",
            ws_url="wss://agent.example/voice/stream/tok",
            call_sid="call_x", params={},
        )

    mb40 = _bridge_with_jitter(40)
    assert mb40.sender.prebuffer_bytes == 2 * media.FRAME_BYTES

    mb60 = _bridge_with_jitter(60)
    assert mb60.sender.prebuffer_bytes == 3 * media.FRAME_BYTES

    mb100 = _bridge_with_jitter(100)
    assert mb100.sender.prebuffer_bytes == 5 * media.FRAME_BYTES


def test_prebuffer_default_y_minimo(monkeypatch):
    # Sin la env -> default DEFAULT_JITTER_MS (60 ms -> 3 frames). Un valor
    # absurdo/0 -> mínimo de 1 frame (nunca prebuffer 0, que rompería el
    # arranque del stream). Un valor no numérico -> cae al default sin lanzar.
    monkeypatch.delenv("AMI_VOICE_JITTER_MS", raising=False)
    mb_def = media.MediaBridge(
        channel_id="ch", sip_channel_id="ch",
        ws_url="wss://agent.example/voice/stream/tok", call_sid="call_x", params={})
    expected = max(1, round(media.DEFAULT_JITTER_MS / media.FRAME_MS))
    assert mb_def.sender.prebuffer_bytes == expected * media.FRAME_BYTES

    monkeypatch.setenv("AMI_VOICE_JITTER_MS", "0")
    mb0 = media.MediaBridge(
        channel_id="ch", sip_channel_id="ch",
        ws_url="wss://agent.example/voice/stream/tok", call_sid="call_x", params={})
    assert mb0.sender.prebuffer_bytes == 1 * media.FRAME_BYTES, (
        "jitter 0 -> mínimo 1 frame de prebuffer")

    monkeypatch.setenv("AMI_VOICE_JITTER_MS", "no-es-un-numero")
    mb_bad = media.MediaBridge(
        channel_id="ch", sip_channel_id="ch",
        ws_url="wss://agent.example/voice/stream/tok", call_sid="call_x", params={})
    assert mb_bad.sender.prebuffer_bytes == expected * media.FRAME_BYTES, (
        "env no numérica -> default sin lanzar")


# ──────────────────────────────────────────────────────────────────────
#  10. MediaBridge.start() — orquestación ARI + bind UDP efímero (I/O mockeada)
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


# ──────────────────────────────────────────────────────────────────────
#  8. Tope del outbuf (drop-oldest) — el jitter buffer no crece sin límite
# ──────────────────────────────────────────────────────────────────────

def test_outbuf_bounded_drop_oldest():
    # Si el cliente entrega audio MÁS RÁPIDO que tiempo real, el outbuf se acota
    # descartando el audio MÁS VIEJO (drop-oldest), contando los bytes dropeados.
    s = media.RtpSender(ssrc=0x1, prebuffer_frames=1, max_outbuf_frames=3)
    cap = s.max_outbuf_bytes
    assert cap == 3 * media.FRAME_BYTES
    # Alimentar 10 frames de golpe (muy por encima del techo de 3).
    s.feed(bytes([0x10]) * (10 * media.FRAME_BYTES))
    assert len(s.outbuf) <= cap, "el outbuf debe quedar acotado al techo"
    assert s.n_outbuf_drops > 0, "debe contar los bytes viejos descartados"
    # Drop-OLDEST: lo que queda es el audio más NUEVO (último frame == 0x10*160).
    assert bytes(s.outbuf[-media.FRAME_BYTES:]) == bytes([0x10]) * media.FRAME_BYTES
    # El techo nunca baja del prebuffer.
    s2 = media.RtpSender(ssrc=0x2, prebuffer_frames=5, max_outbuf_frames=1)
    assert s2.max_outbuf_bytes >= s2.prebuffer_bytes
