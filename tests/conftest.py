"""Shared pytest fixtures for the AMI API test suite.

The strategy: import ami_api in-process, mount a ThreadingHTTPServer on a random
port in a background thread, and reset STATE between tests. We talk to the real
HTTP surface so the tests validate the public contract end-to-end.
"""
from __future__ import annotations

import importlib
import os
import socket
import sys
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest


# Make the project root importable regardless of where pytest is invoked from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


TEST_API_KEY = "test_key_secret"


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@pytest.fixture(scope="session")
def ami_api_module():
    """Import ami_api with the test API key set so module-level constants pick it up."""
    os.environ["AMI_API_KEY"] = TEST_API_KEY
    os.environ.setdefault("AMI_PUBLIC_URL", "http://127.0.0.1")

    if "ami_api" in sys.modules:
        module = importlib.reload(sys.modules["ami_api"])
    else:
        module = importlib.import_module("ami_api")

    # Defensive: in case AMI_API_KEY was empty when ami_api was first imported,
    # the module-level API_KEY may be None. Force the test key so check_auth works.
    module.API_KEY = TEST_API_KEY
    return module


@pytest.fixture(scope="session")
def server_url(ami_api_module):
    """Boot a ThreadingHTTPServer in a background thread and yield its base URL."""
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), ami_api_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(autouse=True)
def reset_state(ami_api_module):
    """Wipe in-memory STATE before every test so they remain independent."""
    state = ami_api_module.STATE
    state["sim_requests"].clear()
    state["offers"].clear()
    state["customers"].clear()
    state["contracts"].clear()
    state["mobile_identities"].clear()
    state["events"].clear()
    yield


@pytest.fixture
def client(server_url):
    """Authenticated httpx.Client with the test bearer token preset."""
    headers = {"Authorization": f"Bearer {TEST_API_KEY}"}
    with httpx.Client(base_url=server_url, headers=headers, timeout=5.0) as c:
        yield c


@pytest.fixture
def anon_client(server_url):
    """Unauthenticated httpx.Client (for testing public routes / 401 responses)."""
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        yield c


@pytest.fixture
def sample_customer_payload():
    """Realistic customer-data payload accepted by /customer-data."""
    return {
        "legal_name": "Acme Robotics S.L.",
        "tax_id": "B12345678",
        "billing_email": "billing@acme.test",
        "address": "Calle Falsa 123, 28001 Madrid",
        "representative_name": "Ada Lovelace",
    }


@pytest.fixture
def fresh_sim_request(client):
    """Helper that creates a SIMRequest and returns the parsed JSON {sim_request, offer}."""
    r = client.post("/v1/sim-requests", json={"country": "ES", "sim_type": "eSIM"})
    assert r.status_code == 201, f"setup failed: {r.status_code} {r.text}"
    return r.json()
