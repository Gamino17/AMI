"""Authentication tests: which routes are public and how Bearer auth is enforced."""
from __future__ import annotations

import pytest


# Routes that must respond without an Authorization header.
PUBLIC_GET_ROUTES = [
    "/",
    "/v1/health",
    "/llms.txt",
    "/openapi.json",
    "/install.sh",
]


@pytest.mark.parametrize("path", PUBLIC_GET_ROUTES)
def test_public_get_routes_do_not_require_auth(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 200, (
        f"public route {path} returned {r.status_code}: {r.text[:200]}"
    )


def test_sign_page_is_public(anon_client, client):
    # Build a contract via the authenticated flow first so we have a real id.
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    offer_id = sim["offer"]["id"]
    client.post(f"/v1/offers/{offer_id}/accept")
    customer = client.post("/v1/customers", json={
        "legal_name": "X", "tax_id": "Y", "billing_email": "a@b.c",
        "address": "addr", "representative_name": "rep",
    }).json()
    contract = client.post("/v1/contracts", json={
        "offer_id": offer_id, "customer_id": customer["id"],
    }).json()

    # Now hit /v1/sign/{id}?t=<token> and the confirm callback without auth.
    # El signing_token de 128 bits actúa como capability secret de la firma.
    token = contract["signing_token"]
    r_get = anon_client.get(f"/v1/sign/{contract['id']}?t={token}")
    assert r_get.status_code == 200
    assert "text/html" in r_get.headers.get("content-type", "")

    r_post = anon_client.post(f"/v1/sign/{contract['id']}/confirm?t={token}", data={})
    assert r_post.status_code == 200
    assert "text/html" in r_post.headers.get("content-type", "")


PROTECTED_GET_ROUTES = [
    "/v1/events",
    "/v1/sim-options",
    "/v1/sim-requests/nonexistent",
    "/v1/contracts/nonexistent",
    "/v1/mobile-identities/nonexistent",
]


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_protected_get_routes_require_auth(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 401, (
        f"protected route {path} should be 401 without auth, got {r.status_code}"
    )
    assert r.json().get("error") == "unauthorized"


def test_protected_post_routes_require_auth(anon_client):
    r = anon_client.post("/v1/sim-requests", json={"country": "ES"})
    assert r.status_code == 401
    assert r.json().get("error") == "unauthorized"


def test_wrong_bearer_token_is_rejected(anon_client):
    r = anon_client.get(
        "/v1/sim-options",
        headers={"Authorization": "Bearer not_the_real_key"},
    )
    assert r.status_code == 401


def test_authorization_without_bearer_prefix_is_rejected(anon_client):
    # Plain token (no "Bearer " prefix) must be rejected — check_auth requires
    # the literal "Bearer " prefix before comparing.
    r = anon_client.get(
        "/v1/sim-options",
        headers={"Authorization": "test_key_secret"},
    )
    assert r.status_code == 401


def test_correct_bearer_token_is_accepted(client):
    r = client.get("/v1/sim-options")
    assert r.status_code == 200
    assert "countries" in r.json()


def test_correct_bearer_allows_post_creation(client):
    r = client.post("/v1/sim-requests", json={"country": "ES"})
    assert r.status_code == 201
