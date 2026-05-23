"""Tests de las páginas-de-presentación añadidas para la reunión con socios."""
from __future__ import annotations

import json

import httpx
import pytest


PITCH_PAGES = ["/live", "/use-cases", "/pricing", "/calculator", "/waitlist"]


@pytest.mark.parametrize("path", PITCH_PAGES)
def test_pitch_page_renders(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert len(r.text) > 20000, f"{path} suspiciously small"


@pytest.mark.parametrize("path", PITCH_PAGES)
def test_pitch_page_has_chrome(anon_client, path):
    r = anon_client.get(path)
    assert "ami-chrome-header" in r.text
    assert "ami-chrome-footer" in r.text


def test_live_page_has_demo_controls(anon_client):
    r = anon_client.get("/live")
    for needle in ("liveStart", "liveReset", "live-step", "live-phone",
                   "live-summary", "/v1/demo/quick"):
        assert needle in r.text, f"missing: {needle}"


def test_use_cases_has_six_cards(anon_client):
    r = anon_client.get("/use-cases")
    body = r.text
    # Cuento las 6 industrias / títulos del subagent
    for needle in ("Recordatorio", "Cobros", "Recepción", "NPS", "2FA", "delivery"):
        # bilingüe, alguna versión aparece
        assert needle.lower() in body.lower(), f"missing case: {needle}"


def test_pricing_has_three_plans(anon_client):
    r = anon_client.get("/pricing")
    body = r.text
    for plan in ("Starter", "Growth", "Enterprise"):
        assert plan in body, f"missing plan: {plan}"


def test_calculator_has_interactive_inputs(anon_client):
    r = anon_client.get("/calculator")
    body = r.text
    # Inputs esperados de la calc
    for needle in ('type="number"', 'type="range"', 'id="'):
        assert needle in body


def test_waitlist_page_has_form(anon_client):
    r = anon_client.get("/waitlist")
    body = r.text
    assert '<form' in body
    assert 'name="email"' in body
    assert 'name="use_case"' in body


def test_waitlist_post_creates_entry(server_url):
    """POST /v1/waitlist con datos válidos → 201 + id."""
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.post("/v1/waitlist",
                   json={"name": "Ada Lovelace",
                         "email": "ada@example.com",
                         "company": "Analytical Engines",
                         "use_case": "agent_provisioning",
                         "context": "Building an AI agent"})
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] is True
    assert body["id"].startswith("wl_")


def test_waitlist_post_invalid_email_is_400(server_url):
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        r = c.post("/v1/waitlist",
                   json={"name": "X", "email": "not-an-email",
                         "use_case": "just_curious"})
    assert r.status_code == 400


def test_waitlist_admin_list_requires_admin_auth(server_url, ami_api_module, monkeypatch):
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", "admin_pitch_key")
    with httpx.Client(base_url=server_url, timeout=5.0) as c:
        # Sin auth
        r = c.get("/v1/admin/waitlist")
        assert r.status_code == 401
        # Con auth
        r = c.get("/v1/admin/waitlist",
                  headers={"Authorization": "Bearer admin_pitch_key"})
        assert r.status_code == 200
        assert "entries" in r.json()


def test_landing_explore_links_to_new_pages(anon_client):
    r = anon_client.get("/")
    body = r.text
    for href in ('href="/live"', 'href="/use-cases"',
                 'href="/pricing"', 'href="/calculator"'):
        assert href in body, f"explore missing {href}"


def test_chrome_nav_contains_new_pages(anon_client):
    """El header común debe enlazar Live, Use cases, Pricing además de las viejas."""
    r = anon_client.get("/")
    body = r.text
    for href in ('href="/live"', 'href="/use-cases"', 'href="/pricing"'):
        assert href in body, f"nav missing {href}"


def test_pitch_pages_brand_check_clean(anon_client):
    """Brand-check sobre las 5 pitch pages — cero menciones prohibidas."""
    prohibited = ("twilio", "vonage", "messagebird", "hostinger", "vercel",
                  "replit", "railway", "stripe", "shopify", "cloudflare",
                  "openai", "demerzel", "openclaw")
    for path in PITCH_PAGES:
        body = anon_client.get(path).text.lower()
        for brand in prohibited:
            assert brand not in body, f"{path} leaks brand: {brand}"
