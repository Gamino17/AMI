## Summary

<!-- What does this PR change, and why? One or two sentences. -->

## Affected surface

- [ ] REST v1 API
- [ ] MCP tools (`ami.*`)
- [ ] Provisioning / contract / signature flow
- [ ] KYC
- [ ] SMS
- [ ] Voice / SIP bridge
- [ ] Governance (limits / budget / usage)
- [ ] Webhooks
- [ ] Auth / identity / tokens
- [ ] Docs / landing / OpenAPI / SDK / examples
- [ ] Internal only (no public surface change)

## Details

<!-- What changed, and any design decisions worth calling out. -->

## How I tested

<!-- Commands run, scenarios exercised. Paste relevant output. -->

```bash
pytest -q --tb=short
```

## Checklist

- [ ] `pytest -q --tb=short` passes locally
- [ ] Backend changes use the **Python standard library only** (no new runtime deps)
- [ ] Added/updated tests for the change
- [ ] Updated docs / catalogs if a public surface (endpoint, MCP tool, landing catalog) changed
- [ ] No third-party brand names in public surfaces (the `brand-check` CI job stays green)
- [ ] The provisioning state machine and audit events are respected
- [ ] Public REST/MCP surface changes are additive, or a breaking change was discussed in an issue first

## Related issues

<!-- e.g. Closes #123 -->

---

By submitting this PR I agree my contribution is licensed under the Apache License 2.0.
