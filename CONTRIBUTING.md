# Contributing to AMI

Thanks for your interest in AMI — the **Agent Mobile Identity Protocol**. AMI lets an AI agent request, contract (with real KYC), activate and operate a real mobile identity — phone number, voice, SMS, data — over MCP (28 `ami.*` tools) and a REST v1 API.

This guide covers local setup, running the test suite, the code style we hold to, and how to open a pull request.

Live surfaces you can point at while developing:

- API + landing: `https://protocolami.com`
- Remote MCP (streamable-http): `https://mcp.protocolami.com/mcp/` (the trailing slash matters — without it the server answers `307`)
- Health: `https://protocolami.com/v1/health`
- OpenAPI: `https://protocolami.com/openapi.json`
- `llms.txt`: `https://protocolami.com/llms.txt`

---

## Ground rules

- **The backend is Python standard library only.** No web framework, no ORM. This is a hard design constraint, not an accident. New backend code MUST NOT introduce runtime dependencies beyond what the SDKs and tests already need (`mcp`, `httpx`, `pytest`). If you think you need a new dependency, open an issue first and explain why stdlib can't do it.
- **No third-party brand names in public surfaces.** AMI is described by *what it is*, never by comparison to other products. A CI job (`brand-check`) greps public files and fails the build on prohibited mentions. Keep `README.md`, `docs/`, and the backend clean.
- **No invented features.** Everything in code, docs and the landing must map to a real capability that ships. If a capability is not implemented, don't document it as if it were.
- **English for public files** (README, docs, this file, issue/PR templates).

---

## Local setup (venv)

The backend runs on Python 3.11 or 3.12 (this is the CI matrix). It is stdlib-only, so the only things you install are the MCP SDK, `httpx`, and `pytest`.

```bash
# from the repo root
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # mcp, httpx, pytest
```

`requirements.txt` is intentionally tiny:

```
mcp>=1.27
httpx>=0.27
pytest>=8.0
```

---

## Running it locally

**1. Start the REST API (port 8000):**

```bash
AMI_API_KEY=dev_key AMI_PUBLIC_URL=http://localhost:8000 python3 ami_api.py
```

If `AMI_API_KEY` is unset, the API boots in dev mode with no auth. `AMI_PUBLIC_URL` is used to build the `signature_url` returned by contracts.

**2. Run the end-to-end flow (stdlib only, no `requests`):**

```bash
AMI_API_URL=http://localhost:8000 AMI_API_KEY=dev_key python3 demo_flow.py
```

`demo_flow.py` walks the full lifecycle: health → SIMRequest + offer → accept offer → customer data → contract → web signature callback → activation → identity status + audit events.

**3. Start the MCP server:**

```bash
# stdio (local clients)
AMI_API_URL=http://localhost:8000 AMI_API_KEY=dev_key .venv/bin/python ami_mcp.py

# streamable-http (remote clients), binds 0.0.0.0:8001 by default
AMI_API_URL=http://localhost:8000 AMI_API_KEY=dev_key .venv/bin/python ami_mcp.py http
```

### Environment variables

| Variable         | Component  | Description                                                          |
|------------------|------------|----------------------------------------------------------------------|
| `AMI_API_URL`    | mcp / demo | Base URL of the AMI backend. Default `http://localhost:8000`.        |
| `AMI_API_KEY`    | api / mcp  | Bearer token. If unset on the API, it boots dev mode without auth.   |
| `AMI_PUBLIC_URL` | api        | Public URL used to build the contract `signature_url`.               |
| `AMI_MCP_HOST`   | mcp http   | Bind host for HTTP transport. Default `0.0.0.0`.                     |
| `AMI_MCP_PORT`   | mcp http   | Port for HTTP transport. Default `8001`.                            |
| `PORT`           | api        | HTTP backend port. Default `8000`.                                  |

---

## Running the tests

The suite lives in `tests/` (~620 tests across the repo; the exact function count today is 554). It runs the same way CI does:

```bash
pytest -q --tb=short
```

`pytest.ini` sets `testpaths = tests` and excludes `sdk`, `infra`, `.venv`, `.git`, `node_modules`. The SDKs under `sdk/python` and `sdk/typescript` carry their own test setups — run those from inside their directories.

CI (`.github/workflows/test.yml`) additionally:

- runs the suite on the **Python 3.11 and 3.12** matrix,
- sanity-renders every landing/spec page and validates the OpenAPI document,
- runs the **`brand-check`** job that fails the build if a prohibited brand name appears in a public surface.

Before opening a PR, run `pytest -q` locally and make sure it is green.

---

## Code style

- **Standard library first.** Match the existing style in `ami_api.py`, `ami_mcp.py`, and the `ami_*` modules — plain functions, explicit state machine transitions, no magic.
- **Keep the public surface stable.** The REST v1 endpoints and the 28 `ami.*` MCP tools are a contract. Additive changes are fine; breaking changes need discussion in an issue first.
- **Follow the state machine.** Provisioning moves through `SIMRequest → Offer → Customer → Contract → Signature → MobileIdentity`. New flows must respect it and emit the corresponding audit events.
- **Every new capability needs a test** in `tests/` and, if it touches a public surface (endpoint, MCP tool, landing catalog), an update to the relevant catalog and to the docs.
- 4-space indentation, clear names, small functions. When in doubt, read a neighbouring module and mirror it.

---

## Opening a pull request

1. **Fork** the repo and create a topic branch off `main` (e.g. `feat/inbound-sms-filter` or `fix/kyc-status-poll`).
2. Make your change. Add or update tests. Keep commits focused; write clear commit messages.
3. Run `pytest -q --tb=short` locally — green required.
4. If you touched a public surface, update the relevant docs/catalog and confirm no prohibited brand names slipped in.
5. Push and open a PR against `main`. Fill in the pull request template: what changed, why, how you tested it, and which surfaces (REST / MCP / docs) are affected.
6. CI must be green (tests on 3.11 + 3.12, page render sanity, `brand-check`) before review.

Small, well-scoped PRs get reviewed fastest. If your change is large or shifts the protocol surface, open an issue to align on the approach before you write the code.

By contributing you agree that your contributions are licensed under the **Apache License 2.0**, the same license as the project (see [LICENSE](LICENSE)).
