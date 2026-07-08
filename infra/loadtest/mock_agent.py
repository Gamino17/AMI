#!/usr/bin/env python3
"""Mock voice agent para load-testing del ami_voice_bridge.

Reemplaza a openclaw/OpenAI en la prueba de carga: implementa el lado
"voice_url" del contrato AMI para que el bridge ejercite TODO su camino
(webhook -> TwiML -> WS Media Streams -> RTP<->WS de ida y vuelta) SIN
depender del agente real (ni de su coste ni de sus límites de concurrencia).

Qué hace, exactamente:
  1) HTTP POST {voice_url}  (form-urlencoded Twilio-compat, firmado con
     X-AMI-Signature)  ->  responde TwiML:
         <Response><Connect><Stream url="wss?://<PUBLIC>/ws"/></Connect></Response>
  2) WS {stream_url}: acepta la conexión del bridge y sigue el contrato
     (infra/ami_voice_bridge/media.py, sección 4):
        AMI -> mock:  {"event":"start","start":{"streamSid","callSid"}}
                      {"event":"media","media":{"payload":"<b64>",...}}
                      {"event":"stop"}
        mock -> AMI:  {"event":"media","streamSid":"...","media":{"payload":"<b64>"}}
     El mock hace ECHO del audio entrante -> fuerza la ruta WS->RTP del bridge
     (así medimos las DOS direcciones, no solo la de subida).

Métricas: imprime cada 2s sesiones activas + frames in/out agregados, que es
tu gauge de concurrencia real vista desde el lado agente.

Uso:
    pip install aiohttp
    # ws:// (simple, para red de test):
    MOCK_PUBLIC=10.0.0.5:8099 python3 mock_agent.py
    # wss:// (si el bridge exige wss; genera cert self-signed):
    MOCK_SCHEME=wss MOCK_PUBLIC=host:8099 python3 mock_agent.py

Nota TLS: el bridge valida `wss://` (ami_voice_streams.is_safe_wss_url). En la
caja CLONADA de test, o bien sirves wss con este cert self-signed y desactivas
la verificación en el bridge, o parcheas is_safe_wss_url para aceptar ws://
SOLO en el clon. Ver README.
"""
from __future__ import annotations
import asyncio, json, os, ssl, sys

try:
    from aiohttp import web, WSMsgType
except ImportError:
    sys.exit("Falta aiohttp -> pip install aiohttp")

SCHEME = os.environ.get("MOCK_SCHEME", "ws")            # ws | wss
PUBLIC = os.environ.get("MOCK_PUBLIC", "127.0.0.1:8099")  # host:port público
BIND   = os.environ.get("MOCK_BIND", "0.0.0.0")
PORT   = int(os.environ.get("MOCK_PORT", PUBLIC.rsplit(":", 1)[-1] or "8099"))
ECHO   = os.environ.get("MOCK_ECHO", "1") == "1"        # devolver audio (ruta de bajada)

STATS = {"sessions": 0, "peak": 0, "frames_in": 0, "frames_out": 0}


async def voice(request: web.Request) -> web.Response:
    """voice_url: responde el TwiML que apunta el Stream a nuestro propio WS."""
    await request.read()  # drena el body (form-urlencoded firmado); no lo validamos en carga
    stream_url = f"{SCHEME}://{PUBLIC}/ws"
    twiml = (f'<?xml version="1.0" encoding="UTF-8"?>'
             f'<Response><Connect><Stream url="{stream_url}"/></Connect></Response>')
    return web.Response(text=twiml, content_type="text/xml")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    STATS["sessions"] += 1
    STATS["peak"] = max(STATS["peak"], STATS["sessions"])
    stream_sid = None
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                frame = json.loads(msg.data)
            except Exception:
                continue
            ev = frame.get("event")
            if ev == "start":
                stream_sid = (frame.get("start") or {}).get("streamSid")
            elif ev == "media":
                STATS["frames_in"] += 1
                if ECHO and stream_sid:
                    payload = (frame.get("media") or {}).get("payload")
                    if payload:
                        await ws.send_str(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": payload},
                        }))
                        STATS["frames_out"] += 1
            elif ev == "stop":
                break
    finally:
        STATS["sessions"] -= 1
    return ws


async def _stats_loop():
    last_in = 0
    while True:
        await asyncio.sleep(2)
        d_in = STATS["frames_in"] - last_in
        last_in = STATS["frames_in"]
        print(f"[mock] sesiones={STATS['sessions']:>4} pico={STATS['peak']:>4} "
              f"frames_in/2s={d_in:>6} out_total={STATS['frames_out']}", flush=True)


def main():
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_route("*", "/voice", voice)
    app.router.add_get("/ws", ws_handler)
    app.on_startup.append(lambda a: a.loop.create_task(_stats_loop()))

    ssl_ctx = None
    if SCHEME == "wss":
        # cert self-signed efímero (solo para la caja de test)
        import subprocess, tempfile
        d = tempfile.mkdtemp()
        crt, key = f"{d}/c.pem", f"{d}/k.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", key, "-out", crt, "-days", "3", "-subj", "/CN=mock"],
                       check=True)
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(crt, key)

    print(f"[mock] {SCHEME}://{PUBLIC}  bind={BIND}:{PORT}  echo={ECHO}", flush=True)
    web.run_app(app, host=BIND, port=PORT, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
