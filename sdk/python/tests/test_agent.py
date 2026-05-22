"""Tests for :class:`ami.AmiAgent` — the agent_token-scoped client."""
from __future__ import annotations

import json

import pytest

from ami import AmiAuthError, AmiRateLimitError


def test_self_returns_typed_object(requests_mock, agent, base_url, agent_token):
    requests_mock.get(
        f"{base_url}/v1/agent/self",
        json={
            "mobile_identity_id": "mid_001",
            "customer_id": "customer_42",
            "phone_number": "+34600000000",
            "capabilities": ["sms", "voice"],
            "token_prefix": "amiagt_live_ab",
            "token_created_at": "2026-05-22T08:00:00Z",
            "status": "active",
        },
    )

    info = agent.self_()
    sent = requests_mock.last_request
    assert sent.headers["Authorization"] == f"Bearer {agent_token}"
    assert info.mobile_identity_id == "mid_001"
    assert info.capabilities == ["sms", "voice"]
    assert info.status == "active"


def test_whoami_is_alias_of_self(agent):
    # Bound methods are not identical across attribute lookups; compare the
    # underlying function instead.
    assert agent.whoami.__func__ is agent.self_.__func__


def test_send_sms_posts_correct_payload(requests_mock, agent, base_url):
    requests_mock.post(
        f"{base_url}/v1/agent/sms/send",
        status_code=201,
        json={
            "id": "msg_001",
            "mid": "mid_001",
            "direction": "outbound",
            "to": "+34600111222",
            "from": "+34600000000",
            "body": "hola",
            "status": "queued",
            "created_at": "2026-05-22T10:00:00Z",
        },
    )

    msg = agent.send_sms(to="+34600111222", body="hola")
    body = json.loads(requests_mock.last_request.body)
    assert body == {"to": "+34600111222", "body": "hola"}
    assert msg.id == "msg_001"
    assert msg.from_ == "+34600000000"
    assert msg.status == "queued"


def test_list_sms_passes_query_params(requests_mock, agent, base_url):
    requests_mock.get(
        f"{base_url}/v1/agent/sms",
        json={
            "messages": [
                {
                    "id": "msg_001",
                    "mid": "mid_001",
                    "direction": "outbound",
                    "to": "+34600111222",
                    "from": "+34600000000",
                    "body": "hola",
                    "status": "delivered",
                    "created_at": "2026-05-22T10:00:00Z",
                },
            ],
            "count": 1,
        },
    )

    msgs = agent.list_sms(limit=5, direction="outbound")
    sent = requests_mock.last_request
    assert sent.qs == {"limit": ["5"], "direction": ["outbound"]}
    assert len(msgs) == 1
    assert msgs[0].status == "delivered"


def test_iter_sms_yields_one_page(requests_mock, agent, base_url):
    """``iter_sms`` is a generator that, today, yields exactly one page."""
    requests_mock.get(
        f"{base_url}/v1/agent/sms",
        json={
            "messages": [
                {"id": "msg_001", "mid": "mid_001", "direction": "inbound",
                 "to": "+34600000000", "from": "+34600999888", "body": "ack",
                 "status": "delivered", "created_at": "2026-05-22T10:00:00Z"},
                {"id": "msg_002", "mid": "mid_001", "direction": "outbound",
                 "to": "+34600111222", "from": "+34600000000", "body": "ping",
                 "status": "delivered", "created_at": "2026-05-22T10:01:00Z"},
            ],
            "count": 2,
        },
    )

    ids = [m.id for m in agent.iter_sms(page_size=50)]
    assert ids == ["msg_001", "msg_002"]


def test_place_call_includes_callback_uri(requests_mock, agent, base_url):
    requests_mock.post(
        f"{base_url}/v1/agent/calls/place",
        status_code=201,
        json={
            "id": "call_001",
            "mid": "mid_001",
            "direction": "outbound",
            "to": "+34600999888",
            "from": "+34600000000",
            "status": "initiated",
            "callback_sip_uri": "sip:agent@host.example",
            "created_at": "2026-05-22T10:00:00Z",
        },
    )

    call = agent.place_call(to="+34600999888", callback_sip_uri="sip:agent@host.example")
    body = json.loads(requests_mock.last_request.body)
    assert body == {"to": "+34600999888", "callback_sip_uri": "sip:agent@host.example"}
    assert call.status == "initiated"
    assert call.callback_sip_uri == "sip:agent@host.example"


def test_hangup_call(requests_mock, agent, base_url):
    requests_mock.post(
        f"{base_url}/v1/agent/calls/call_001/hangup",
        json={
            "id": "call_001",
            "mid": "mid_001",
            "direction": "outbound",
            "to": "+34600999888",
            "from": "+34600000000",
            "status": "completed",
            "callback_sip_uri": None,
            "created_at": "2026-05-22T10:00:00Z",
        },
    )

    call = agent.hangup_call("call_001")
    assert call.status == "completed"


def test_usage_returns_split_view(requests_mock, agent, base_url):
    requests_mock.get(
        f"{base_url}/v1/agent/usage",
        json={
            "mid": "mid_001",
            "usage": {
                "sms_count_today": 12,
                "calls_count_today": 3,
                "spend_this_month_eur": 4.20,
            },
            "limits": {
                "sms_per_day": 500,
                "calls_per_day": 200,
                "monthly_budget_eur": 100.0,
            },
        },
    )

    snap = agent.usage()
    assert snap.usage["sms_count_today"] == 12
    assert snap.limits["sms_per_day"] == 500
    assert snap.mid == "mid_001"


def test_send_sms_rate_limited_raises_with_reason(requests_mock, agent, base_url):
    requests_mock.post(
        f"{base_url}/v1/agent/sms/send",
        status_code=429,
        json={"reason": "sms_hourly_limit_exceeded"},
    )
    with pytest.raises(AmiRateLimitError) as exc:
        agent.send_sms(to="+34600111222", body="hola")
    assert exc.value.reason == "sms_hourly_limit_exceeded"


def test_invalid_token_raises_auth_error(requests_mock, agent, base_url):
    requests_mock.get(
        f"{base_url}/v1/agent/self",
        status_code=401,
        json={"error": "invalid_agent_token"},
    )
    with pytest.raises(AmiAuthError):
        agent.self_()
