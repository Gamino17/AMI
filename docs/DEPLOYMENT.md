# AMI · despliegue del stack telco real

Esta guía describe el cambio del modo **mock** (lifecycle simulado en memoria,
ningún SMS ni llamada real) al modo **live** (Kannel + Asterisk hablando con el
trunk del partner). Pensada para que el partner haga su parte en su lado y
nosotros la nuestra en el VPS sin pisarnos.

## TL;DR — qué tiene que aportar el partner

1. **Trunk SMPP** para SMS:
   - host + puerto (típicamente 2775)
   - `system_id` + password (BIND_TRANSCEIVER)
   - opcionalmente `system_type`, TON/NPI, throttling
2. **Trunk SIP** para voz:
   - SBC host + puerto
   - usuario + password de registro
3. **Inventario de números** asignados a nuestra cuenta (rangos MSISDN).
4. **Whitelisting** de la IP pública del VPS donde corre AMI:
   - puerto SMPP saliente hacia su SMSC
   - puerto SIP (5060/udp y tcp) bidireccional
   - rango RTP (10000-10100/udp) hacia el VPS

Lo demás está ya en este repo y se levanta con `docker compose up -d`.

## Arquitectura del despliegue

```
                            ┌──────────────────────────────┐
                            │      Render (Web service)    │
                            │                              │
                            │   ami_api.py (REST + HTML)   │
                            │   ami_mcp.py (MCP HTTP)      │
                            │   AMI_TELCO_MODE=live        │
                            └──────────────┬───────────────┘
                                           │ HTTPS
                                           │ (sendsms a Kannel,
                                           │  ARI a Asterisk)
                                           ▼
                            ┌──────────────────────────────┐
                            │       VPS con IP pública     │
                            │                              │
                            │   ┌─────────┐  ┌──────────┐  │
                            │   │ kannel  │  │asterisk  │  │
                            │   └────┬────┘  └────┬─────┘  │
                            │        │ SMPP       │ SIP    │
                            │        ▼            ▼        │
                            └──────────────────────────────┘
                                     │            │
                                     ▼            ▼
                              ┌──────────────────────┐
                              │   Partner telco      │
                              │   (SMSC + SIP SBC)   │
                              └──────────────────────┘
```

**Por qué Render para ami_api y un VPS aparte para Kannel/Asterisk:**

- Render es perfecto para HTTP stateless con TLS gratis y deploys auto desde git.
- Kannel + Asterisk requieren puertos UDP (5060, 10000–10100), IP estable
  para whitelisting del partner, y procesos long-lived que no encajan en
  contenedores efímeros. Un VPS pequeño (4 vCPU, 4 GB RAM) sobra para v1.

## Paso 0 — Antes de empezar

- VPS con Ubuntu 22.04 LTS, IP pública estática, Docker + docker-compose instalado.
- Dominio (o subdominio) para Kannel HTTP si quieres exponerlo con TLS (recomendado):
  p.ej. `kannel.tuvps.example`. Idealmente cubrir con Caddy/Traefik delante.
- Acceso al dashboard de Render para tocar variables de entorno.

## Paso 1 — Clonar el repo en el VPS

```bash
git clone https://github.com/Gamino17/AMI.git
cd AMI/infra
cp .env.example .env
```

Edita `.env`:

- `AMI_PUBLIC_URL`: URL pública de tu ami_api en Render (p.ej. `https://api.protocolami.com`).
- `AMI_API_KEY`: el mismo valor que en el dashboard de Render.
- `AMI_TELCO_INBOUND_KEY`: **genera uno nuevo** con `openssl rand -hex 32`. Este
  mismo valor lo pegarás en Render para que las llamadas a `/v1/_telco/*` se
  autentiquen entre la VPS y Render.
- `SMPP_*` y `SIP_TRUNK_*`: lo que el partner te entregó.
- `KANNEL_SENDSMS_USER` / `KANNEL_SENDSMS_PASSWORD`: credenciales internas del
  HTTP gateway de Kannel (sólo se usan dentro del compose).
- `ARI_USERNAME` / `ARI_PASSWORD`: credenciales internas del ARI de Asterisk
  (sólo se usan dentro del compose).
- `EXTERNAL_IP`: la IP pública de tu VPS (necesaria si Asterisk está tras NAT).

## Paso 2 — Levantar el stack

```bash
docker compose up -d
docker compose logs -f kannel asterisk
```

Comprobar:

- **Kannel admin** en `http://localhost:13000/status?password=<status-password>`
  debe reportar el SMSC `partner_smpp` como `online`.
- **Asterisk CLI**: `docker compose exec asterisk asterisk -rvvvv` →
  `pjsip show endpoints` debe mostrar `trunk_partner` con un AOR `Avail`.

Si alguno falla:

- SMSC offline → revisar credenciales SMPP y que la IP del VPS esté
  whitelisteada en el SBC del partner.
- PJSIP unavailable → revisar password SIP, NAT/EXTERNAL_IP, y que el SBC
  acepte INVITE/REGISTER desde tu IP.

## Paso 3 — Exponer Kannel sendsms con TLS (opcional pero recomendado)

Para que Render llame a `https://kannel.tuvps.example/cgi-bin/sendsms` en
lugar de un IP:puerto raw HTTP, pon Caddy o Traefik delante:

```
# Caddyfile mínimo
kannel.tuvps.example {
    reverse_proxy 127.0.0.1:13013
}
```

## Paso 4 — Configurar Render para modo live

En el dashboard de Render, servicio `ami-mock-api` (o como lo hayas renombrado),
edita las variables de entorno:

| Variable                  | Valor                                                                  |
|---------------------------|------------------------------------------------------------------------|
| `AMI_TELCO_MODE`          | `live`                                                                  |
| `AMI_TELCO_INBOUND_KEY`   | el mismo que pusiste en `.env` del VPS                                  |
| `AMI_KANNEL_SENDSMS_URL`  | `https://kannel.tuvps.example/cgi-bin/sendsms`                          |
| `AMI_KANNEL_USERNAME`     | el `KANNEL_SENDSMS_USER` del `.env`                                     |
| `AMI_KANNEL_PASSWORD`     | el `KANNEL_SENDSMS_PASSWORD` del `.env`                                 |
| `AMI_KANNEL_DLR_URL`      | `https://api.protocolami.com/v1/_telco/sms/dlr`                         |
| `AMI_ARI_URL`             | `https://asterisk.tuvps.example/ari` (o `http://VPS_IP:8088/ari`)       |
| `AMI_ARI_USERNAME`        | el `ARI_USERNAME` del `.env`                                            |
| `AMI_ARI_PASSWORD`        | el `ARI_PASSWORD` del `.env`                                            |
| `AMI_ARI_TRUNK`           | `PJSIP/trunk_partner` (no cambiar)                                      |

Trigger redeploy. Una vez arriba, comprobar:

```bash
curl https://api.protocolami.com/v1/health
# {
#   "ok": true,
#   "service": "ami",
#   "telco": {
#     "adapter": "live",
#     "kannel_configured": true,
#     "ari_configured": true
#   }
# }
```

## Paso 5 — Smoke test end-to-end

Crea un MID de prueba (vía `/v1/demo/quick` o el flujo normal de contratación)
y prueba SMS y voz contra un móvil real tuyo:

```bash
# SMS
curl -X POST https://api.protocolami.com/v1/agent/sms/send \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+34600...", "body": "Hola desde AMI live"}'

# Llamada (necesitas un endpoint SIP donde la quieras recibir)
curl -X POST https://api.protocolami.com/v1/agent/calls/place \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+34600...", "callback_sip_uri": "sip:proj@sip.api.openai.com;transport=tls"}'
```

Si todo está bien:

- El SMS llega al móvil en segundos. `GET /v1/agent/sms` mostrará `delivered`
  cuando Kannel reciba el DLR del partner.
- La llamada suena en el móvil. Si tu `callback_sip_uri` apunta a Realtime de
  OpenAI, OpenAI dispara su webhook `realtime.call.incoming` y tu backend del
  cliente decide aceptar.

## Paso 6 — Configurar inbound por MID

Por cada número activo en producción que vaya a recibir llamadas:

```bash
curl -X POST https://api.protocolami.com/v1/mobile-identities/$MID/inbound-config \
  -H "Authorization: Bearer $AMI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"inbound_sip_uri": "sip:proj@sip.api.openai.com;transport=tls"}'
```

Sin esto configurado, cualquier llamada entrante a ese número se rechaza con
`486 Busy Here` (limpio, no rompe nada).

## Rollback al modo mock

```bash
# En Render
AMI_TELCO_MODE=mock
# (los otros AMI_KANNEL_*, AMI_ARI_* puedes dejarlos; el adapter mock los ignora)
```

Redeploy. El comportamiento vuelve a ser el v1: lifecycle simulado, ningún SMS
ni llamada saliente real. Útil si el partner tiene una incidencia o si quieres
hacer demo sin gastar tráfico.

## Costes operativos del modo live

- **VPS** (Hetzner/Scaleway/etc.): ~10-20 €/mes para v1.
- **Tráfico SMS**: lo factura el partner por mensaje cursado, no nosotros.
- **Tráfico voz**: ídem, por minuto cursado.
- **Render starter**: 7 $/mes por servicio web.

## Apéndice — qué hace cada pieza

- `infra/docker-compose.yml`: orquesta los tres servicios (ami_api opcional aquí
  para tests locales; en prod ami_api vive en Render).
- `infra/kannel/kannel.conf`: bind SMPP al partner + sendsms HTTP + DLR storage.
- `infra/kannel/sms-services.conf`: catch-all que reenvía MO entrante a AMI.
- `infra/asterisk/pjsip.conf`: trunk SIP al partner (registro + auth + AOR).
- `infra/asterisk/extensions.conf`: dialplan bridge-by-API; hooks que postean
  transiciones a `/v1/_telco/calls/{id}/status`.
- `infra/asterisk/ari.conf` + `http.conf`: habilita ARI sobre HTTP 8088.
- `ami_telco/live.py`: cliente HTTP de Kannel + ARI desde el backend.
