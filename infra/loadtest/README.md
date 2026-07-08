# AMI voice bridge — kit de load-test (medir la rodilla de concurrencia)

Responde con **datos medidos** a "¿cuántas llamadas simultáneas aguanta el VPS?".
El limitador real no es Asterisk (relay μ-law barato) ni la RAM/red, sino el
**`ami_voice_bridge`** (proceso Python, asyncio/GIL, una coroutine por llamada).
Este kit lo estresa de punta a punta.

## ⚠️ Regla de oro
**Ejecutar SIEMPRE contra una caja CLONADA, nunca contra la demo viva** (`2.25.146.248`).
Un flood a ~100 llamadas puede tumbar el registro del trunk y dejar la demo sin
audio. En Hostinger: crea un snapshot del KVM 2 y levanta un VPS gemelo para la
prueba.

## Piezas
- `mock_agent.py` — sustituye a openclaw/OpenAI: implementa el lado `voice_url`
  (webhook → TwiML → WS Media Streams con **echo** del audio). Así medimos el
  bridge sin machacar al agente real ni chocar con su límite de concurrencia.
- `sipp/uac_pcmu.xml` — escenario SIPp: coloca llamadas con RTP μ-law real.
- `run.sh` — rampa en escalones + muestreo de CPU/mem/canales/stats del bridge.

## Definir "el límite" (mides contra un umbral, no contra "hasta que peta")
Señales de fallo por orden de aparición:
1. **Calidad**: sube `underruns` / `inbound_drops` / `outbuf_drops` / `decode_fail`
   (el bridge los loguea por llamada: `docker logs ami_voice_bridge | grep stats:`).
2. **CPU**: el core del bridge >85% (`docker stats ami_voice_bridge`).
3. **Setup**: llamadas que no montan (agotamiento de puertos, timeouts en SIPp).
4. **Latencia**: crece el retardo del audio.

**Rodilla = primer escalón donde cruzas tu umbral** (sugerido: underruns >10% o CPU >85%).

---

## Vía rápida (riesgo casi cero): micro-benchmark de 1 llamada
No necesita SIPp ni clon; da el **coste por llamada** para extrapolar.
1. Asegura una llamada activa por el bridge (voice_url apuntando a un agente/mock).
2. Con la llamada en curso, en el VPS:
   ```
   docker stats --no-stream ami_voice_bridge
   ```
   Anota el `CPU%` con 1 llamada (idle es ~0%).
3. Al colgar: `docker logs --since=2m ami_voice_bridge | grep stats:` (frames, underruns).
4. Extrapola: **concurrencia ≈ (≈90% de 1 core) ÷ (CPU% por llamada)**. Cruza con la
   matemática de puertos (ver abajo). Convierte el estimado "~50-100" en algo medido.

---

## Prueba completa (número real de la rodilla) — en el CLON

### 0. Preparar el clon
- Snapshot del KVM 2 → levantar VPS gemelo.
- **Ampliar puertos RTP** (si vas a medir >~50): en el clon, subir `rtpend` en
  `infra/asterisk/rtp.conf` **Y** el mapeo `ports:` de `docker-compose.yml`
  (p.ej. `10000-11000:10000-11000/udp`) **Y** ufw, y recrear el contenedor
  (`docker compose up -d --force-recreate asterisk`). Los tres deben coincidir o
  las llamadas conectan **sin audio**.

### 1. Levantar el mock (en el clon o en una máquina alcanzable por el bridge)
```
pip install aiohttp
MOCK_PUBLIC=<IP_mock>:8099 python3 mock_agent.py      # ws://
# si el bridge exige wss:// (is_safe_wss_url): MOCK_SCHEME=wss ... (cert self-signed)
```
> TLS: el bridge valida `wss://`. En el clon, o sirves `wss` (el mock genera cert
> self-signed y desactivas verificación en el bridge), o parcheas
> `ami_voice_streams.is_safe_wss_url` para aceptar `ws://` **solo en el clon**.

### 2. Crear un MID de test con voice_url → mock
Da de alta un MID (o usa el admin `/v1/admin/mobile-identities/manual`) y apunta
su voz al mock:
```
curl -X POST -H "Authorization: Bearer <AMI_API_KEY_del_clon>" \
  -d '{"voice_url":"http://<IP_mock>:8099/voice"}' \
  https://<clon>/v1/mobile-identities/<mid>/voice-config
```
Anota el **número** del MID → es el `-s` de SIPp.

### 3. Conseguir el pcap de audio
Copia un pcap G.711 u-law a `sipp/pcmu.pcap` (los ejemplos de SIPp traen
`g711u.pcap` / `pcmu.pcap`).

### 4. Lanzar la rampa
```
CLONE_SSH=root@<clon_ip> ASTERISK_IP=<clon_ip> NUMBER=<numero_mid_test> \
LEVELS="5 10 25 50 75 100" HOLD=120 ./run.sh
```
Genera `loadtest_report_*.txt` con, por escalón: CPU/mem del bridge y Asterisk,
canales activos y stats (underruns/drops) del bridge.

### 5. Leer el resultado
Busca el primer escalón donde: underruns% sube claramente, o CPU del bridge >85%,
o SIPp reporta fallos de setup. Ese es tu **número real** de llamadas concurrentes
a calidad aceptable.

---

## Matemática de puertos (recordatorio, corre en paralelo a la CPU)
Cada llamada consume ~4 puertos RTP del pool `rtp.conf` (pata trunk + pata
externalMedia, RTP+RTCP). Con `10000-10100` (~101 puertos) → **~25 llamadas** tope
duro. Para medir más, amplía el rango **y** el `ports:` de Docker **y** ufw juntos.

## Caveats honestos
- **El techo real en producción suele ser el agente de voz externo** (OpenAI /
  ElevenLabs / openclaw), no el VPS — mídelo por separado. Este kit mide el VPS.
- μ-law 8kHz tiene un techo de calidad inherente (PSTN); mides "aceptable", no "HD".
- El mock hace echo perfecto (sin jitter de red real) → el número que saques es el
  **límite del VPS/bridge en condiciones ideales**; con un agente real sobre red
  con jitter, la cifra usable será algo menor.
