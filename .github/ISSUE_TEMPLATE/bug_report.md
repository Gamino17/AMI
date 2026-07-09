---
name: Bug report
about: Report something in AMI that isn't working as documented
title: "[bug] "
labels: bug
assignees: ''
---

## Description

A clear and concise description of the bug.

## Affected surface

- [ ] REST v1 API endpoint
- [ ] MCP tool (`ami.*`)
- [ ] Provisioning / contract / signature flow
- [ ] KYC
- [ ] SMS
- [ ] Voice (call origination / SIP bridge / inbound config)
- [ ] Governance (limits / budget / usage)
- [ ] Webhooks
- [ ] Auth / tokens (customer key, `agent_token`, admin)
- [ ] Docs / landing / OpenAPI
- [ ] Other

Which endpoint or tool? (e.g. `POST /v1/contracts`, `ami.place_call`)

## Steps to reproduce

1.
2.
3.

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened. Include error messages and, if relevant, the audit event.

> Please redact API keys, `agent_token`s, webhook secrets, and any real personal/KYC data.

## Environment

- Where: [ ] live (`https://protocolami.com`) / [ ] local
- Access path: [ ] REST / [ ] MCP stdio / [ ] MCP streamable-http
- Python version (if local): `3.11` / `3.12`
- AMI commit / date:

## Additional context

Request/response captures, logs, or anything else that helps. (Redact secrets.)

> If this is a **security vulnerability**, do not file it here — email security@parallaxiei.com. See [SECURITY.md](../../SECURITY.md).
