---
name: Feature request
about: Propose a capability or improvement for the AMI protocol
title: "[feature] "
labels: enhancement
assignees: ''
---

## Problem / use case

What can an agent not do today that it should be able to? Describe the concrete scenario.

## Proposed solution

What you'd like AMI to do. Be specific about the surface it would live on:

- [ ] New or changed REST v1 endpoint
- [ ] New or changed MCP tool (`ami.*`)
- [ ] Provisioning / KYC / contract flow
- [ ] SMS
- [ ] Voice / SIP bridge
- [ ] Governance (limits / budget / country prefixes / usage)
- [ ] Webhooks
- [ ] Auth / identity / tokens
- [ ] Docs / SDK / examples
- [ ] Other

## How it fits the protocol

AMI adds verifiable identity and fine-grained permissions on top of MCP and A2A. Explain how this proposal fits that model and respects the provisioning state machine (`SIMRequest → Offer → Customer → Contract → Signature → MobileIdentity`).

## Alternatives considered

Any other approaches you weighed, and why the proposed one is better.

## Constraints to keep in mind

- The backend is **Python standard library only** — no new runtime dependencies without a strong justification.
- The public surface (REST v1 + the 28 `ami.*` tools) is a contract: prefer additive changes.
- No third-party brand names in public surfaces.

## Additional context

Anything else — sketches, payload shapes, links.
