# AMI — Agent Mobile Identity Protocol · Spec técnica

**Versión:** 1.0  
**Estado:** referencia (la implementación de este repo es la implementación de referencia)  
**Licencia:** MIT (ver `LICENSE` en el repo)

AMI es un protocolo abierto que estandariza cómo un agente AI **solicita, contrata y activa una identidad móvil** (SIM, eSIM o número de teléfono). Define entidades, máquina de estados, endpoints REST y tools MCP para que un agente recorra el flujo `solicitud → oferta → datos cliente → contrato → firma → MobileIdentity activa` sin pasar por procesos pensados para humanos.

AMI v1 cubre **contratación y aprovisionamiento**. La operación posterior (llamadas, SMS, WhatsApp, datos, OTP) queda fuera del scope v1 — puede aparecer como AMI Operations v2 o como módulos separados.

---

## Tabla de contenidos

1. [Flujo principal de contratación](#1-flujo-principal-de-contratación)
2. [Entidad central: MobileIdentity](#2-entidad-central-mobileidentity)
3. [API y Protocolo](#3-api-y-protocolo)
4. [Arquitectura conceptual](#4-arquitectura-conceptual)
5. [Casos de uso iniciales](#5-casos-de-uso-iniciales)
6. [Seguridad, compliance y gobernanza](#6-seguridad-compliance-y-gobernanza)
7. [Definición técnica AMI v1](#7-definición-técnica-ami-v1)
   - 7.1 Alcance funcional
   - 7.2 Comunicación agente → AMI
   - 7.3 Tools MCP v1
   - 7.4 API REST v1
   - 7.5 Entidades técnicas
   - 7.6 Motor de estados
   - 7.7 Ejemplo completo paso a paso
   - 7.8 Stack de referencia
   - 7.9 Principio de diseño

---

## 1. Flujo principal de contratación

### Paso 1 — El agente solicita número

```json
{
  "tool": "ami.request_sim_offer",
  "input": {
    "country": "ES",
    "capabilities": ["voice", "sms"],
    "purpose": "agent_identity",
    "agent_name": "Agente01"
  }
}
```

### Paso 2 — AMI devuelve oferta

```json
{
  "offer_id": "offer_123",
  "country": "ES",
  "capabilities": ["voice", "sms"],
  "monthly_price": "8.90 EUR",
  "setup_fee": "5.00 EUR",
  "requires_contract": true,
  "requires_customer_data": true,
  "expires_at": "2026-05-15T23:59:59Z"
}
```

### Paso 3 — Cliente aporta datos

```json
{
  "tool": "ami.submit_customer_data",
  "input": {
    "sim_request_id": "simreq_abc",
    "customer": {
      "legal_name": "Acme S.L.",
      "tax_id": "B00000000",
      "address": "...",
      "billing_email": "...",
      "representative_name": "..."
    }
  }
}
```

### Paso 4 — AMI genera contrato y URL de firma

```json
{
  "contract_id": "contract_789",
  "signature_url": "https://.../v1/sign/contract_789",
  "status": "signature_pending",
  "expires_at": "2026-05-15T23:59:59Z"
}
```

### Paso 5 — Tras firma, se activa la identidad móvil

```json
{
  "mobile_identity_id": "mid_abc",
  "phone_number": "+34910000000",
  "status": "active",
  "capabilities": ["voice", "sms"],
  "contract_id": "contract_789"
}
```

---

## 2. Entidad central: MobileIdentity

`MobileIdentity` representa una identidad móvil contratada, gobernada y operable por agentes.

```json
{
  "mobile_identity_id": "mid_abc",
  "phone_number": "+34910000000",
  "status": "active",
  "owner": {
    "type": "company",
    "legal_name": "Acme S.L.",
    "tax_id": "B00000000"
  },
  "agent": {
    "agent_id": "demerzel",
    "display_name": "Agente01",
    "role": "business_assistant"
  },
  "capabilities": ["voice", "sms"],
  "contract_id": "contract_789",
  "limits": {
    "monthly_spend_eur": 100,
    "daily_messages": 200,
    "allowed_countries": ["ES"],
    "blocked_prefixes": ["premium", "international_high_risk"]
  },
  "policy": {
    "human_approval_required_for_external_first_contact": true,
    "recording_enabled": false,
    "transcription_enabled": true,
    "retention_days": 90
  }
}
```

---

## 3. API y Protocolo

AMI expone tres interfaces:

1. **REST API** para integraciones tradicionales.
2. **MCP Server** (Model Context Protocol) para agentes.
3. **SDK / CLI** para desarrolladores y pruebas.

### 3.1 Métodos de contratación (v1)

Auth: `AMI_API_KEY` del customer (Nivel 1).

- `ami.search_sim_options`
- `ami.request_sim_offer`
- `ami.accept_offer`
- `ami.submit_customer_data`
- `ami.create_contract`
- `ami.get_contract_status`
- `ami.confirm_signature_status`
- `ami.activate_sim_identity` — devuelve el `agent_token` scoped al MID, **una sola vez**
- `ami.get_identity_status`
- `ami.cancel_request`
- `ami.rotate_agent_token` — hard rotate: invalida el token anterior al instante

### 3.2 Métodos de operación (Operations v2)

Auth: `agent_token` scoped al MID (Nivel 2).

**SMS:**
- `ami.send_sms(to, body)` — encola; transición `queued → sent → delivered`
- `ami.list_sms(limit, direction)` — historial scoped al MID

**Voz (bridge-by-API):**
- `ami.place_call(to, callback_sip_uri)` — origina la llamada al PSTN y la
  bridgea por SIP al endpoint del cliente (su URI SIP de motor de voz en
  tiempo real, PBX propio, o cualquier destino que hable SIP). AMI cursa la
  pipa; el "cerebro" de la voz vive en el endpoint del cliente.
- `ami.list_calls(limit, direction)`, `ami.get_call(call_id)`
- `ami.hangup_call(call_id)`
- `ami.set_inbound_sip_uri(mid, sip_uri)` — auth Nivel 1; configura a dónde
  reenviar las llamadas entrantes del MID (típicamente el mismo endpoint
  Realtime del cliente). Sin esto configurado, las entrantes se rechazan.

**Audit:**
- `ami.list_events`

### 3.3 Métodos de gobierno (v2 — pendientes)

- `ami.set_limits`, `ami.get_usage`
- `ami.suspend_identity`, `ami.release_number`
- `ami.audit_log`

### 3.4 Modelo de voz: AMI = operador, NO Realtime

Tres piezas distintas en una llamada de agente:

1. **Operador / SIP provider** — pieza que AMI ocupa. Compra/asigna números,
   cursa llamadas al PSTN, ofrece SIP-out al endpoint del cliente.
2. **Backend del cliente / agente** — decide si acepta la llamada, con qué
   prompt, qué tools tiene, logs, permisos, límites.
3. **Motor de voz Realtime** — recibe/manda audio en vivo y ejecuta el agente
   de voz con baja latencia.

AMI ocupa **únicamente la pieza 1**. Las piezas 2 y 3 son del cliente. Esto
mantiene la analogía con cualquier operador clásico: el operador no graba ni
transcribe la llamada, solo te la cursa. El coste de Realtime corre en la
cuenta del cliente; AMI factura por número + minutos cursados.

Patrón saliente típico:

```
agente → tool.phone.call → backend.cliente
                            ↓ POST /v1/agent/calls/place {to, callback_sip_uri}
                          AMI (operador)
                            ↓ marca al PSTN
                            ↓ bridgea SIP al callback_sip_uri
                          endpoint del cliente (Realtime / PBX)
```

Patrón entrante:

```
red telco → AMI (recibe la llamada)
              ↓ resuelve inbound_sip_uri del MID
              ↓ SIP-forward
            endpoint del cliente
```

---

## 4. Arquitectura conceptual

```text
Agente AI / Empresa
        │
        ▼
AMI MCP Server / API Gateway
        │
        ├── Contracting Service
        │     ├── Offers
        │     ├── Customer/KYC/KYB
        │     ├── Contracts
        │     └── Signature
        │
        ├── Identity Service
        │     ├── MobileIdentity
        │     ├── Numbers
        │     ├── SIM/eSIM
        │     └── Credentials
        │
        ├── Policy Engine
        │     ├── Limits
        │     ├── Approvals
        │     ├── Allowed channels
        │     └── Risk rules
        │
        ├── Telecom Provider Adapters
        │     ├── Operator partner
        │     ├── SMS gateway
        │     ├── WhatsApp BSP
        │     └── Voice/SIP provider
        │
        └── Audit & Billing
              ├── Logs
              ├── Costs
              ├── Transcripts
              └── Invoices
```

Los proveedores subyacentes son intercambiables. El agente consume AMI; el implementador decide qué operador, BSP o gateway ejecuta por debajo.

---

## 5. Casos de uso iniciales

- **Asistente empresarial con WhatsApp.** Una empresa contrata un número para su agente; el agente clasifica mensajes, redacta borradores y pide aprobación humana antes de enviar.
- **Agente de llamadas con transcripción.** Recibe llamadas, transcribe, resume, clasifica urgencia y transfiere a humano si detecta intención sensible.
- **Agente de confirmación de citas.** Envía recordatorios y confirma asistencia por SMS o WhatsApp con límites de frecuencia y opt-out.
- **Agente comercial.** Gestiona leads entrantes, responde información básica y agenda llamada con humano.
- **Identidad móvil temporal.** Contratación de números temporales para campañas, pruebas, eventos o agentes de proyecto.

---

## 6. Seguridad, compliance y gobernanza

### Principios

- Ningún agente debe operar sin identidad contractual asociada.
- Todo número debe tener propietario, contrato y política.
- Toda acción debe dejar log.
- Las acciones sensibles deben requerir aprobación humana.
- Los límites de gasto deben estar activos por defecto.
- El sistema debe poder suspender una identidad de forma inmediata.

### Controles mínimos

- KYC/KYB según tipo de cliente y país.
- Firma contractual.
- Consentimiento para grabaciones/transcripciones cuando aplique.
- Opt-in/opt-out en mensajería regulada.
- Auditoría de mensajes y llamadas.
- Retención configurable.
- Detección de abuso, rate limits, bloqueo de destinos premium o de riesgo.
- Tokens con permisos limitados.

### Cumplimiento legal

Cada implementador debe validar para su jurisdicción: normativa telecom local, registro de SIMs/eSIMs, uso de WhatsApp Business, GDPR, grabación de llamadas, responsabilidad del agente al contactar terceros, conservación de logs y transcripciones.

---

## 7. Definición técnica AMI v1

> AMI v1 es el protocolo mediante el cual un agente AI puede solicitar, contratar y recibir una identidad móvil legalmente activada, pasando por oferta, datos del cliente, contrato, firma y provisioning.

### 7.1 Alcance funcional

```text
Agente solicita SIM/número
→ AMI devuelve opciones/oferta
→ Agente/cliente acepta oferta
→ AMI solicita datos de cliente/titular
→ AMI genera contrato
→ Cliente firma
→ AMI solicita provisioning al partner telco
→ Partner confirma activación
→ AMI devuelve MobileIdentity activa
```

Quedan fuera del core inicial: iniciar/recibir llamadas, enviar/recibir SMS o WhatsApp, transcribir llamadas, operar contact center, automatizar conversaciones. Esas capacidades aparecerán como AMI Operations v2 o como módulos separados.

### 7.2 Comunicación agente → AMI

El agente se comunica con AMI mediante **MCP tools**. El MCP server traduce a HTTP/JSON contra la API REST de AMI.

```text
Agente AI
  ↓ MCP tool call
AMI MCP Server
  ↓ REST/JSON + Bearer
AMI Backend / API
  ↓ Adapter
Partner telco / firma / KYC
```

> MCP es la interfaz natural para agentes. REST/OpenAPI es la interfaz estable para sistemas, dashboards, partners y backend.

### 7.3 Tools MCP v1

```text
ami.search_sim_options        Lista países, SIM/eSIM y capacidades.
ami.request_sim_offer         Crea solicitud y devuelve oferta.
ami.accept_offer              Acepta la oferta antes de contrato.
ami.submit_customer_data      Envía datos legales/fiscales del cliente.
ami.create_contract           Genera contrato y enlace de firma.
ami.get_contract_status       Consulta estado del contrato.
ami.confirm_signature_status  Confirma firma vía callback o comprobación manual.
ami.activate_sim_identity     Inicia provisioning tras firma.
ami.get_identity_status       Consulta si la identidad está activa.
ami.cancel_request            Cancela el flujo antes de activación.
```

### 7.4 API REST v1

```text
POST /v1/sim-requests
GET  /v1/sim-requests/{id}
POST /v1/sim-requests/{id}/cancel
POST /v1/sim-requests/{id}/customer-data

GET  /v1/sim-options

POST /v1/offers/{id}/accept
GET  /v1/offers/{id}

POST /v1/customers
GET  /v1/customers/{id}

POST /v1/contracts
GET  /v1/contracts/{id}
POST /v1/contracts/{id}/mock-sign     # atajo programático

GET  /v1/sign/{id}                    # página HTML pública de firma
POST /v1/sign/{id}/confirm            # callback público del form de firma

POST /v1/mobile-identities/activate
GET  /v1/mobile-identities/{id}

GET  /v1/events
GET  /v1/health
```

Auth: `Authorization: Bearer <AMI_API_KEY>` en todos los endpoints excepto `GET /v1/health`, `GET /v1/sign/{id}` y `POST /v1/sign/{id}/confirm`.

OpenAPI 3.1 publicada en `/openapi.json`.

### 7.5 Entidades técnicas

```text
Agent · Customer · SIMRequest · SIMOption · Offer
Contract · Signature · MobileIdentity
ProvisioningStatus · AuditEvent
```

Ejemplo de `SIMRequest`:

```json
{
  "id": "simreq_123",
  "country": "ES",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "agent_id": "agent_demerzel",
  "customer_id": null,
  "status": "offer_created",
  "created_at": "2026-05-08T15:00:00Z"
}
```

Ejemplo de `MobileIdentity`:

```json
{
  "id": "mid_001",
  "status": "active",
  "phone_number": "+34600000000",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "agent_id": "agent_demerzel",
  "customer_id": "customer_789",
  "contract_id": "contract_456",
  "provider_activation_id": "act_999"
}
```

### 7.6 Motor de estados

El protocolo está gobernado por una máquina de estados explícita:

```text
requested
offer_created
offer_accepted
customer_data_submitted
signature_pending
signed
provisioning
active
─── terminales ───
rejected
cancelled
failed
```

Reglas:

- No se puede crear contrato sin oferta aceptada.
- No se puede activar identidad sin contrato firmado.
- No se puede entregar `MobileIdentity` activa sin confirmación del partner telco.
- Todo cambio de estado emite un `AuditEvent`.
- Los estados `failed`, `rejected` y `cancelled` deben tener motivo.

### 7.7 Ejemplo completo paso a paso

Caso: contratar una eSIM española para un agente "Agente01" con SMS y llamadas, máximo 10 €/mes.

**1. El agente llama al MCP**

```json
ami.request_sim_offer({
  "country": "ES",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "agent_name": "Agente01",
  "max_monthly_price": 10,
  "currency": "EUR"
})
```

**2. El MCP server llama a la API**

```http
POST /v1/sim-requests
Authorization: Bearer ***
Content-Type: application/json

{
  "country": "ES",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "agent": {"name": "Agente01", "purpose": "agent_identity"},
  "commercial_constraints": {"max_monthly_price": 10, "currency": "EUR"}
}
```

**3. AMI consulta disponibilidad al partner telco y devuelve oferta**

```json
{
  "offer_id": "offer_123",
  "status": "offer_created",
  "monthly_price": 8.90,
  "setup_fee": 5.00,
  "currency": "EUR",
  "requires_contract": true,
  "requires_customer_data": true,
  "expires_at": "2026-05-15T23:59:59Z"
}
```

**4. El agente acepta la oferta**

```json
ami.accept_offer({"offer_id": "offer_123"})
```

**5. El agente envía datos del cliente**

```json
ami.submit_customer_data({
  "sim_request_id": "simreq_xxx",
  "customer": {
    "legal_name": "Acme S.L.",
    "tax_id": "B00000000",
    "billing_email": "admin@acme.com",
    "address": "Madrid, España",
    "representative_name": "..."
  }
})
```

**6. AMI genera contrato**

```json
{
  "contract_id": "contract_456",
  "status": "signature_pending",
  "signature_url": "https://api.example.com/v1/sign/contract_456"
}
```

**7. El firmante abre la URL en el navegador y firma. Webhook devuelve `signed`.**

**8. AMI activa la eSIM con el partner telco**

```json
telco.provision_esim({
  "provider_offer_id": "telco_001",
  "customer_id": "customer_789",
  "contract_id": "contract_456"
})
```

**9. AMI devuelve la identidad activa**

```json
{
  "status": "active",
  "mobile_identity_id": "mid_001",
  "phone_number": "+34600000000",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "contract_id": "contract_456"
}
```

### 7.8 Stack de referencia

```text
Backend:       Python (stdlib en la implementación de referencia) o Node/FastAPI
Base de datos: PostgreSQL en producción (memoria en la referencia)
API spec:      OpenAPI 3.1
Agent layer:   MCP Server (transporte stdio + streamable-http)
Firma:         Signaturit, DocuSign o equivalente (página HTML propia en la referencia)
Auth:          API keys por cliente + tokens por integración
```

### 7.9 Principio de diseño

> AMI no es inicialmente el operador de llamadas. AMI es el protocolo de contratación, activación y entrega de identidad móvil para agentes.

Mantener este enfoque facilita el MVP, evita mezclar el alcance v1 con la complejidad de operación telefónica, y permite que cualquier implementador enchufe operadores reales detrás del adapter sin tocar la API pública.
