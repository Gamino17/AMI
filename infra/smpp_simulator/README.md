# SMPP Simulator (local)

Un SMSC partner de juguete en Python stdlib pura.

## Para qué sirve

La stack SMS de AMI termina en Kannel, que habla SMPP 3.4 con el SMSC del
partner telco. Mientras el partner no nos entregue credenciales reales, no
podemos probar end-to-end (POST `/v1/agent/sms/send` → Kannel → SMSC → DLR
→ webhook → estado `delivered` en AMI).

Este simulador hace exactamente lo que haría el SMSC del partner: acepta el
`BIND_TRANSCEIVER` que Kannel inicia, responde a los `SUBMIT_SM` con un
`SUBMIT_SM_RESP`, y emite un `DELIVER_SM` con el DLR estándar para que
Kannel cierre el ciclo.

Es **solo** para desarrollo y testing local. En cuanto el partner active el
trunk SMPP, los flags `SMPP_HOST/PORT/USER/PASSWORD` de `infra/.env` apuntan
al partner real y este proceso queda apagado.

## Cómo arrancarlo

```bash
python infra/smpp_simulator/smpp_simulator.py                 # default 2775
python infra/smpp_simulator/smpp_simulator.py --port 2775
python infra/smpp_simulator/smpp_simulator.py --fail-rate 0.3 # 30% UNDELIV
python infra/smpp_simulator/smpp_simulator.py --no-dlr        # no emite DLR
python infra/smpp_simulator/smpp_simulator.py --help
```

Modos:

| Flag           | Efecto                                              | Para qué                       |
|----------------|------------------------------------------------------|--------------------------------|
| (default)      | Todos los SMS quedan `DELIVRD`                      | Happy path                     |
| `--fail-rate F`| Una fracción F (0..1) recibe DLR `UNDELIV err:001`  | Manejo de errores en AMI       |
| `--no-dlr`     | Responde `SUBMIT_SM_RESP` pero nunca emite DLR      | Probar timeouts / reintentos   |
| `--dlr-delay S`| Segundos entre `SUBMIT_SM_RESP` y `DELIVER_SM`      | Probar latencias               |

Los logs salen por stdout, una línea por PDU:

```
2026-05-22T12:00:00 INFO [smpp_simulator] accepted peer=172.17.0.1:54211
2026-05-22T12:00:00 INFO [smpp_simulator] bind ok system_id=test bind_type=TRX password_len=4
2026-05-22T12:00:00 INFO [smpp_simulator] [recv] SUBMIT_SM seq=2 system_id=test ...
2026-05-22T12:00:00 INFO [smpp_simulator] [send] SUBMIT_SM_RESP seq=2 ...
2026-05-22T12:00:00 INFO [smpp_simulator] [send] DELIVER_SM seq=1 ...
```

## Apuntar Kannel al simulador

Edita `infra/.env`:

```bash
# Mac (Docker Desktop): el host fuera del compose se resuelve por host.docker.internal
SMPP_HOST=host.docker.internal
# Linux: usa la IP del bridge docker0
# SMPP_HOST=172.17.0.1

SMPP_PORT=2775
SMPP_SYSTEM_ID=test
SMPP_PASSWORD=test
SMPP_SYSTEM_TYPE=
SMPP_SOURCE_TON=1
SMPP_SOURCE_NPI=1
```

El simulador acepta cualquier `system_id`/`password`, así que estos valores
son convención local. Cuando llegue el partner, sustituye por los suyos y
para el simulador.

## Probar un envío end-to-end (outbound)

En tres terminales:

```bash
# Terminal 1 — el SMSC falso
python infra/smpp_simulator/smpp_simulator.py --port 2775

# Terminal 2 — la stack AMI (ami_api + kannel)
cd infra && docker compose up

# Terminal 3 — disparar un envío
curl -X POST http://localhost:8000/v1/agent/sms/send \
  -H "Authorization: Bearer <agent_token>" \
  -H "Content-Type: application/json" \
  -d '{"to":"+34600111222","body":"hola mundo"}'
```

Qué deberías ver:

1. En el log del simulador: `BIND_TRANSCEIVER` (al arrancar Kannel), luego
   `SUBMIT_SM seq=N from=AMI to=+34600111222 len=10 message_id=<hex>`.
2. ~200 ms después: `DELIVER_SM (DLR) message_id=<hex> stat=DELIVRD`.
3. En ami_api: el mensaje pasa de `queued` → `sent` → `delivered`.
4. `GET /v1/agent/sms` lo devuelve con `status: delivered`.

Para probar el camino UNDELIV: arranca el simulador con `--fail-rate 1.0`,
manda otro SMS, verifica que termina en `failed`.

Para probar timeouts: arranca con `--no-dlr`. Kannel hace `SUBMIT_SM` ok,
pero el DLR no llega; AMI debe quedarse en `sent` y eventualmente reintentar
o expirar según la política configurada.

## Probar inbound (MO)

El simulador puede inyectar un mensaje mobile-originated en una sesión bound
viva. Modo más simple: arrancar el simulador en una terminal interactiva y
escribir en stdin:

```
mo +34999111222 AMI hola estoy probando
```

Esto manda un `DELIVER_SM` con `esm_class=0` (no DLR) a la primera sesión
con BIND activo. Kannel lo recibe, lo enruta por su `sms-service` catch-all
hasta el webhook `/v1/_telco/sms/inbound` de ami_api, y aparece en
`GET /v1/agent/sms` con `direction: inbound`.

Si más adelante prefieres dispararlo desde un script o un test, importa el
módulo y llama directamente:

```python
from smpp_simulator import SmppSimulator
sim = SmppSimulator(port=2775); sim.start()
sim.inject_mo(source="+34999111222", destination="AMI", text="hola")
```

## Tests

```bash
python -m pytest tests/test_smpp_simulator.py -q
```

Los tests no necesitan Kannel ni docker compose: arrancan el simulador en
un puerto efímero y hablan SMPP 3.4 binario directamente desde Python.

## Limitaciones conscientes

- Solo SMPP 3.4. Si el partner exige 3.3 o 5.0 lo iteramos cuando toque.
- Sin TLS (`bearerbox` con `ssl=true`). El partner real probablemente no
  pida TLS sobre SMPP; si lo hace, añadimos `ssl.SSLContext` aquí mismo.
- Sin almacenamiento persistente: si reinicias el simulador, los message_id
  emitidos antes se pierden. Lo que importa para los DLR de Kannel es el
  `message_id` del SUBMIT en curso, no histórico.
- No fragmenta mensajes largos (`UDH`). Si vamos a enviar SMS de >160
  caracteres de prueba, hay que ampliar `parse_submit_sm` para leer el TLV
  `message_payload`.
