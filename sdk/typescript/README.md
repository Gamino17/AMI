# @ami-protocol/sdk

Official TypeScript SDK for the **AMI protocol** — the open protocol that lets
AI agents contract, provision and operate a mobile identity (SIM, eSIM,
phone number).

- Full coverage of the AMI HTTP API: contracting, identity admin, SMS, voice,
  limits, usage, webhooks.
- Zero runtime dependencies. Uses the global `fetch` (Node 18+, every modern
  runtime).
- Fully typed. camelCase everywhere on the surface — the SDK converts to and
  from the snake_case wire format for you.
- Typed error hierarchy: `AmiAuthError`, `AmiRateLimitError`,
  `AmiNotFoundError`, `AmiValidationError`, `AmiConflictError`,
  `AmiServerError`, `AmiTransportError`.
- HMAC-SHA256 webhook verifier in `@ami-protocol/sdk/webhooks`.

## Install

```bash
npm install @ami-protocol/sdk
```

Requires Node 18 or newer. Works under Bun and Deno (via Node-compat).

## Quickstart — provision a number end to end

```ts
import { AmiClient } from "@ami-protocol/sdk";

const client = new AmiClient({
  apiKey: process.env.AMI_API_KEY!,
  baseUrl: "https://api.protocolami.com",
});

const creds = await client.provisionNumber({
  country: "ES",
  capabilities: ["sms", "voice"],
  agentName: "support-bot",
  customer: {
    legalName: "Acme S.L.",
    taxId: "B12345678",
    billingEmail: "billing@acme.test",
    address: "Madrid, Spain",
    representativeName: "Ada Lovelace",
  },
});

console.log("MID:", creds.mid);
console.log("Phone number:", creds.phoneNumber);
console.log("Agent token:", creds.agentToken); // store it, returned once
```

## Step-by-step contracting

```ts
const { simRequest, offer } = await client.requestNumber({
  country: "ES",
  capabilities: ["sms", "voice"],
  agentName: "agent01",
});

await client.acceptOffer(offer.id);

const { customer } = await client.submitCustomerData(simRequest.id, {
  legalName: "Acme S.L.",
  taxId: "B12345678",
  billingEmail: "billing@acme.test",
  address: "Madrid, Spain",
  representativeName: "Ada Lovelace",
  representativePhone: "+34600111222", // optional; enables SMS delivery of the KYC link
});

// Trigger human KYC of the legal representative. AMI emails (and SMS, if
// representativePhone was provided) the verification link. Idempotent.
const kyc = await client.initiateKyc(simRequest.id);
console.log("Send the rep to:", kyc.verificationUrl);

const contract = await client.createContract({
  offerId: offer.id,
  customerId: customer.id,
});

// In production the signer opens contract.signatureUrl in a browser.
// For programmatic flows you can shortcut:
await client.mockSign(contract.id);

const identity = await client.activateIdentity(contract.id);
// identity.agentToken is returned ONLY here. Persist it now.
```

## Admin — limits, inbound SIP, webhooks, token rotation

```ts
await client.setInboundSipUri(identity.id, "sip:engine@host.example");

await client.updateLimits(identity.id, {
  smsPerDay: 1000,
  monthlyBudgetEur: 50,
  allowedCountryPrefixes: ["+34", "+33"],
});

const wh = await client.createWebhook(identity.id, {
  url: "https://acme.test/ami-webhook",
  events: ["sms.inbound", "call.inbound", "call.completed"],
});
// wh.secret is returned ONLY here.

const rotated = await client.rotateAgentToken(identity.id);
// rotated.agentToken is the new token; the previous one is dead.
```

## SMS and voice — `AmiAgent`

The contracting flow returns the scoped `agentToken`. Use it with
`AmiAgent`:

```ts
import { AmiAgent } from "@ami-protocol/sdk";

const agent = new AmiAgent({
  agentToken: process.env.AMI_AGENT_TOKEN!,
  baseUrl: "https://api.protocolami.com",
});

const me = await agent.self();
console.log("Operating", me.phoneNumber);

const sms = await agent.sendSms({ to: "+34600111222", body: "Hello" });
const history = await agent.listSms({ limit: 20, direction: "outbound" });

const call = await agent.placeCall({
  to: "+34600111222",
  callbackSipUri: "sip:engine@host.example",
});
await agent.hangupCall(call.id);

const usage = await agent.usage();
console.log("Spent this month:", usage.usage.spendThisMonthEur, "EUR");
```

Or get an agent directly from a client (reuses base URL):

```ts
const agent = client.asAgent(creds.agentToken);
```

## Webhook verification

Every webhook delivery is signed with `HMAC-SHA256(secret, rawBody)` in the
`X-Ami-Signature: sha256=<hex>` header. Verify it before trusting the payload.

```ts
import { verifySignature } from "@ami-protocol/sdk/webhooks";

// Express example. `rawBody` MUST be the bytes AMI sent, not a parsed JSON.
app.post("/ami-webhook", (req, res) => {
  const ok = verifySignature({
    secret: process.env.AMI_WEBHOOK_SECRET!,
    body: req.rawBody,
    signatureHeader: req.headers["x-ami-signature"],
  });
  if (!ok) return res.status(401).end();

  const event = JSON.parse(req.rawBody);
  // event.event === "sms.inbound" | "call.completed" | ...
  // event.mid    === "mid_..."
  // event.data   === { ... }
  res.sendStatus(200);
});
```

## Errors

Every HTTP error is mapped to a specific subclass. Catch what you care about:

```ts
import {
  AmiAuthError,
  AmiRateLimitError,
  AmiNotFoundError,
  AmiValidationError,
  AmiConflictError,
  AmiServerError,
  AmiTransportError,
} from "@ami-protocol/sdk";

try {
  await agent.sendSms({ to: "+34600", body: "hi" });
} catch (err) {
  if (err instanceof AmiRateLimitError) {
    // err.reason === "sms_hourly_limit_exceeded"
    //              | "sms_daily_limit_exceeded"
    //              | "monthly_budget_exceeded"
    //              | "country_not_allowed"
    //              | ...
  } else if (err instanceof AmiAuthError) {
    // rotate the token
  } else if (err instanceof AmiServerError) {
    // retry after err.retryAfterSec ?? small backoff
  } else if (err instanceof AmiTransportError) {
    // network issue
  } else {
    throw err;
  }
}
```

## Cancellation and timeouts

Every operation accepts an optional `AbortSignal`. The SDK also enforces a
default 30s per-request timeout (configurable via `timeoutMs`):

```ts
const ctrl = new AbortController();
setTimeout(() => ctrl.abort(), 5_000);

await agent.placeCall(
  { to: "+34600", callbackSipUri: "sip:engine@host.example" },
  ctrl.signal,
);
```

## Configuration

| Option        | Default                          | Notes                                              |
| ------------- | -------------------------------- | -------------------------------------------------- |
| `baseUrl`     | `https://api.protocolami.com`    | Override for self-hosted or staging deployments.   |
| `timeoutMs`   | `30000`                          | Set to `0` to disable. AbortSignal still works.    |
| `maxRetries`  | `2`                              | Applied to idempotent GETs and to 5xx responses.   |
| `fetch`       | global `fetch`                   | Inject a custom implementation for tests or edge.  |
| `userAgent`   | _none_                           | Suffix appended to `@ami-protocol/sdk-ts/<ver>`.   |

## License

Apache-2.0
