"""Shared pytest fixtures.

The SDK is tested without any real network call. A ``requests_mock`` fixture
intercepts every HTTP request issued by the underlying ``requests.Session``
and lets each test register the responses it cares about. The base URL used
across the suite is the canonical production URL — a value the user does
not have to override during development.
"""
from __future__ import annotations

import pytest

from ami import AmiAgent, AmiClient


BASE_URL = "https://api.protocolami.com"
API_KEY = "ami_live_testkey_0000"
AGENT_TOKEN = "amiagt_live_" + "a" * 64


@pytest.fixture
def base_url() -> str:
    return BASE_URL


@pytest.fixture
def api_key() -> str:
    return API_KEY


@pytest.fixture
def agent_token() -> str:
    return AGENT_TOKEN


@pytest.fixture
def client(api_key: str, base_url: str) -> AmiClient:
    """A fresh :class:`AmiClient` pointed at the canonical base URL."""
    return AmiClient(api_key=api_key, base_url=base_url)


@pytest.fixture
def agent(agent_token: str, base_url: str) -> AmiAgent:
    """A fresh :class:`AmiAgent` pointed at the canonical base URL."""
    return AmiAgent(agent_token=agent_token, base_url=base_url)
