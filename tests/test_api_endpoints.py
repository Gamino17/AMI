"""Endpoint-level smoke tests: payload defaults, validation, 404s, audit events, compat routes."""
from __future__ import annotations


def test_sim_options_lists_spain(client):
    r = client.get("/v1/sim-options")
    assert r.status_code == 200
    body = r.json()
    assert "countries" in body
    assert "ES" in body["countries"], "Spain (ES) is the reference country in v1"
    es = body["countries"]["ES"]
    assert "sim_types" in es and "eSIM" in es["sim_types"]
    assert "capabilities" in es
    assert "price" in es and "currency" in es["price"]


def test_create_sim_request_with_unsupported_country_returns_400(client):
    r = client.post("/v1/sim-requests", json={"country": "JP"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unsupported_country"
    assert body["country"] == "JP"


def test_create_sim_request_with_empty_payload_uses_defaults(client):
    r = client.post("/v1/sim-requests", json={})
    assert r.status_code == 201
    sim = r.json()["sim_request"]
    assert sim["country"] == "ES", "default country is ES"
    assert sim["sim_type"] == "eSIM", "default sim_type is eSIM"
    # capabilities default is an empty list at the request level; the offer fills them in.
    assert sim["capabilities"] == []
    offer = r.json()["offer"]
    assert offer["capabilities"] == ["sms", "voice"], (
        "offers fill defaults [sms, voice] when SIMRequest had no capabilities"
    )


def test_create_sim_request_no_body_at_all_uses_defaults(client):
    # Some clients POST with no Content-Length / empty body.
    r = client.post("/v1/sim-requests")
    assert r.status_code == 201
    assert r.json()["sim_request"]["country"] == "ES"


def test_get_unknown_sim_request_returns_404(client):
    r = client.get("/v1/sim-requests/sim_does_not_exist")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_get_unknown_contract_returns_404(client):
    r = client.get("/v1/contracts/ctr_nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_get_unknown_mobile_identity_returns_404(client):
    r = client.get("/v1/mobile-identities/mid_nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_unknown_route_returns_404(client):
    r = client.get("/v1/this-route-does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"] == "unknown_route"


def test_events_endpoint_returns_audit_trail(client):
    # Generate some activity first.
    client.post("/v1/sim-requests", json={"country": "ES"})
    r = client.get("/v1/events")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) >= 2, "creating a SIMRequest should emit at least 2 events"

    # Spec requires every event to carry these fields.
    for evt in events:
        for field in ("id", "at", "action", "entity_type", "entity_id", "data"):
            assert field in evt, f"event missing field {field}: {evt}"
        assert evt["id"].startswith("evt_")


def test_events_include_sim_request_lifecycle_actions(client):
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    client.post(f"/v1/offers/{sim['offer']['id']}/accept")
    actions = [e["action"] for e in client.get("/v1/events").json()["events"]]
    assert "sim_request_created" in actions
    assert "offer_created" in actions
    assert "offer_accepted" in actions


def test_legacy_post_customers_still_works(client):
    payload = {
        "legal_name": "Legacy Co",
        "tax_id": "Z00",
        "billing_email": "legacy@example.com",
        "address": "Old St 1",
        "representative_name": "Grace Hopper",
    }
    r = client.post("/v1/customers", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["id"].startswith("customer_")
    assert body["status"] == "created"
    assert body["legal_name"] == "Legacy Co"


def test_invalid_json_body_returns_400(client):
    r = client.post(
        "/v1/sim-requests",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_json"


def test_offer_accept_emits_offer_accepted_status(client):
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    r = client.post(f"/v1/offers/{sim['offer']['id']}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "offer_accepted"
    assert "accepted_at" in r.json()


def test_double_accept_offer_is_409(client):
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    offer_id = sim["offer"]["id"]
    client.post(f"/v1/offers/{offer_id}/accept")
    second = client.post(f"/v1/offers/{offer_id}/accept")
    assert second.status_code == 409
    assert second.json()["error"] == "offer_not_acceptable"


def test_customer_data_with_missing_fields_returns_400(client):
    sim = client.post("/v1/sim-requests", json={"country": "ES"}).json()
    client.post(f"/v1/offers/{sim['offer']['id']}/accept")
    # Only send legal_name; everything else is missing.
    r = client.post(
        f"/v1/sim-requests/{sim['sim_request']['id']}/customer-data",
        json={"legal_name": "Acme"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "missing_fields"
    assert "tax_id" in body["fields"]
    assert "billing_email" in body["fields"]


def test_health_endpoint_payload_shape(anon_client):
    r = anon_client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "ami-mock"
    assert "time" in body
