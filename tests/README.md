# AMI test suite

Pytest-based contract tests for `ami_api.py`. They boot the real
`ThreadingHTTPServer` in-process on a random port and hit it over HTTP, so the
public contract (auth, state machine, JSON shapes, HTML pages, discovery
endpoints) is what's actually verified.

## Run

From the repo root:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -v
```

Or, if you prefer the system interpreter:

```sh
python3 -m pip install pytest httpx
python3 -m pytest tests/ -v
```

## Layout

- `conftest.py` — session-scoped HTTP server fixture, autouse `STATE` reset
  between tests, authenticated/anon `httpx.Client` fixtures.
- `test_auth.py` — public vs. protected routes, Bearer token validation.
- `test_state_machine.py` — full happy path, illegal transitions, cancellation.
- `test_api_endpoints.py` — payload defaults, validation, 404s, audit events,
  legacy `/v1/customers` compat.
- `test_signature_flow.py` — HTML sign page, form-based confirm, legacy
  `mock-sign` shortcut.
- `test_landing_and_discovery.py` — landing HTML, `llms.txt`, `openapi.json`,
  `install.sh`.

## Notes

- Tests are independent: an autouse fixture wipes `STATE` between each test.
- The server is started once per session and torn down at the end.
- `AMI_API_KEY` is set to `test_key_secret` before `ami_api` is imported, so
  auth is enforced for the duration of the suite.
