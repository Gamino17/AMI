"""Tests for the HTML signature flow + the legacy mock-sign shortcut."""
from __future__ import annotations

import pytest


@pytest.fixture
def signed_ready_contract(client, sample_customer_payload):
    """Drive a SIMRequest up to a signature_pending contract and return it."""
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    offer_id = sim["offer"]["id"]
    sim_id = sim["sim_request"]["id"]
    client.post(f"/v1/offers/{offer_id}/accept")
    submitted = client.post(
        f"/v1/sim-requests/{sim_id}/customer-data",
        json=sample_customer_payload,
    ).json()
    contract = client.post("/v1/contracts", json={
        "offer_id": offer_id,
        "customer_id": submitted["customer"]["id"],
    }).json()
    return contract


def test_sign_page_renders_customer_and_offer_data(anon_client, signed_ready_contract, sample_customer_payload):
    contract = signed_ready_contract
    t = contract["signing_token"]

    r = anon_client.get(f"/v1/sign/{contract['id']}?t={t}")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")

    html = r.text
    # Customer fields rendered.
    assert sample_customer_payload["legal_name"] in html
    assert sample_customer_payload["tax_id"] in html
    assert sample_customer_payload["billing_email"] in html
    # Offer fields rendered.
    assert "eSIM" in html
    assert "EUR" in html
    # Pending state shows the form button.
    assert "Pendiente de firma" in html
    assert "Firmar contrato" in html


def test_sign_for_unknown_contract_returns_404_html(anon_client):
    r = anon_client.get("/v1/sign/ctr_does_not_exist")
    assert r.status_code == 404
    assert "text/html" in r.headers.get("content-type", "")
    assert "no encontrado" in r.text.lower()


def test_confirm_sign_flips_contract_to_signed(anon_client, client, signed_ready_contract):
    contract = signed_ready_contract
    t = contract["signing_token"]

    r = anon_client.post(
        f"/v1/sign/{contract['id']}/confirm?t={t}",
        data={},  # form-urlencoded, empty body
    )
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Firmado" in r.text

    # Reading the contract back via JSON API confirms persistence.
    fresh = client.get(f"/v1/contracts/{contract['id']}").json()
    assert fresh["status"] == "signed"
    assert "signed_at" in fresh


def test_reload_sign_page_after_signing_shows_signed_state(anon_client, signed_ready_contract):
    contract = signed_ready_contract
    t = contract["signing_token"]
    anon_client.post(f"/v1/sign/{contract['id']}/confirm?t={t}", data={})

    r = anon_client.get(f"/v1/sign/{contract['id']}?t={t}")
    assert r.status_code == 200
    html = r.text
    assert "Firmado" in html
    # The pending-state form should be gone (no submit button).
    assert "Firmar contrato" not in html, (
        "after signing the page must not show the sign button anymore"
    )


def test_legacy_mock_sign_endpoint_still_works(client, signed_ready_contract):
    contract = signed_ready_contract

    r = client.post(f"/v1/contracts/{contract['id']}/mock-sign")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "signed"
    assert "signed_at" in body


def test_mock_sign_twice_returns_409(client, signed_ready_contract):
    contract = signed_ready_contract
    first = client.post(f"/v1/contracts/{contract['id']}/mock-sign")
    assert first.status_code == 200

    second = client.post(f"/v1/contracts/{contract['id']}/mock-sign")
    assert second.status_code == 409
    assert second.json()["error"] == "already_signed"


def test_mock_sign_on_unknown_contract_returns_404(client):
    r = client.post("/v1/contracts/ctr_nope/mock-sign")
    assert r.status_code == 404
    assert r.json()["error"] == "contract_not_found"


def test_signing_advances_sim_request_to_signed(client, signed_ready_contract):
    contract = signed_ready_contract
    client.post(f"/v1/contracts/{contract['id']}/mock-sign")

    sim = client.get(f"/v1/sim-requests/{contract['sim_request_id']}").json()
    assert sim["status"] == "signed", (
        f"signing the contract must transition the SIMRequest to signed, got {sim['status']}"
    )
