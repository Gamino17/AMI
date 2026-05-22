"""Tests for :class:`ami.AmiClient` — the customer-facing client."""
from __future__ import annotations

import json

import pytest

from ami import (
    AmiAgent,
    AmiAuthError,
    AmiClient,
    AmiConflictError,
    AmiNotFound,
    AmiRateLimitError,
    AmiServerError,
    AmiValidationError,
)


def test_request_number_sends_correct_payload_and_headers(requests_mock, client, base_url, api_key):
    requests_mock.post(
        f"{base_url}/v1/sim-requests",
        status_code=201,
        json={
            "sim_request": {"id": "simreq_abc", "status": "offer_created"},
            "offer": {
                "id": "offer_123",
                "sim_request_id": "simreq_abc",
                "status": "offer_created",
                "country": "ES",
                "sim_type": "eSIM",
                "capabilities": ["sms", "voice"],
                "monthly_price": 8.9,
                "setup_fee": 5.0,
                "currency": "EUR",
                "requires_contract": True,
                "requires_customer_data": True,
                "expires_at": "2026-05-29T00:00:00Z",
            },
        },
    )

    offer = client.request_number(
        country="ES",
        capabilities=["sms", "voice"],
        agent_name="agent01",
    )

    sent = requests_mock.last_request
    assert sent.headers["Authorization"] == f"Bearer {api_key}"
    assert sent.headers["Content-Type"].startswith("application/json")
    body = json.loads(sent.body)
    assert body["country"] == "ES"
    assert body["capabilities"] == ["sms", "voice"]
    assert body["agent"] == {"name": "agent01", "purpose": "agent_identity"}

    assert offer.id == "offer_123"
    assert offer.sim_request_id == "simreq_abc"
    assert offer.monthly_price == 8.9
    assert offer.capabilities == ["sms", "voice"]


def test_accept_offer_uses_path_id(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/offers/offer_123/accept",
        json={
            "id": "offer_123",
            "sim_request_id": "simreq_abc",
            "status": "offer_accepted",
            "country": "ES",
            "sim_type": "eSIM",
            "capabilities": ["sms"],
            "monthly_price": 8.9,
            "setup_fee": 5.0,
            "currency": "EUR",
            "requires_contract": True,
            "requires_customer_data": True,
        },
    )

    offer = client.accept_offer("offer_123")
    assert offer.status == "offer_accepted"


def test_submit_customer_data_nests_payload(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/sim-requests/simreq_abc/customer-data",
        status_code=201,
        json={
            "sim_request": {"id": "simreq_abc"},
            "customer": {
                "id": "customer_42",
                "status": "created",
                "legal_name": "Acme S.L.",
                "tax_id": "B12345678",
                "billing_email": "billing@acme.test",
                "address": "Madrid, Spain",
                "representative_name": "Ada Lovelace",
            },
        },
    )

    cust = client.submit_customer_data(
        sim_request_id="simreq_abc",
        legal_name="Acme S.L.",
        tax_id="B12345678",
        billing_email="billing@acme.test",
        address="Madrid, Spain",
        representative_name="Ada Lovelace",
    )

    body = json.loads(requests_mock.last_request.body)
    assert body == {
        "customer": {
            "legal_name": "Acme S.L.",
            "tax_id": "B12345678",
            "billing_email": "billing@acme.test",
            "address": "Madrid, Spain",
            "representative_name": "Ada Lovelace",
        }
    }
    assert cust.id == "customer_42"
    assert cust.legal_name == "Acme S.L."


def test_activate_identity_returns_agent_token(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/activate",
        status_code=201,
        json={
            "id": "mid_001",
            "status": "active",
            "phone_number": "+34600000000",
            "sim_type": "eSIM",
            "capabilities": ["sms", "voice"],
            "contract_id": "contract_789",
            "customer_id": "customer_42",
            "agent_token": "amiagt_live_deadbeef",
        },
    )

    identity = client.activate_identity("contract_789")
    assert identity.id == "mid_001"
    assert identity.mid == "mid_001"
    assert identity.agent_token == "amiagt_live_deadbeef"


def test_provision_number_runs_full_flow(requests_mock, client, base_url):
    """``provision_number`` chains four POSTs in the expected order."""
    requests_mock.post(
        f"{base_url}/v1/sim-requests",
        json={
            "sim_request": {"id": "simreq_abc"},
            "offer": {
                "id": "offer_123",
                "sim_request_id": "simreq_abc",
                "status": "offer_created",
                "country": "ES",
                "sim_type": "eSIM",
                "capabilities": ["sms", "voice"],
                "monthly_price": 8.9,
                "setup_fee": 5.0,
                "currency": "EUR",
                "requires_contract": True,
                "requires_customer_data": True,
            },
        },
        status_code=201,
    )
    requests_mock.post(
        f"{base_url}/v1/offers/offer_123/accept",
        json={"id": "offer_123", "sim_request_id": "simreq_abc", "status": "offer_accepted",
              "country": "ES", "sim_type": "eSIM", "capabilities": ["sms", "voice"],
              "monthly_price": 8.9, "setup_fee": 5.0, "currency": "EUR",
              "requires_contract": True, "requires_customer_data": True},
    )
    requests_mock.post(
        f"{base_url}/v1/sim-requests/simreq_abc/customer-data",
        status_code=201,
        json={
            "sim_request": {"id": "simreq_abc"},
            "customer": {
                "id": "customer_42",
                "status": "created",
                "legal_name": "Acme S.L.",
                "tax_id": "B12345678",
                "billing_email": "billing@acme.test",
                "address": "Madrid, Spain",
                "representative_name": "Ada Lovelace",
            },
        },
    )
    requests_mock.post(
        f"{base_url}/v1/contracts",
        status_code=201,
        json={
            "id": "contract_789",
            "status": "signature_pending",
            "offer_id": "offer_123",
            "customer_id": "customer_42",
            "sim_request_id": "simreq_abc",
            "signature_url": "https://example.test/v1/sign/contract_789",
        },
    )
    requests_mock.post(
        f"{base_url}/v1/contracts/contract_789/mock-sign",
        json={
            "id": "contract_789",
            "status": "signed",
            "offer_id": "offer_123",
            "customer_id": "customer_42",
            "sim_request_id": "simreq_abc",
            "signature_url": "https://example.test/v1/sign/contract_789",
        },
    )
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/activate",
        status_code=201,
        json={
            "id": "mid_001",
            "status": "active",
            "phone_number": "+34600000000",
            "sim_type": "eSIM",
            "capabilities": ["sms", "voice"],
            "contract_id": "contract_789",
            "customer_id": "customer_42",
            "agent_token": "amiagt_live_token_xxx",
        },
    )

    creds = client.provision_number(
        country="ES",
        customer={
            "legal_name": "Acme S.L.",
            "tax_id": "B12345678",
            "billing_email": "billing@acme.test",
            "address": "Madrid, Spain",
            "representative_name": "Ada Lovelace",
        },
    )

    assert creds.mid == "mid_001"
    assert creds.phone_number == "+34600000000"
    assert creds.agent_token == "amiagt_live_token_xxx"
    assert creds.customer_id == "customer_42"
    assert creds.contract_id == "contract_789"


def test_as_agent_returns_authenticated_agent(client):
    agent = client.as_agent("amiagt_live_abc")
    assert isinstance(agent, AmiAgent)
    assert agent.base_url == client.base_url


def test_update_limits_requires_at_least_one_field(client):
    with pytest.raises(ValueError):
        client.update_limits("mid_001")


def test_update_limits_sends_only_set_fields(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/mid_001/limits",
        json={"mid": "mid_001", "limits": {"sms_per_day": 1000, "monthly_budget_eur": 50.0}},
    )

    out = client.update_limits("mid_001", sms_per_day=1000, monthly_budget_eur=50.0)
    body = json.loads(requests_mock.last_request.body)
    assert body == {"sms_per_day": 1000, "monthly_budget_eur": 50.0}
    assert out == {"sms_per_day": 1000, "monthly_budget_eur": 50.0}


def test_create_webhook_returns_secret(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/mid_001/webhooks",
        status_code=201,
        json={
            "id": "wh_42",
            "mid": "mid_001",
            "url": "https://acme.example/hook",
            "events": ["sms.inbound"],
            "status": "active",
            "secret": "whsec_supersecret",
        },
    )

    wh = client.create_webhook(
        "mid_001",
        url="https://acme.example/hook",
        events=["sms.inbound"],
    )
    assert wh.id == "wh_42"
    assert wh.secret == "whsec_supersecret"


def test_rotate_agent_token_returns_new_plain_token(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/mid_001/rotate-token",
        json={
            "mid": "mid_001",
            "agent_token": "amiagt_live_newone",
            "revoked_previous": 1,
            "rotated_at": "2026-05-22T10:00:00Z",
        },
    )

    new_token = client.rotate_agent_token("mid_001")
    assert new_token == "amiagt_live_newone"


def test_set_inbound_sip_uri_supports_clear(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/mid_001/inbound-config",
        json={"mid": "mid_001", "inbound_sip_uri": None},
    )
    client.set_inbound_sip_uri("mid_001", None)
    assert json.loads(requests_mock.last_request.body) == {"inbound_sip_uri": None}


# -- error mapping ----------------------------------------------------------


def test_401_raises_auth_error(requests_mock, client, base_url):
    requests_mock.get(
        f"{base_url}/v1/mobile-identities/mid_001",
        status_code=401,
        json={"error": "unauthorized"},
    )
    with pytest.raises(AmiAuthError) as exc:
        client.get_identity("mid_001")
    assert exc.value.status_code == 401
    assert exc.value.reason == "unauthorized"


def test_404_raises_not_found(requests_mock, client, base_url):
    requests_mock.get(
        f"{base_url}/v1/mobile-identities/mid_xxx",
        status_code=404,
        json={"error": "not_found", "id": "mid_xxx"},
    )
    with pytest.raises(AmiNotFound):
        client.get_identity("mid_xxx")


def test_409_raises_conflict(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/mobile-identities/activate",
        status_code=409,
        json={"error": "contract_not_signed", "status": "signature_pending"},
    )
    with pytest.raises(AmiConflictError) as exc:
        client.activate_identity("contract_xxx")
    assert exc.value.reason == "contract_not_signed"


def test_400_raises_validation(requests_mock, client, base_url):
    requests_mock.post(
        f"{base_url}/v1/contracts",
        status_code=400,
        json={"error": "missing_fields", "fields": ["legal_name"]},
    )
    with pytest.raises(AmiValidationError) as exc:
        client.create_contract(offer_id="offer_x", customer_id="customer_x")
    assert exc.value.detail == {"error": "missing_fields", "fields": ["legal_name"]}


def test_429_raises_rate_limit_with_reason(requests_mock, client, base_url):
    requests_mock.get(
        f"{base_url}/v1/mobile-identities/mid_001/usage",
        status_code=429,
        json={"error": "monthly_budget_exceeded"},
    )
    with pytest.raises(AmiRateLimitError) as exc:
        client.get_usage("mid_001")
    assert exc.value.reason == "monthly_budget_exceeded"


def test_5xx_on_get_retries_then_raises(requests_mock, client, base_url):
    """A persistent 5xx on a GET retries and then surfaces ``AmiServerError``."""
    requests_mock.get(
        f"{base_url}/v1/mobile-identities/mid_001",
        status_code=503,
        json={"error": "service_unavailable"},
    )
    # Avoid sleeping in tests.
    import ami._http as http_mod
    original = http_mod._RETRY_BACKOFF_S
    http_mod._RETRY_BACKOFF_S = (0.0, 0.0, 0.0)
    try:
        with pytest.raises(AmiServerError):
            client.get_identity("mid_001")
    finally:
        http_mod._RETRY_BACKOFF_S = original
    # 1 initial + 3 retries
    assert requests_mock.call_count == 4


def test_5xx_on_get_recovers(requests_mock, client, base_url):
    """Second attempt succeeds — the SDK returns the recovered value."""
    requests_mock.get(
        f"{base_url}/v1/mobile-identities/mid_001",
        [
            {"status_code": 502, "json": {"error": "bad_gateway"}},
            {"status_code": 200, "json": {
                "id": "mid_001",
                "status": "active",
                "phone_number": "+34600000000",
                "sim_type": "eSIM",
                "capabilities": ["sms"],
                "contract_id": "contract_x",
                "customer_id": "customer_x",
            }},
        ],
    )
    import ami._http as http_mod
    original = http_mod._RETRY_BACKOFF_S
    http_mod._RETRY_BACKOFF_S = (0.0, 0.0, 0.0)
    try:
        identity = client.get_identity("mid_001")
    finally:
        http_mod._RETRY_BACKOFF_S = original
    assert identity.id == "mid_001"
    assert requests_mock.call_count == 2


def test_post_5xx_does_not_retry(requests_mock, client, base_url):
    """POST is never retried — side effects must not be duplicated."""
    requests_mock.post(
        f"{base_url}/v1/contracts/contract_x/mock-sign",
        status_code=500,
        json={"error": "boom"},
    )
    with pytest.raises(AmiServerError):
        client.mock_sign("contract_x")
    assert requests_mock.call_count == 1
