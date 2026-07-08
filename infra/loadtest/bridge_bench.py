#!/usr/bin/env python3
"""Benchmark sintético del ami_voice_bridge — mide la RODILLA de concurrencia
del proceso Python del bridge SIN Asterisk, SIN Docker, SIN red, SIN tocar prod.

Idea: el limitador real del bridge es su proceso asyncio (GIL, 1 core) moviendo
audio μ-law a 20 ms por llamada. Este script arranca N "llamadas" concurrentes
que ejecutan EXACTAMENTE el trabajo por-frame del bridge (código REAL importado
de media.py) en ambas direcciones, a cadencia de 20 ms, y mide cuándo el loop
deja de llegar a tiempo (lateness) y a cuánta CPU de 1 core.

Uso (en la caja, idle):
    cd /opt/ami && git pull && python3 infra/loadtest/bridge_bench.py

Faithfulness / caveats:
  - Usa las funciones REALES de media.py (parse_rtp/build_rtp, RtpSender.tick,
    b64_encode/decode_ulaw, encode_media, decode_client_frame). No reimplementa.
  - Es 1 proceso asyncio = 1 core, IGUAL que el bridge en producción (GIL).
  - NO incluye los syscalls de socket UDP/WS ni el TLS del WS reales (esos
    también cuestan por-frame). Por eso el número es un LÍMITE SUPERIOR: la
    rodilla real en producción es algo MENOR. Triangula con el estimado ~50-100.
"""
from __future__ import annotations
import asyncio, os, resource, sys, time, types

# Stub de websockets para poder importar media.py sin la dep (solo usamos su
# lógica pura; el I/O de red no se toca).
sys.modules.setdefault("websockets", types.ModuleType("websockets"))
for _p in ("/opt/ami/infra/ami_voice_bridge",
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ami_voice_bridge")):
    if os.path.exists(os.path.join(_p, "media.py")):
        sys.path.insert(0, _p); break
import media  # noqa: E402

FRAME_MS = media.FRAME_MS                       # 20
TICK = media.PACER_INTERVAL                      # 0.020
FRAME = media.FRAME_BYTES                        # 160
# Un frame μ-law "de verdad" (bytes no triviales) y sus formas pre-serializadas.
AUDIO = bytes((i * 7 + 13) & 0xFF for i in range(FRAME))
IN_PKT = media.build_rtp(AUDIO, seq=1000, ts=0, ssrc=0x1234, marker=True)   # RTP entrante de Asterisk
CLIENT_JSON = '{"event":"media","streamSid":"MZbench","media":{"payload":"%s"}}' % media.b64_encode_ulaw(AUDIO)

LEVELS = [int(x) for x in os.environ.get("BENCH_LEVELS", "25 50 100 150 200 300 500 750 1000").split()]
SECONDS = float(os.environ.get("BENCH_SECONDS", "5"))
NTICKS = int(SECONDS / TICK)
# Umbral de rodilla: p95 de lateness de scheduling. El bridge tiene un jitter
# buffer de salida de ~60ms (DEFAULT_JITTER_MS), así que una lateness p95 por
# debajo de ~15ms se absorbe sin underruns audibles. Por encima, el scheduling
# empieza a comerse el margen del buffer -> degradación.
LATE_MS_THRESHOLD = float(os.environ.get("BENCH_LATE_MS", "15"))


def _one_frame_work(sender, ts_ms):
    """Trabajo por-frame de UNA llamada, ambas direcciones (código real del bridge)."""
    # Inbound: Asterisk RTP -> parse -> b64 -> media JSON hacia el cliente
    p = media.parse_rtp(IN_PKT)
    if p:
        media.encode_media(media.b64_encode_ulaw(p["payload"]), ts_ms)
    # Outbound: WS media del cliente -> decode -> b64decode -> pacer (jitter+RTP)
    cf = media.decode_client_frame(CLIENT_JSON)
    if cf:
        sender.feed(media.b64_decode_ulaw(cf["media"]["payload"]))
    sender.tick()


async def _call(sender, start, lateness):
    ts = 0
    for i in range(1, NTICKS + 1):
        target = start + i * TICK
        dt = target - time.perf_counter()
        if dt > 0:
            await asyncio.sleep(dt)
        lateness.append((time.perf_counter() - target) * 1000.0)  # ms
        _one_frame_work(sender, ts)
        ts += FRAME_MS


def _pctl(xs, q):
    if not xs: return 0.0
    s = sorted(xs); k = min(len(s) - 1, int(q * len(s)))
    return s[k]


async def run_level(n):
    senders = [media.RtpSender(ssrc=0x1000 + i) for i in range(n)]
    lateness = []
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    w0 = time.perf_counter()
    start = w0 + 0.05
    await asyncio.gather(*[_call(s, start, lateness) for s in senders])
    wall = time.perf_counter() - w0
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
    return {
        "n": n,
        "cpu_pct": 100.0 * cpu / wall,
        "p50": _pctl(lateness, 0.50),
        "p95": _pctl(lateness, 0.95),
        "pmax": max(lateness) if lateness else 0.0,
        "late_pct": 100.0 * sum(1 for x in lateness if x > LATE_MS_THRESHOLD) / max(1, len(lateness)),
    }


def microcost():
    """Coste puro por-frame (sin asyncio) -> N teórico = 20ms / coste_por_llamada."""
    s = media.RtpSender(ssrc=1)
    N = 20000
    t0 = time.perf_counter()
    for i in range(N):
        _one_frame_work(s, i * FRAME_MS)
    per_call_us = (time.perf_counter() - t0) / N * 1e6
    return per_call_us, (FRAME_MS * 1000.0) / per_call_us


async def main():
    print("=== AMI voice bridge — benchmark sintético ===")
    print(f"python={sys.version.split()[0]}  cores={os.cpu_count()}  "
          f"ticks/nivel={NTICKS} ({SECONDS}s)  umbral_late={LATE_MS_THRESHOLD}ms")
    us, nmax = microcost()
    print(f"\n[microcoste] {us:.1f} µs/llamada/frame  ->  techo teórico ~{nmax:.0f} "
          f"llamadas (1 core, solo CPU, sin scheduling/IO)\n")
    print(f"{'N':>5} | {'CPU%(1core)':>11} | {'lat p50':>8} | {'lat p95':>8} | "
          f"{'lat max':>8} | {'% late':>7} | veredicto")
    print("-" * 78)
    knee = None
    for n in LEVELS:
        r = await run_level(n)
        ok = r["p95"] < LATE_MS_THRESHOLD and r["cpu_pct"] < 90
        if ok:
            knee = n
        verdict = "OK" if ok else "<< RODILLA"
        print(f"{r['n']:>5} | {r['cpu_pct']:>10.0f}% | {r['p50']:>6.1f}ms | "
              f"{r['p95']:>6.1f}ms | {r['pmax']:>6.1f}ms | {r['late_pct']:>6.1f}% | {verdict}")
        import gc; gc.collect()
        await asyncio.sleep(0.3)
    print("-" * 78)
    print(f"RODILLA (último N con p95<{LATE_MS_THRESHOLD}ms y CPU<90% de 1 core): "
          f"{knee if knee else '<10'} llamadas concurrentes.")
    print("Nota: es un LÍMITE SUPERIOR (sin syscalls de socket/TLS reales). "
          "La cifra usable en prod es algo menor.")


if __name__ == "__main__":
    asyncio.run(main())
