"""Tests del portal de documentación /docs."""
from __future__ import annotations

import httpx
import pytest


def test_docs_page_returns_html(anon_client):
    r = anon_client.get("/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "AMI" in r.text


def test_docs_page_has_sidebar_with_sections(anon_client):
    r = anon_client.get("/docs")
    body = r.text
    # Sections clave que esperamos en el sidebar
    for sec in ("welcome", "quickstart", "concepts", "auth", "contracting",
                "sms", "voice", "webhooks", "limits", "admin", "errors", "sdks"):
        assert f'data-sidebar-link="{sec}"' in body, f"missing sidebar link: {sec}"


def test_docs_has_code_tabs(anon_client):
    r = anon_client.get("/docs")
    body = r.text
    assert "docs-code-tab" in body
    # Al menos los 4 lenguajes esperados aparecen como labels
    for label in ("Python", "TypeScript", "curl", "MCP"):
        assert label in body


def test_docs_has_tryit_widget(anon_client):
    r = anon_client.get("/docs")
    body = r.text
    assert "data-tryit" in body
    assert "/v1/demo/quick" in body


def test_docs_bilingual(anon_client):
    es = anon_client.get("/docs?lang=es").text
    en = anon_client.get("/docs?lang=en").text
    # El HTML es el mismo (bilingüe inline), solo cambia el <html lang>
    assert '<html lang="es">' in es
    assert '<html lang="en">' in en
    # Ambos bloques de copy están en el HTML (CSS oculta el opposite)
    assert "Bienvenida" in es and "Welcome" in es


def test_docs_has_security_headers(anon_client):
    r = anon_client.get("/docs")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "default-src" in (r.headers.get("Content-Security-Policy") or "")


def test_docs_brand_check_clean(anon_client):
    """Cero menciones de marcas prohibidas en el portal de docs."""
    r = anon_client.get("/docs")
    body = r.text.lower()
    for brand in ("twilio", "vonage", "messagebird", "hostinger", "vercel",
                  "replit", "railway", "stripe", "shopify", "cloudflare",
                  "openai", "demerzel", "openclaw"):
        assert brand not in body, f"brand leak: {brand}"


def test_docs_listed_in_endpoints(anon_client):
    """/docs aparece en los endpoints públicos (landing + openapi)."""
    landing = anon_client.get("/").text
    assert "/docs" in landing

    openapi = anon_client.get("/openapi.json").json()
    assert "/docs" in openapi["paths"]
