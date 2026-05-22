# AMI — Agent Mobile Identity Protocol

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).

AMI es un protocolo que permite a un agente AI contratar y aprovisionar su propia
identidad móvil (SIM, eSIM, número de teléfono) de forma autónoma. La única pieza
simulada en este repo es la SIM física (telco mock); el resto del flujo —oferta,
datos del cliente, contrato, firma vía página web y activación— se comporta como
producción y respeta la máquina de estados de la spec §17.6.

## Arquitectura

```text
Agente (Claude Desktop / Code / cliente MCP)
        |
        |  MCP tools (ami.*)
        v
ami_mcp.py        (MCP server: stdio o streamable-http :8001)
        |
        |  HTTP JSON + Bearer AMI_API_KEY
        v
ami_api.py        (REST API v1, motor de estados, página HTML de firma)
        |
        |  flujo end-to-end
        v
SIMRequest -> Offer -> Customer -> Contract -> Signature -> MobileIdentity
                                       ^
                                       |
                  GET /v1/sign/{id}   <-- firma desde navegador
                  POST /v1/sign/{id}/confirm
```

## Setup del venv

Con `uv` (recomendado, Python 3.13):

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Sin `uv` (necesita Python 3.10+):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Ejecutar local

### 1. Levantar la API mock (puerto 8000)

```bash
AMI_API_KEY=dev_key AMI_PUBLIC_URL=http://localhost:8000 python3 ami_api.py
```

Si no se setea `AMI_API_KEY`, la API arranca en modo dev sin auth.
`AMI_PUBLIC_URL` se usa para construir la `signature_url` que devuelve el contrato.

### 2. Probar el flujo completo

```bash
AMI_API_URL=http://localhost:8000 AMI_API_KEY=dev_key python3 demo_flow.py
```

`demo_flow.py` solo usa stdlib (sin `requests`). Recorre los 8 pasos: health,
SIMRequest + oferta, aceptar oferta, datos de cliente, contrato, firma vía
callback público, activación de MobileIdentity y consulta final de estado +
audit events.

### 3. Lanzar el MCP server

Stdio (para clientes locales tipo Claude Desktop / Claude Code):

```bash
AMI_API_URL=http://localhost:8000 AMI_API_KEY=dev_key .venv/bin/python ami_mcp.py
```

HTTP streamable (para clientes remotos):

```bash
AMI_API_URL=http://localhost:8000 AMI_API_KEY=dev_key .venv/bin/python ami_mcp.py http
```

El servidor HTTP escucha por defecto en `0.0.0.0:8001` (configurable con
`AMI_MCP_HOST` / `AMI_MCP_PORT`).

## Conectar el MCP a Claude Desktop / Claude Code (stdio)

Añade al `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ami": {
      "command": "/Users/danielgaminocano/Developer/AMI/.venv/bin/python",
      "args": ["/Users/danielgaminocano/Developer/AMI/ami_mcp.py"],
      "env": {
        "AMI_API_URL": "https://protocolami.com",
        "AMI_API_KEY": "<tu-api-key>"
      }
    }
  }
}
```

Reinicia el cliente y las 11 tools `ami.*` quedan disponibles para el agente.

## Conectar a un MCP HTTP remoto

Desde un cliente MCP que soporte transporte streamable-http, apunta a:

```
https://mcp.protocolami.com/mcp/      # remoto (Render expone en 443)
http://localhost:8001/mcp/                  # local
```

La barra final importa: sin ella el server responde 307. El SDK oficial sigue el redirect, pero hay clientes MCP que no.

`AMI_API_KEY` se inyecta en el entorno del proceso `ami_mcp.py` y se reenvía
como `Authorization: Bearer <token>` hacia el backend AMI. El protocolo MCP en
sí no exige autenticación adicional; el bearer es del API.

## Variables de entorno

| Variable          | Componente   | Descripción                                                           |
|-------------------|--------------|-----------------------------------------------------------------------|
| `AMI_API_URL`     | mcp / demo   | URL base del backend AMI. Default: `http://localhost:8000`.           |
| `AMI_API_KEY`     | api / mcp    | Bearer token. Si no se setea en la API, arranca en modo dev sin auth. |
| `AMI_PUBLIC_URL`  | api          | URL pública para construir `signature_url` en los contratos.          |
| `AMI_MCP_HOST`    | mcp http     | Bind host para transporte HTTP. Default: `0.0.0.0`.                   |
| `AMI_MCP_PORT`    | mcp http     | Puerto para transporte HTTP. Default: `8001`.                         |
| `PORT`            | api          | Puerto del backend HTTP. Default: `8000`.                             |

## Endpoints del API (v1)

- `GET  /v1/health` — healthcheck (público)
- `GET  /v1/sim-options` — países, tipos de SIM y capacidades
- `POST /v1/sim-requests` — crea SIMRequest y devuelve oferta inmediata
- `POST /v1/sim-requests/{id}/cancel` — cancela una SIMRequest no terminal
- `POST /v1/sim-requests/{id}/customer-data` — adjunta datos del cliente
- `POST /v1/offers/{id}/accept` — acepta una oferta
- `POST /v1/customers` — crea cliente suelto (compatibilidad)
- `POST /v1/contracts` — crea contrato + `signature_url`
- `GET  /v1/sign/{id}` — página HTML de firma (pública)
- `POST /v1/sign/{id}/confirm` — callback de firma desde el form (público)
- `POST /v1/contracts/{id}/mock-sign` — atajo programático de firma (legacy)
- `POST /v1/mobile-identities/activate` — activa la MobileIdentity tras la firma
- `GET  /v1/mobile-identities/{id}` — consulta una MobileIdentity
- `GET  /v1/sim-requests/{id}` — consulta una SIMRequest
- `GET  /v1/events` — últimos AuditEvents

## Tools MCP

Todas registradas con namespace `ami.*`:

- `ami.search_sim_options` — lista países, tipos de SIM/eSIM y capacidades.
- `ami.request_sim_offer` — crea una SIMRequest y devuelve la oferta del partner telco.
- `ami.accept_offer` — acepta una oferta antes de generar contrato.
- `ami.submit_customer_data` — envía los datos legales/fiscales del cliente.
- `ami.create_contract` — genera el contrato y devuelve `signature_url`.
- `ami.get_contract_status` — consulta el estado de un contrato.
- `ami.confirm_signature_status` — alias semántico para verificar la firma.
- `ami.activate_sim_identity` — inicia el provisioning tras la firma.
- `ami.get_identity_status` — consulta una MobileIdentity activa.
- `ami.cancel_request` — cancela una SIMRequest antes de la activación.
- `ami.list_events` — devuelve los últimos AuditEvents (debug).

## Estado actual y siguiente paso

Todo el flujo (oferta, datos, contrato, firma vía web y activación) corre
end-to-end con persistencia en memoria y auditoría. La única pieza simulada es
la SIM física: la activación devuelve un `phone_number`, un `provider_activation_id`
y una `esim_qr_url` ficticios.

El siguiente paso es enchufar:

- un **partner telco real** (Telefónica / Vodafone / un MVNO) detrás de
  `POST /v1/mobile-identities/activate`;
- un **proveedor de firma** (Signaturit, DocuSign u otro) detrás de
  `GET /v1/sign/{id}` + `POST /v1/sign/{id}/confirm`,

todo sin cambiar la API pública ni las tools MCP que ya consume el agente.

> Para deploy, ver `render.yaml`.
