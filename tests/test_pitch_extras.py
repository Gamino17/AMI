"""Tests de las 4 páginas adicionales del kit de presentación:
/pitch (deck), /sandbox (playground), /status (uptime), /security (whitepaper)."""
from __future__ import annotations

import pytest


PAGES = ["/pitch", "/sandbox", "/status", "/security"]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert len(r.text) > 25000, f"{path} suspiciously small"


def test_pitch_has_deck_controls(anon_client):
    body = anon_client.get("/pitch").text
    for needle in ("pitch-slide", "pitchNext", "pitchPrev", "ArrowRight",
                   "data-slide", "Esc", "fullscreen"):
        assert needle in body, f"missing pitch control: {needle}"


def test_pitch_has_multiple_slides(anon_client):
    """El deck debe tener al menos 12 slides."""
    body = anon_client.get("/pitch").text
    assert body.count('class="pitch-slide"') >= 12


def test_pitch_no_chrome(anon_client):
    """/pitch es modo presentación full-screen — NO debe llevar chrome común."""
    body = anon_client.get("/pitch").text
    assert "ami-chrome-header" not in body
    assert "pitch-chrome" in body  # tiene su propio chrome minimalista


def test_sandbox_has_runner(anon_client):
    body = anon_client.get("/sandbox").text
    for needle in ("runRequest", "/v1/demo/quick", "/v1/health", "/v1/events"):
        assert needle in body, f"sandbox missing: {needle}"


def test_sandbox_has_snippet_tabs(anon_client):
    """El sandbox debe tener al menos 5 snippets pre-cargados."""
    body = anon_client.get("/sandbox").text
    # Buscamos los nombres de los snippets esperados
    for label in ("Demo quick", "Health", "events", "openapi"):
        assert label.lower() in body.lower(), f"sandbox missing snippet: {label}"


def test_status_has_components(anon_client):
    body = anon_client.get("/status").text
    # Componentes principales
    for needle in ("REST", "MCP", "SQLite", "operational"):
        assert needle in body, f"status missing: {needle}"


def test_security_has_sections(anon_client):
    body = anon_client.get("/security").text
    # Secciones esperadas del whitepaper
    for needle in ("multi-tenant", "HMAC", "SSRF", "audit", "Rate limits"):
        assert needle.lower() in body.lower(), f"security missing: {needle}"


@pytest.mark.parametrize("path", PAGES)
def test_brand_check_clean(anon_client, path):
    body = anon_client.get(path).text.lower()
    for brand in ("twilio", "vonage", "messagebird", "hostinger", "vercel",
                  "replit", "railway", "stripe", "shopify", "cloudflare",
                  "openai", "demerzel", "openclaw"):
        assert brand not in body, f"{path} leaks brand: {brand}"


def test_nav_includes_new_pages(anon_client):
    """El nav del chrome común debe enlazar a las nuevas."""
    body = anon_client.get("/").text
    for href in ('href="/pitch"', 'href="/sandbox"'):
        assert href in body, f"nav missing {href}"


def test_footer_includes_new_pages(anon_client):
    body = anon_client.get("/").text
    for href in ('href="/status"', 'href="/security"', 'href="/sandbox"'):
        assert href in body, f"footer missing {href}"


def test_landing_explore_includes_new_pages(anon_client):
    body = anon_client.get("/").text
    for href in ('href="/pitch"', 'href="/sandbox"',
                 'href="/security"', 'href="/status"'):
        assert href in body, f"explore missing {href}"
