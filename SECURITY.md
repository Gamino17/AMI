# Security Policy

AMI is a protocol for provisioning and operating **real mobile identities** — phone numbers, voice, SMS and data — on behalf of AI agents, with real KYC on the legal representative before any contract exists. Because of that, we take security reports seriously and ask you to report responsibly.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, email:

**security@parallaxiei.com**

Include as much of the following as you can:

- A clear description of the vulnerability and its impact.
- The affected surface (REST v1 endpoint, an `ami.*` MCP tool, the signed webhook delivery, the signature page, KYC flow, auth/token handling, etc.).
- Steps to reproduce, or a proof of concept.
- Any relevant request/response captures (please redact real credentials, API keys, `agent_token`s and personal data).

We will acknowledge your report, investigate, keep you updated on remediation, and credit you if you'd like once a fix has shipped. Please give us a reasonable window to remediate before any public disclosure.

## Scope

In scope:

- The REST v1 API and the 28 `ami.*` MCP tools.
- Authentication and identity: customer API key (Bearer), scoped `agent_token` (Level 2, hard-rotate), admin key, and multi-tenant isolation.
- The KYC verification gate and the web signature flow.
- Signed outbound webhook delivery.
- Governance controls (rate limits, monthly budget, allowed country prefixes).

Out of scope:

- The simulated physical SIM piece pending a telco partner.
- Denial-of-service via raw volume, and reports from automated scanners without a demonstrated, exploitable impact.

## Security properties worth knowing

These are part of how AMI is built and are relevant when assessing or reporting:

- **Signed webhooks.** Every outbound webhook is signed with `X-AMI-Signature`, an **HMAC-SHA256** over the payload using a per-webhook secret. The secret is returned exactly once, at registration time. Consumers must verify the signature before trusting a delivery.
- **KYC gate.** The legal representative's identity is verified (document + selfie on a public verification page) and must reach `verified` before a contract can be created. This is a hard gate, not an afterthought.
- **Two-tier auth.** A customer API key (Bearer) owns the account; a scoped `agent_token` (Level 2) grants an agent operational access to a single mobile identity and can be **hard-rotated** — invalidating the previous token instantly. Admin operations require a separate admin key.
- **Audit trail.** Sensitive operations emit audit events for inspection.

## Handling credentials in reports

Never paste live API keys, agent tokens, webhook secrets, or real personal/KYC data into a report or a public channel. Redact them. If you believe a credential has been exposed, say so in your email so we can rotate it immediately.

## Supported versions

AMI is a continuously deployed protocol; security fixes land on `main` and are deployed to the live surfaces at `https://protocolami.com`. There is no separate maintenance branch — please test against the current `main` / the live endpoints.
