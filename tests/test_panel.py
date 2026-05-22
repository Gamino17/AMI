"""Tests del panel del cliente (dashboard server-rendered)."""
from __future__ import annotations

import re

import httpx
import pytest


# ---------------- login / auth de cookie ----------------

def test_login_page_renders(anon_client):
    r = anon_client.get("/panel/login")
    assert r.status_code == 200
    assert "AMI" in r.text
    assert "API key" in r.text
    assert '<form method="POST"' in r.text


def test_panel_without_cookie_redirects_to_login(anon_client):
    r = anon_client.get("/panel", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/panel/login"


def test_login_with_correct_key_sets_cookie_and_redirects(server_url):
    """POST form-urlencoded directamente (httpx no manda form por defecto)."""
    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False) as c:
        r = c.post("/panel/login", data={"key": "test_key_secret"})
    assert r.status_code == 302
    assert r.headers["location"] == "/panel"
    sc = r.headers.get("set-cookie", "")
    assert "ami-panel-token=test_key_secret" in sc
    assert "HttpOnly" in sc
    assert "SameSite=Lax" in sc


def test_login_with_wrong_key_renders_error(server_url):
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.post("/panel/login", data={"key": "wrong"})
    assert r.status_code == 401
    assert "Invalid API key" in r.text


def test_panel_with_valid_cookie_renders(server_url, active_identity):
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": "test_key_secret"}) as c:
        r = c.get("/panel")
    assert r.status_code == 200
    assert "Overview" in r.text
    # Debe aparecer el MID activado
    assert active_identity["mid"] in r.text


def test_logout_clears_cookie(server_url):
    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False) as c:
        r = c.post("/panel/logout")
    assert r.status_code == 302
    assert r.headers["location"] == "/panel/login"
    sc = r.headers.get("set-cookie", "")
    assert "Max-Age=0" in sc


# ---------------- vista de MID detail ----------------

def test_mid_detail_renders_for_existing_mid(server_url, active_identity):
    mid = active_identity["mid"]
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": "test_key_secret"}) as c:
        r = c.get(f"/panel/mid/{mid}")
    assert r.status_code == 200
    assert mid in r.text
    assert active_identity["identity"]["phone_number"] in r.text
    assert "Usage" in r.text
    assert "Recent SMS" in r.text
    assert "Recent calls" in r.text
    assert "Webhooks" in r.text


def test_mid_detail_404_for_unknown(server_url):
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": "test_key_secret"}) as c:
        r = c.get("/panel/mid/mid_doesnotexist")
    assert r.status_code == 404
    assert "Not found" in r.text


def test_mid_detail_redirects_without_cookie(server_url, active_identity):
    with httpx.Client(base_url=server_url, timeout=5.0, follow_redirects=False) as c:
        r = c.get(f"/panel/mid/{active_identity['mid']}")
    assert r.status_code == 302
    assert r.headers["location"] == "/panel/login"


# ---------------- contenido dinámico ----------------

def test_panel_home_shows_sms_count_after_sending(client, anon_client, active_identity, server_url):
    # Enviar un SMS para que aparezca en la actividad
    anon_client.post(
        "/v1/agent/sms/send",
        headers={"Authorization": f"Bearer {active_identity['agent_token']}"},
        json={"to": "+34611000000", "body": "hola"},
    )
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": "test_key_secret"}) as c:
        r = c.get("/panel")
    # Debe verse el contador de SMS today >= 1
    assert re.search(r"SMS today.*?</div>\s*<div class=\"value\">[1-9]", r.text, re.S)


def test_mid_detail_shows_inbound_uri_when_configured(server_url, client, active_identity):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/inbound-config",
                json={"inbound_sip_uri": "sip:agent@host.example;transport=tls"})
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": "test_key_secret"}) as c:
        r = c.get(f"/panel/mid/{mid}")
    assert "sip:agent@host.example" in r.text


def test_mid_detail_shows_webhook_when_registered(server_url, client, active_identity):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/webhooks",
                json={"url": "https://example.com/x", "events": ["sms.inbound"]})
    with httpx.Client(base_url=server_url, timeout=5.0,
                      cookies={"ami-panel-token": "test_key_secret"}) as c:
        r = c.get(f"/panel/mid/{mid}")
    assert "https://example.com/x" in r.text
    assert "sms.inbound" in r.text
