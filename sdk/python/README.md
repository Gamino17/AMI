# ami-protocol — Python SDK

Official Python SDK for the **AMI protocol** (Agent Mobile Identity). AMI is
an open protocol that standardises how an AI agent requests, contracts,
activates and operates a mobile identity — SIM, eSIM, or phone number — with
its own contract, owner, policy and audit trail.

This package gives you:

- `AmiClient` — the customer-facing client. Walks the full contracting flow
  (request a number, accept the offer, submit customer data, contract,
  sign, activate) and manages webhooks, limits and inbound SIP routing.
- `AmiAgent` — the operations-plane client. Authenticated with the per-MID
  `agent_token` returned at activation. Sends SMS, originates calls
  (bridge-by-API to your own SIP endpoint), reads usage.
- `ami.webhooks.verify_signature` — HMAC-SHA256 verification of the
  `X-Ami-Signature` header for outbound webhooks.
- Typed exceptions: `AmiAuthError`, `AmiNotFound`, `AmiConflictError`,
  `AmiValidationError`, `AmiRateLimitError`, `AmiServerError`,
  `AmiTransportError`.

## Install

```bash
pip install ami-protocol
```

The only runtime dependency is `requests`. Python 3.9+ is supported.

## The two auth planes

AMI has two distinct credentials:

| Plane | Credential | Header | Scope | Use it for |
|------|------------|--------|-------|------------|
| Level 1 | `AMI_API_KEY` | `Authorization: Bearer <key>` | Customer account | Contracting + admin (limits, webhooks, rotation) |
| Level 2 | `agent_token` (`amiagt_live_...`) | `Authorization: Bearer <token>` | One MobileIdentity (MID) | Send SMS / place calls scoped to that MID |

The `agent_token` is **returned once** at activation. Persist it in your
secret store the moment you receive it. If it leaks, rotate it with
`AmiClient.rotate_agent_token(mid)`.

## End-to-end example

```python
from ami import AmiClient

client = AmiClient(
    api_key="ami_live_...",
    base_url="https://api.protocolami.com",
)

# 1) Provision a new number in one shot.
creds = client.provision_number(
    country="ES",
    customer={
        "legal_name": "Acme S.L.",
        "tax_id": "B12345678",
        "billing_email": "billing@acme.test",
        "address": "Madrid, Spain",
        "representative_name": "Ada Lovelace",
    },
)
print(creds.phone_number, creds.mid)

# Persist creds.agent_token in your vault. You will not see it again.

# 2) Configure the MID before operating it.
client.set_inbound_sip_uri(creds.mid, "sip:agent@host.example;transport=tls")
client.update_limits(creds.mid, sms_per_day=1000, monthly_budget_eur=50)

# 3) Subscribe to outbound events. The secret is shown once.
hook = client.create_webhook(
    creds.mid,
    url="https://acme.example/ami/hook",
    events=["sms.inbound", "sms.delivered", "call.completed"],
)
print("webhook secret:", hook.secret)  # store it in your vault

# 4) Operate the MID with the agent-scoped client.
agent = client.as_agent(creds.agent_token)

msg = agent.send_sms(to="+34600111222", body="hello from my agent")
print(msg.id, msg.status)              # "msg_...", "queued"

call = agent.place_call(
    to="+34600999888",
    callback_sip_uri="sip:agent@host.example;transport=tls",
)
print(call.id, call.status)            # "call_...", "initiated"

snapshot = agent.usage()
print(snapshot.usage["sms_count_today"], "/", snapshot.limits["sms_per_day"])
```

## Step-by-step contracting flow

`provision_number` is a convenience wrapper. If you need full control over
each transition (typically because a human signs the contract), drive the
flow step by step:

```python
offer = client.request_number(
    country="ES",
    capabilities=["sms", "voice"],
    agent_name="agent01",
)

client.accept_offer(offer.id)
cust = client.submit_customer_data(
    sim_request_id=offer.sim_request_id,
    legal_name="Acme S.L.",
    tax_id="B12345678",
    billing_email="billing@acme.test",
    address="Madrid, Spain",
    representative_name="Ada Lovelace",
)
contract = client.create_contract(offer_id=offer.id, customer_id=cust.id)

# Send contract.signature_url to the human. When the page is submitted,
# the contract becomes "signed".  In tests, sign programmatically:
client.mock_sign(contract.id)

identity = client.activate_identity(contract.id)
assert identity.agent_token is not None  # store it now or never
```

## Webhooks

Register an outbound webhook to learn about events on the MID (`sms.inbound`,
`sms.delivered`, `sms.failed`, `call.inbound`, `call.completed`,
`call.failed`). Each delivery carries an `X-Ami-Signature: sha256=<hex>`
header. Verify it on receipt:

```python
from ami.webhooks import verify_signature

def handle(request):
    body = request.body  # raw bytes — do NOT re-serialize
    sig = request.headers["X-Ami-Signature"]
    if not verify_signature(secret=WEBHOOK_SECRET, body=body, signature_header=sig):
        return 401, "bad signature"
    payload = json.loads(body)
    # payload = {"event": "sms.delivered", "mid": "...", "data": {...}}
    ...
```

## Errors

Every method raises a subclass of `AmiError` on a non-2xx response:

```python
from ami import AmiRateLimitError, AmiAuthError

try:
    agent.send_sms(to="+1...", body="hi")
except AmiRateLimitError as e:
    # e.reason is the machine-readable code, e.g.
    # "sms_hourly_limit_exceeded" / "country_not_allowed" /
    # "monthly_budget_exceeded".
    print("blocked by policy:", e.reason)
except AmiAuthError:
    # agent_token revoked or wrong; rotate and re-load it.
    ...
```

Transient `5xx` responses on `GET` requests are retried internally with
exponential backoff. `POST` requests are never retried by the SDK to avoid
double side-effects (sending an SMS twice, creating two contracts).

## License

Apache-2.0. See `LICENSE`.
