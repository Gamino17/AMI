# AMI — Agent Mobile Identity Protocol · Technical Spec

**Version:** 1.0  
**Status:** reference (the implementation in this repo is the reference implementation)  
**License:** MIT (see `LICENSE` in the repo)

AMI is an open protocol that standardizes how an AI agent **requests, contracts and activates a mobile identity** (SIM, eSIM or phone number). It defines entities, a state machine, REST endpoints and MCP tools so an agent can walk the flow `request → offer → customer data → contract → signature → active MobileIdentity` without going through processes designed for humans.

AMI v1 covers **contracting and provisioning**. Post-activation operation (calls, SMS, WhatsApp, data, OTP) is out of scope for v1 — it may appear as AMI Operations v2 or as separate modules.

---

## Table of contents

1. [Main contracting flow](#1-main-contracting-flow)
2. [Central entity: MobileIdentity](#2-central-entity-mobileidentity)
3. [API and Protocol](#3-api-and-protocol)
4. [Conceptual architecture](#4-conceptual-architecture)
5. [Initial use cases](#5-initial-use-cases)
6. [Security, compliance and governance](#6-security-compliance-and-governance)
7. [AMI v1 technical definition](#7-ami-v1-technical-definition)
   - 7.1 Functional scope
   - 7.2 Agent → AMI communication
   - 7.3 MCP tools v1
   - 7.4 REST API v1
   - 7.5 Technical entities
   - 7.6 State machine
   - 7.7 Full step-by-step example
   - 7.8 Reference stack
   - 7.9 Design principle

---

## 1. Main contracting flow

### Step 1 — The agent requests a number

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

### Step 2 — AMI returns an offer

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

### Step 3 — Customer submits data

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

### Step 4 — AMI issues a contract and signing URL

```json
{
  "contract_id": "contract_789",
  "signature_url": "https://.../v1/sign/contract_789",
  "status": "signature_pending",
  "expires_at": "2026-05-15T23:59:59Z"
}
```

### Step 5 — After signature, the mobile identity is activated

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

## 2. Central entity: MobileIdentity

`MobileIdentity` represents a mobile identity contracted, governed and operable by agents.

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
    "agent_id": "agent01",
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

## 3. API and Protocol

AMI exposes three interfaces:

1. **REST API** for traditional integrations.
2. **MCP Server** (Model Context Protocol) for agents.
3. **SDK / CLI** for developers and testing.

### 3.1 Contracting methods (v1)

Auth: customer `AMI_API_KEY` (Level 1).

- `ami.search_sim_options`
- `ami.request_sim_offer`
- `ami.accept_offer`
- `ami.submit_customer_data`
- `ami.create_contract`
- `ami.get_contract_status`
- `ami.confirm_signature_status`
- `ami.activate_sim_identity` — returns the `agent_token` scoped to the MID, **once only**
- `ami.get_identity_status`
- `ami.cancel_request`
- `ami.rotate_agent_token` — hard rotate: invalidates the previous token immediately

### 3.2 Operations methods (Operations v2)

Auth: `agent_token` scoped to the MID (Level 2).

**SMS:**
- `ami.send_sms(to, body)` — enqueues; transitions `queued → sent → delivered`
- `ami.list_sms(limit, direction)` — history scoped to the MID

**Voice (bridge-by-API):**
- `ami.place_call(to, callback_sip_uri)` — originates the call to the PSTN and
  bridges it over SIP to the customer endpoint (their real-time voice engine
  SIP URI, their own PBX, or any destination that speaks SIP). AMI carries
  the pipe; the voice "brain" lives at the customer endpoint.
- `ami.list_calls(limit, direction)`, `ami.get_call(call_id)`
- `ami.hangup_call(call_id)`
- `ami.set_inbound_sip_uri(mid, sip_uri)` — Level 1 auth; configures where to
  forward inbound calls for the MID (typically the same real-time customer
  endpoint). Without this configured, inbound calls are rejected.

**Audit:**
- `ami.list_events`

### 3.3 Governance methods

Auth: customer `AMI_API_KEY` (Level 1).

**Rate limits + spending:**
- `ami.get_limits(mid)` — returns `sms_per_hour`, `sms_per_day`, `calls_per_hour`,
  `calls_per_day`, `monthly_budget_eur`, `allowed_country_prefixes`.
- `ami.update_limits(mid, ...)` — partial patch. Reasonable defaults applied when the MID is activated.
- `ami.get_usage(mid)` — current counters + percentage consumed + current rates.
- `ami.get_my_usage(agent_token)` — alias scoped by agent_token (Level 2).
- Automatic enforcement: `send_sms` and `place_call` return 429 with the
  specific reason (`sms_hourly_limit_exceeded`, `country_not_allowed`,
  `monthly_budget_exceeded`, etc.).

**Outbound webhooks:**
- `ami.create_webhook(mid, url, events)` — the customer registers a URL that
  AMI will call with MID events (`sms.inbound`, `sms.delivered`, `sms.failed`,
  `call.inbound`, `call.completed`, `call.failed`, or `*`). Returns a secret
  **once only**, used to verify the `X-Ami-Signature: sha256=…` header.
- `ami.list_webhooks(mid)`, `ami.delete_webhook(mid, webhook_id)`.
- Delivery: up to 3 retries with backoff [0.5, 2, 8]s. After 10 consecutive
  failures, the webhook auto-disables.
- Client pattern: the receiver recomputes `HMAC-SHA256(secret, raw_body)` and
  compares it against the header; if it doesn't match, ignore.

**Pending for v2.x:**
- `ami.suspend_identity`, `ami.release_number`, `ami.audit_log`.

### 3.4 Voice model: AMI = operator, NOT Realtime

Three distinct pieces in an agent call:

1. **Operator / SIP provider** — the piece AMI occupies. Buys/assigns numbers,
   carries calls to the PSTN, offers SIP-out to the customer endpoint.
2. **Customer / agent backend** — decides whether to accept the call, with
   what prompt, which tools it has, logs, permissions, limits.
3. **Real-time voice engine** — sends/receives live audio and runs the voice
   agent with low latency.

AMI occupies **only piece 1**. Pieces 2 and 3 belong to the customer. This
keeps the analogy with any classic operator: the operator does not record or
transcribe the call, it only carries it. The cost of the real-time engine is
on the customer's account; AMI bills for the number plus minutes carried.

Typical outbound pattern:

```
agent → tool.phone.call → customer.backend
                            ↓ POST /v1/agent/calls/place {to, callback_sip_uri}
                          AMI (operator)
                            ↓ dials the PSTN
                            ↓ bridges SIP to the callback_sip_uri
                          customer endpoint (Realtime / PBX)
```

Inbound pattern:

```
telco network → AMI (receives the call)
                  ↓ resolves the MID's inbound_sip_uri
                  ↓ SIP-forward
                customer endpoint
```

---

## 4. Conceptual architecture

```text
AI Agent / Company
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

The underlying providers are interchangeable. The agent consumes AMI; the implementer decides which operator, BSP or gateway runs underneath.

---

## 5. Initial use cases

- **Business assistant with WhatsApp.** A company contracts a number for its agent; the agent classifies messages, drafts replies and requests human approval before sending.
- **Call agent with transcription.** Takes calls, transcribes, summarizes, classifies urgency and hands off to a human if it detects sensitive intent.
- **Appointment confirmation agent.** Sends reminders and confirms attendance over SMS or WhatsApp with frequency limits and opt-out.
- **Sales agent.** Handles inbound leads, answers basic questions and schedules a call with a human.
- **Temporary mobile identity.** Provisioning of temporary numbers for campaigns, tests, events or project-scoped agents.

---

## 6. Security, compliance and governance

### Principles

- No agent should operate without an associated contractual identity.
- Every number must have an owner, contract and policy.
- Every action must produce a log.
- Sensitive actions must require human approval.
- Spending limits must be active by default.
- The system must be able to suspend an identity immediately.

### Minimum controls

- KYC/KYB depending on customer type and country.
- Contract signature.
- Consent for recordings/transcriptions where applicable.
- Opt-in/opt-out on regulated messaging.
- Audit of messages and calls.
- Configurable retention.
- Abuse detection, rate limits, blocking of premium or risky destinations.
- Tokens with limited permissions.

### Legal compliance

Each implementer must validate for its jurisdiction: local telecom regulation, SIM/eSIM registration, WhatsApp Business usage, GDPR, call recording, the agent's responsibility when contacting third parties, retention of logs and transcripts.

---

## 7. AMI v1 technical definition

> AMI v1 is the protocol by which an AI agent can request, contract and receive a legally activated mobile identity, going through offer, customer data, contract, signature and provisioning.

### 7.1 Functional scope

```text
Agent requests SIM/number
→ AMI returns options/offer
→ Agent/customer accepts offer
→ AMI requests customer/holder data
→ AMI issues a contract
→ Customer signs
→ AMI requests provisioning from the telco partner
→ Partner confirms activation
→ AMI returns an active MobileIdentity
```

Out of scope for the initial core: placing/receiving calls, sending/receiving SMS or WhatsApp, transcribing calls, running a contact center, automating conversations. Those capabilities will appear as AMI Operations v2 or as separate modules.

### 7.2 Agent → AMI communication

The agent communicates with AMI through **MCP tools**. The MCP server translates to HTTP/JSON against the AMI REST API.

```text
AI Agent
  ↓ MCP tool call
AMI MCP Server
  ↓ REST/JSON + Bearer
AMI Backend / API
  ↓ Adapter
Telco partner / signature / KYC
```

> MCP is the natural interface for agents. REST/OpenAPI is the stable interface for systems, dashboards, partners and backend.

### 7.3 MCP tools v1

```text
ami.search_sim_options        Lists countries, SIM/eSIM and capabilities.
ami.request_sim_offer         Creates a request and returns an offer.
ami.accept_offer              Accepts the offer before contract.
ami.submit_customer_data      Submits the customer's legal/tax data.
ami.create_contract           Issues the contract and signing link.
ami.get_contract_status       Queries the contract status.
ami.confirm_signature_status  Confirms signature via callback or manual check.
ami.activate_sim_identity     Starts provisioning after signature.
ami.get_identity_status       Checks whether the identity is active.
ami.cancel_request            Cancels the flow before activation.
```

### 7.4 REST API v1

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
POST /v1/contracts/{id}/mock-sign     # programmatic shortcut

GET  /v1/sign/{id}                    # public HTML signing page
POST /v1/sign/{id}/confirm            # public callback from the signing form

POST /v1/mobile-identities/activate
GET  /v1/mobile-identities/{id}

GET  /v1/events
GET  /v1/health
```

Auth: `Authorization: Bearer <AMI_API_KEY>` on all endpoints except `GET /v1/health`, `GET /v1/sign/{id}` and `POST /v1/sign/{id}/confirm`.

OpenAPI 3.1 published at `/openapi.json`.

### 7.5 Technical entities

```text
Agent · Customer · SIMRequest · SIMOption · Offer
Contract · Signature · MobileIdentity
ProvisioningStatus · AuditEvent
```

Example `SIMRequest`:

```json
{
  "id": "simreq_123",
  "country": "ES",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "agent_id": "agent01",
  "customer_id": null,
  "status": "offer_created",
  "created_at": "2026-05-08T15:00:00Z"
}
```

Example `MobileIdentity`:

```json
{
  "id": "mid_001",
  "status": "active",
  "phone_number": "+34600000000",
  "sim_type": "eSIM",
  "capabilities": ["sms", "voice"],
  "agent_id": "agent01",
  "customer_id": "customer_789",
  "contract_id": "contract_456",
  "provider_activation_id": "act_999"
}
```

### 7.6 State machine

The protocol is governed by an explicit state machine:

```text
requested
offer_created
offer_accepted
customer_data_submitted
signature_pending
signed
provisioning
active
─── terminal ───
rejected
cancelled
failed
```

Rules:

- A contract cannot be created without an accepted offer.
- An identity cannot be activated without a signed contract.
- An active `MobileIdentity` cannot be returned without confirmation from the telco partner.
- Every state change emits an `AuditEvent`.
- The `failed`, `rejected` and `cancelled` states must include a reason.

### 7.7 Full step-by-step example

Case: contract a Spanish eSIM for an "Agente01" agent with SMS and voice, capped at €10/month.

**1. The agent calls MCP**

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

**2. The MCP server calls the API**

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

**3. AMI queries availability from the telco partner and returns an offer**

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

**4. The agent accepts the offer**

```json
ami.accept_offer({"offer_id": "offer_123"})
```

**5. The agent submits customer data**

```json
ami.submit_customer_data({
  "sim_request_id": "simreq_xxx",
  "customer": {
    "legal_name": "Acme S.L.",
    "tax_id": "B00000000",
    "billing_email": "admin@acme.com",
    "address": "Madrid, Spain",
    "representative_name": "..."
  }
})
```

**6. AMI issues a contract**

```json
{
  "contract_id": "contract_456",
  "status": "signature_pending",
  "signature_url": "https://api.example.com/v1/sign/contract_456"
}
```

**7. The signer opens the URL in a browser and signs. A webhook returns `signed`.**

**8. AMI activates the eSIM with the telco partner**

```json
telco.provision_esim({
  "provider_offer_id": "telco_001",
  "customer_id": "customer_789",
  "contract_id": "contract_456"
})
```

**9. AMI returns the active identity**

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

### 7.8 Reference stack

```text
Backend:       Python (stdlib in the reference implementation) or Node/FastAPI
Database:      PostgreSQL in production (in-memory in the reference)
API spec:      OpenAPI 3.1
Agent layer:   MCP Server (stdio + streamable-http transports)
Signature:     Electronic signature provider (self-hosted HTML page in the reference)
Auth:          API keys per customer + tokens per integration
```

### 7.9 Design principle

> AMI is not initially the call operator. AMI is the protocol for contracting, activating and delivering mobile identity for agents.

Keeping this focus makes the MVP easier, avoids mixing v1 scope with the complexity of telephone operations, and lets any implementer plug real operators behind the adapter without touching the public API.
