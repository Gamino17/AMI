"""Tests for the public-facing surfaces: landing page, llms.txt, openapi.json, install.sh."""
from __future__ import annotations


def test_landing_page_returns_html_with_title(anon_client):
    r = anon_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "<title>AMI" in html, "landing must have an <title> starting with 'AMI'"
    # Key landmarks.
    assert "hero-title" in html, "landing must include the hero block"
    assert 'class="terminal"' in html, "landing must include the terminal hero"
    assert 'id="quickstart"' in html, "landing must include the Quick Start section"
    assert "<footer>" in html, "landing must include a footer"


def test_landing_page_links_to_discovery_endpoints(anon_client):
    r = anon_client.get("/")
    html = r.text
    assert "/llms.txt" in html
    assert "/openapi.json" in html
    assert "/v1/health" in html


def test_index_html_alias_returns_landing(anon_client):
    r = anon_client.get("/index.html")
    assert r.status_code == 200
    assert "<title>AMI" in r.text


def test_llms_txt_starts_with_ami_header_and_lists_namespace(anon_client):
    r = anon_client.get("/llms.txt")
    assert r.status_code == 200
    text = r.text
    assert text.startswith("# AMI"), "llms.txt must start with '# AMI'"
    assert "ami." in text, "llms.txt must reference the ami.* MCP namespace"
    # A handful of representative tools and endpoints must be listed.
    assert "ami.request_sim_offer" in text
    assert "ami.create_contract" in text
    assert "/v1/sim-requests" in text
    assert "/v1/contracts" in text


def test_llms_txt_content_type_is_plaintext(anon_client):
    r = anon_client.get("/llms.txt")
    assert "text/plain" in r.headers.get("content-type", "")


def test_openapi_json_basic_shape(anon_client):
    r = anon_client.get("/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert body.get("openapi") == "3.1.0"
    assert "info" in body and body["info"].get("title", "").startswith("AMI")
    assert "paths" in body
    assert "/v1/health" in body["paths"]
    assert "/v1/sim-requests" in body["paths"]
    assert "/v1/contracts" in body["paths"]


def test_openapi_json_security_scheme(anon_client):
    body = anon_client.get("/openapi.json").json()
    schemes = body.get("components", {}).get("securitySchemes", {})
    assert "bearerAuth" in schemes
    assert schemes["bearerAuth"]["scheme"] == "bearer"


def test_install_sh_is_a_shell_script(anon_client):
    r = anon_client.get("/install.sh")
    assert r.status_code == 200
    assert r.text.startswith("#!/bin/sh"), "install.sh must start with the sh shebang"
    ctype = r.headers.get("content-type", "")
    assert ctype == "text/x-shellscript; charset=utf-8", (
        f"install.sh must be served as text/x-shellscript, got {ctype}"
    )


def test_favicon_endpoint_is_public(anon_client):
    r = anon_client.get("/favicon.ico")
    # Implementation responds 204 with image/x-icon; just guard "not 401, not 5xx".
    assert r.status_code in (200, 204)
