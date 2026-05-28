"""Guardrail: el brief privado de Daniel NUNCA debe filtrarse a páginas
públicas, y solo debe ser accesible con auth admin.

Si alguien re-introduce el contenido del brief en /poc-co o /poc-co/sip
sin querer (copy/paste por accidente), este test falla.
"""
from __future__ import annotations
import os


_BRIEF_PHRASES = [
    "Banderas rojas",
    "Brief privado",
    "Brief para ti",
    "haz que se comprometa",
    "Estructura sugerida de la conversación",
    "Frase de cierre técnica",
    "necesitamos un PoP en Colombia",
]


def test_public_poc_co_does_not_leak_brief(anon_client):
    r = anon_client.get("/poc-co")
    assert r.status_code == 200
    for phrase in _BRIEF_PHRASES:
        assert phrase not in r.text, f"/poc-co leakea brief privado: {phrase!r}"


def test_public_poc_co_sip_does_not_leak_brief(anon_client):
    r = anon_client.get("/poc-co/sip")
    assert r.status_code == 200
    for phrase in _BRIEF_PHRASES:
        assert phrase not in r.text, f"/poc-co/sip leakea brief privado: {phrase!r}"


def test_internal_brief_requires_admin(anon_client):
    r = anon_client.get("/internal/brief-co")
    assert r.status_code == 401


def test_internal_brief_rejects_bad_key(anon_client, ami_api_module, monkeypatch):
    monkeypatch.setenv("AMI_ADMIN_KEY", "correct_brief_key")
    ami_api_module.ADMIN_KEY = "correct_brief_key"
    r = anon_client.get("/internal/brief-co?key=wrong")
    assert r.status_code == 401


def test_internal_brief_accepts_query_key(anon_client, ami_api_module, monkeypatch):
    monkeypatch.setenv("AMI_ADMIN_KEY", "correct_brief_key")
    ami_api_module.ADMIN_KEY = "correct_brief_key"
    r = anon_client.get("/internal/brief-co?key=correct_brief_key")
    assert r.status_code == 200
    # Y SÍ contiene el brief
    assert "Banderas rojas" in r.text
    assert "Frase de cierre" in r.text


def test_internal_brief_accepts_bearer(server_url, ami_api_module, monkeypatch):
    import httpx
    monkeypatch.setenv("AMI_ADMIN_KEY", "correct_brief_key")
    ami_api_module.ADMIN_KEY = "correct_brief_key"
    with httpx.Client(base_url=server_url,
                      headers={"Authorization": "Bearer correct_brief_key"},
                      timeout=5) as c:
        r = c.get("/internal/brief-co")
    assert r.status_code == 200


def test_internal_brief_has_noindex_meta(anon_client, ami_api_module, monkeypatch):
    monkeypatch.setenv("AMI_ADMIN_KEY", "k")
    ami_api_module.ADMIN_KEY = "k"
    r = anon_client.get("/internal/brief-co?key=k")
    assert r.status_code == 200
    assert 'name="robots"' in r.text and "noindex" in r.text
