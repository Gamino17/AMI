# ami_voice_bridge

Puente de voz **Twilio-compatible** de AMI. Container Python independiente que
deja que cualquier agente AI construido sobre la convención Twilio Voice +
Media Streams (openclaw, Vapi, Retell, Bland, …) reciba llamadas **a través de
AMI sin modificarse**.

> Estado: **Paso 2/3 — capa de SIGNALING**. El audio (RTP ↔ WebSocket Media
> Streams) llega en el Paso 3 y ya está contractualmente preparado (ver más
> abajo y el hook `start_media_bridge` en `bridge.py`).

```
Llamante (PSTN)
     ↓ SIP
Partner telco CO (SBC)
     ↓ SIP
Asterisk (VPS) ── Stasis app `ami_voice`
     ↓ ARI events (WebSocket)               ↓ externalMedia (RTP μ-law 8kHz)  ← Paso 3
ami_voice_bridge (este container) ───────────┘
     ↓ HTTP webhook form-urlencoded (formato Twilio)
Cliente (openclaw / Vapi / Retell / …)
     ← TwiML response                        ↑↓ WS Media Streams μ-law base64  ← Paso 3
```

---

## 1. Qué es (lo que SÍ hace hoy — Paso 2)

Un proceso `asyncio` que orquesta la **señalización** de la llamada de voz:

1. **Cliente ARI de eventos.** Mantiene **un** WebSocket persistente contra
   Asterisk (`ws://asterisk:8088/ari/events?app=ami_voice&api_key=user:pass`),
   suscrito a la Stasis app `ami_voice`. Reconecta solo con backoff (Asterisk
   puede no estar listo al arranque del container).
2. **StasisStart → webhook.** Cuando un channel entra en la Stasis app, lee sus
   variables vía ARI REST (`GET /channels/{id}/variable`):
   `CALLBACK_VOICE_URL`, `MID_PHONE`, `AMI_CALL_ID`, `AMI_FROM`. Reconstruye un
   Call-dict mínimo, arma el POST estilo Twilio y lo despacha al `voice_url` del
   cliente.
3. **Ejecuta el TwiML** de la respuesta contra ARI REST, en orden, parando en el
   primer verbo terminal:
   - `reject` → cuelga el channel (`DELETE /channels/{id}`).
   - `hangup` → cuelga el channel.
   - `say` → `POST /channels/{id}/play` (**stub v1**: loguea y reproduce
     best-effort; TTS real es v2). No terminal: continúa al siguiente verbo.
   - `pause` → `asyncio.sleep`.
   - `stream` → invoca el **hook `start_media_bridge`** (Paso 3, ver §2).
     Terminal: cede el channel al puente de audio.
4. **Limpieza** del estado por channel en `StasisEnd` / `ChannelDestroyed`.
5. **Healthcheck.** Sirve `GET /health` en un puerto HTTP propio para el
   healthcheck de Docker.

**Reutiliza** el módulo de Paso 1 `ami_voice_streams.py` (copiado junto a
`bridge.py` en la imagen) en vez de reimplementar nada:
`twilio_voice_payload`, `dispatch_voice_webhook`, `parse_twiml`,
`is_safe_wss_url`. (No usa `new_voice_config`, que sí importa `ami_api`; las
cuatro funciones que el bridge usa no arrastran `ami_api`.)

---

## 2. Qué NO hace todavía (Paso 3 — capa de AUDIO)

El bridge **no abre audio**: no levanta `externalMedia` RTP ni el WebSocket de
Media Streams hacia el cliente. El verbo `stream` solo invoca el hook
`start_media_bridge(channel_id, ws_url, params, session)`, que en Paso 2
**únicamente loguea la intención y guarda la sesión**. El cuerpo del hook
documenta como `TODO Paso 3` exactamente lo que falta:

1. `POST /channels/{id}/externalMedia` (`format: ulaw`) → crea el canal
   UnicastRTP μ-law 8kHz.
2. `POST /bridges` + `addChannel` del canal SIP y del canal externalMedia.
3. Abrir el cliente WS hacia `ws_url` (el `wss://` del TwiML del cliente) y
   completar el handshake Media Streams.
4. Loop bidireccional μ-law base64 (RTP ↔ WS).
5. Pacer de salida 160 bytes / 20 ms.

La firma y el contrato del hook quedan cerrados para que el Paso 3 encaje **sin
rediseño**. Mientras tanto, con `stream`, el channel queda en Stasis hasta que
cuelguen (sin audio la llamada no progresa: es PoC de signaling).

---

## 3. Contrato openclaw que respetará el Paso 3

El Paso 2 ya emite el webhook y parsea el TwiML según este contrato; el Paso 3
implementa el WebSocket de audio respetándolo 1:1 (sin variantes inventadas).

**Webhook al cliente** — `application/x-www-form-urlencoded`, campos Twilio
(`CallSid`, `From`, `To`, `Direction`, `CallStatus`, …). Respuesta `text/xml`
TwiML. El TwiML típico de audio:

```xml
<Response>
  <Connect>
    <Stream url="wss://.../voice/stream/realtime/{token}"/>
  </Connect>
</Response>
```

(El `token` del cliente es efímero, de un solo uso — responsabilidad del
cliente, no del bridge.)

**WebSocket Media Streams (formato Twilio 1:1):**

- AMI → cliente:
  - `{"event":"start","start":{"streamSid":"...","callSid":"..."}}`
  - `{"event":"media","media":{"payload":"<b64>","timestamp":"...","track":"inbound"}}`
  - `{"event":"mark","mark":{"name":"..."}}`
  - `{"event":"stop"}`
- cliente → AMI:
  - `{"event":"media","streamSid":"...","media":{"payload":"<b64>"}}`
  - `{"event":"mark","streamSid":"...","mark":{"name":"..."}}`
  - `{"event":"clear","streamSid":"..."}`

**Audio:** G.711 μ-law (PCMU) 8kHz mono, base64 en `media.payload`. Pacer de
salida **160 bytes / 20 ms** (ritmo, no ráfagas).

**Identificadores:** `callSid` del evento `start` **DEBE** ser el
`AMI_CALL_ID` (`call_xxx`), único por llamada; `streamSid` puede ser cualquier
id por sesión.

---

## 4. Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `AMI_ARI_URL` | `http://asterisk:8088/ari` | Base REST de ARI. De aquí se deriva la URL WS `/ari/events` (mismo nombre que consume `ami_telco/live.py`). |
| `AMI_ARI_USERNAME` | — | Usuario Basic auth de ARI (`ari.conf`). |
| `AMI_ARI_PASSWORD` | — | Password Basic auth de ARI. |
| `AMI_ARI_APP` | `ami_voice` | Nombre de la Stasis app a la que se suscribe el WS. |
| `AMI_VOICE_BRIDGE_HEALTH_PORT` | `8090` | Puerto del servidor `/health` para el healthcheck de Docker. |
| `AMI_VOICE_WEBHOOK_TIMEOUT` | `5` | Timeout (segundos) del POST al `voice_url` del cliente. |
| `AMI_VOICE_LOG_LEVEL` | `INFO` | Nivel de logging del bridge. |

En `docker-compose.yml`, `AMI_ARI_USERNAME` / `AMI_ARI_PASSWORD` se toman de
`${ARI_USERNAME}` / `${ARI_PASSWORD}`.

---

## 5. Dependencias del dialplan / backend (NO incluidas aquí)

El bridge **no recibe nada** hasta que se apliquen, en ficheros compartidos, los
cambios que activan el flujo Stasis. Hoy el repo origina/entra **sin Stasis**
(`live.py` usa context/extension y el dialplan hace `Dial` directo). Estos
cambios están especificados en el bloque **`ami_changes`** del plan (los aplica
un humano, no este workflow):

- **Dialplan** (`infra/asterisk/extensions.conf`, rama entrante
  `[from_partner]`): añadir una rama que setee
  `CALLBACK_VOICE_URL` / `MID_PHONE` / `AMI_CALL_ID` / `AMI_FROM` en el channel
  y haga `Stasis(ami_voice, …)` en lugar de `Dial`. Esas son justo las
  variables que el bridge lee por ARI.
- **Backend `ami_api.py`**: endpoints `voice-config`
  (`POST`/`GET /v1/mobile-identities/{mid}/voice-config`, auth customer key) que
  registran/listan el `voice_url`, y la precedencia voice-config sobre el
  forward SIP en `route_call_inbound` (devuelve `mode: "voice"` con
  `voice_url` + `mid_phone`).
- **`ami_inbound.sh`**: extender para emitir `CALLBACK_VOICE_URL=` / `MID_PHONE=`
  cuando `mode == "voice"`, sin romper el parseo `CUT` del modo SIP existente.

Sin estos cambios el bridge conecta al WS de ARI y se queda esperando
`StasisStart` (no llega ninguno).

---

## 6. Cómo correr / testear

### Local (sin Asterisk real)

```bash
# Única dependencia externa:
pip install -r infra/ami_voice_bridge/requirements.txt   # websockets>=12.0

# Tests de signaling (mockean ARI / HTTP, no necesitan Asterisk):
pytest tests/test_voice_bridge.py -v
```

Los tests cubren: construcción de la URL WS de eventos, ejecución de acciones
TwiML (`hangup` / `reject` / `say`+`hangup` / `stream`→hook), el reuso
end-to-end de `ami_voice_streams` en `fetch_twiml` (incluido que el POST lleva
`CallSid == AMI_CALL_ID`), el hangup implícito ante webhook fallido, el
`StasisStart` sin `voice_url`, y el endpoint `/health`.

### Arranque manual del proceso

```bash
AMI_ARI_URL=http://localhost:8088/ari \
AMI_ARI_USERNAME=ami_ari AMI_ARI_PASSWORD=secret \
AMI_ARI_APP=ami_voice \
python infra/ami_voice_bridge/bridge.py
```

```bash
curl -s http://localhost:8090/health
# {"status":"ok","app":"ami_voice","sessions":0,"ari_ws_connected":true}
```

---

## 7. Despliegue

**Solo VPS / `docker-compose`.** El bridge se añade como un servicio en
`infra/docker-compose.yml` (build `context: ..` para que el `COPY` de
`ami_voice_streams.py` desde la raíz del repo funcione; alcanza a Asterisk por
DNS interno de `ami_net` en `asterisk:8088`):

```bash
docker compose -f infra/docker-compose.yml up -d --build ami_voice_bridge
docker compose -f infra/docker-compose.yml logs -f ami_voice_bridge
```

En Paso 2 **no publica puertos al host** (el WS de eventos es saliente hacia
Asterisk; `/health` es interno al compose). Los puertos RTP se añadirán en el
Paso 3, cuando exista `externalMedia`.

> **NO se despliega en Render.** `render.yaml` no contempla este servicio y no
> debe tocarse: el bridge requiere conectividad WebSocket/RTP directa con
> Asterisk dentro del VPS. Render aloja únicamente `ami-mock-api` (el backend
> HTTP).

---

## 8. Dependencias y por qué

| Componente | Tipo | Por qué |
|---|---|---|
| `websockets>=12.0` | **Única dep externa** (pip) | La stdlib **no** trae cliente WebSocket y el bridge necesita un WS persistente contra `/ari/events` para recibir `StasisStart` en tiempo real. Sin deps transitivas pesadas. |
| `asyncio` | stdlib | Event loop único que orquesta la conexión WS de eventos y un handler por `StasisStart`. |
| `urllib.request` | stdlib | Llamadas ARI REST (GET variable, POST play/continue, DELETE channel) con Basic auth, **reutilizando exacto** el patrón de `ami_telco/live.py`. Se ejecuta vía `run_in_executor` para no bloquear el loop. Evita `httpx`/`aiohttp`. |
| `http.server` | stdlib | Servidor `/health` en un thread daemon aparte (mismo patrón que `ami_api.py`). |
| `ami_voice_streams` | módulo del repo (Paso 1) | **Reutilizado**, no es dep pip: se copia junto a `bridge.py`. Se usan `twilio_voice_payload`, `dispatch_voice_webhook`, `parse_twiml`, `is_safe_wss_url`. |
