"""Tests for the SIMRequest state machine (spec section 17.6).

These tests are the safety net for refactors: they pin every legal transition
and several of the illegal ones, so the moment ami_api drifts from the
contract we'll see a red bar.
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- helpers
def _create(client):
    return client.post("/v1/sim-requests", json={"country": "ES"}).json()


def _accept(client, offer_id):
    return client.post(f"/v1/offers/{offer_id}/accept").json()


def _submit_customer(client, sim_id, payload):
    return client.post(f"/v1/sim-requests/{sim_id}/customer-data", json=payload).json()


def _create_contract(client, offer_id, customer_id):
    return client.post("/v1/contracts", json={
        "offer_id": offer_id, "customer_id": customer_id,
    }).json()


def _sign(client, contract_id):
    return client.post(f"/v1/contracts/{contract_id}/mock-sign").json()


def _activate(client, contract_id):
    return client.post(
        "/v1/mobile-identities/activate",
        json={"contract_id": contract_id},
    ).json()


def _get_sim(client, sim_id):
    return client.get(f"/v1/sim-requests/{sim_id}").json()


# --------------------------------------------------------------------------- happy path
def test_full_happy_path_walks_every_state(client, sample_customer_payload):
    created = _create(client)
    sim, offer = created["sim_request"], created["offer"]

    assert sim["status"] == "offer_created", (
        "creating a SIMRequest must auto-advance to offer_created (offer is generated immediately)"
    )

    _accept(client, offer["id"])
    assert _get_sim(client, sim["id"])["status"] == "offer_accepted"

    submitted = _submit_customer(client, sim["id"], sample_customer_payload)
    assert submitted["sim_request"]["status"] == "customer_data_submitted"

    customer_id = submitted["customer"]["id"]
    contract = _create_contract(client, offer["id"], customer_id)
    assert contract["status"] == "signature_pending"
    assert _get_sim(client, sim["id"])["status"] == "signature_pending"

    signed = _sign(client, contract["id"])
    assert signed["status"] == "signed"
    assert _get_sim(client, sim["id"])["status"] == "signed"

    identity = _activate(client, contract["id"])
    assert identity["status"] == "active"
    final = _get_sim(client, sim["id"])
    assert final["status"] == "active", (
        f"after activation the SIMRequest should be active, got {final['status']}"
    )
    assert final.get("mobile_identity_id") == identity["id"]


# --------------------------------------------------------------------------- illegal transitions
def test_customer_data_without_offer_acceptance_is_409(client, sample_customer_payload):
    created = _create(client)
    sim_id = created["sim_request"]["id"]

    r = client.post(
        f"/v1/sim-requests/{sim_id}/customer-data",
        json=sample_customer_payload,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "invalid_state"
    assert body["expected"] == "offer_accepted"
    assert body["actual"] == "offer_created"


def test_create_contract_without_offer_acceptance_is_409(client):
    created = _create(client)
    offer_id = created["offer"]["id"]

    # Build a customer through the compat endpoint so that's not the missing piece.
    customer = client.post("/v1/customers", json={
        "legal_name": "X", "tax_id": "Y", "billing_email": "a@b.c",
        "address": "addr", "representative_name": "rep",
    }).json()

    r = client.post("/v1/contracts", json={
        "offer_id": offer_id, "customer_id": customer["id"],
    })
    assert r.status_code == 409
    assert r.json()["error"] == "offer_not_accepted"


def test_activate_without_signed_contract_is_409(client, sample_customer_payload):
    created = _create(client)
    sim, offer = created["sim_request"], created["offer"]
    _accept(client, offer["id"])
    submitted = _submit_customer(client, sim["id"], sample_customer_payload)
    contract = _create_contract(client, offer["id"], submitted["customer"]["id"])

    # Contract exists but is still signature_pending.
    r = client.post(
        "/v1/mobile-identities/activate",
        json={"contract_id": contract["id"]},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "contract_not_signed"


# --------------------------------------------------------------------------- cancellation
def test_cancel_from_offer_created_state(client):
    created = _create(client)
    sim_id = created["sim_request"]["id"]

    r = client.post(f"/v1/sim-requests/{sim_id}/cancel", json={"reason": "no_longer_needed"})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert r.json().get("status_reason") == "no_longer_needed"


def test_cancel_from_offer_accepted_state(client):
    created = _create(client)
    sim_id = created["sim_request"]["id"]
    _accept(client, created["offer"]["id"])

    r = client.post(f"/v1/sim-requests/{sim_id}/cancel", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_on_terminal_state_is_409(client, sample_customer_payload):
    # Drive the request all the way to active, then try to cancel.
    created = _create(client)
    sim, offer = created["sim_request"], created["offer"]
    _accept(client, offer["id"])
    submitted = _submit_customer(client, sim["id"], sample_customer_payload)
    contract = _create_contract(client, offer["id"], submitted["customer"]["id"])
    _sign(client, contract["id"])
    _activate(client, contract["id"])

    r = client.post(f"/v1/sim-requests/{sim['id']}/cancel", json={})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "already_terminal"
    assert body["status"] == "active"


def test_double_cancel_is_409(client):
    created = _create(client)
    sim_id = created["sim_request"]["id"]
    first = client.post(f"/v1/sim-requests/{sim_id}/cancel", json={})
    assert first.status_code == 200

    second = client.post(f"/v1/sim-requests/{sim_id}/cancel", json={})
    assert second.status_code == 409
    assert second.json()["error"] == "already_terminal"


def test_cancel_emits_audit_event(client):
    created = _create(client)
    sim_id = created["sim_request"]["id"]
    client.post(f"/v1/sim-requests/{sim_id}/cancel", json={"reason": "test"})

    events = client.get("/v1/events").json()["events"]
    actions = [e["action"] for e in events]
    assert "sim_request_cancelled" in actions, (
        f"expected sim_request_cancelled in events, got {actions}"
    )
