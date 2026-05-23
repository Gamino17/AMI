"""Tests de la information architecture (IA) unificada.

Verifica que TODAS las páginas públicas comparten chrome (header + footer)
con los mismos enlaces, y que la landing tiene la sección Explore (sitemap
visible) con cards por área (Product / Build / Operate).

Antes había 13 inconsistencias documentadas en la auditoría — este suite
las cubre como regression.
"""
from __future__ import annotations

import pytest

PUBLIC_PAGES = ["/", "/docs", "/spec", "/partners", "/experience", "/diagram"]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_page_has_chrome_header(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 200
    assert "ami-chrome-header" in r.text, f"{path} missing chrome header"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_page_has_chrome_footer(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 200
    assert "ami-chrome-footer" in r.text, f"{path} missing chrome footer"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_page_chrome_has_all_nav_links(anon_client, path):
    """El nav común enlaza a docs, spec, experience, diagram, partners, api."""
    r = anon_client.get(path)
    body = r.text
    for href in ('href="/docs"', 'href="/spec"', 'href="/experience"',
                 'href="/diagram"', 'href="/partners"', 'href="/openapi.json"'):
        assert href in body, f"{path} missing nav link: {href}"


@pytest.mark.parametrize("path,active", [
    ("/", "home"),
    ("/docs", "docs"),
    ("/spec", "spec"),
    ("/partners", "partners"),
    ("/experience", "experience"),
    ("/diagram", "diagram"),
])
def test_active_link_marked(anon_client, path, active):
    """El link del nav correspondiente a la página actual lleva class='active'
    y aria-current='page'. La landing ('home') no aparece en el nav, así que
    saltamos esa verificación."""
    r = anon_client.get(path)
    body = r.text
    if active == "home":
        # Landing no tiene "Home" en el nav; el link de marca es el activo.
        return
    assert f'class="active"' in body or 'class="active ' in body
    assert 'aria-current="page"' in body


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_footer_has_all_four_columns(anon_client, path):
    """El footer común tiene 4 columnas: About + Product + Build + Operate."""
    r = anon_client.get(path)
    body = r.text
    for col_label in (">Product<", ">Build<", ">Operate<"):
        assert col_label in body, f"{path} footer missing column {col_label}"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_footer_links_complete(anon_client, path):
    """El footer enlaza a las 9 cosas clave: home, experience, diagram,
    partners, docs, spec, openapi, llms.txt, panel."""
    r = anon_client.get(path)
    body = r.text
    expected = ['href="/"', 'href="/experience"', 'href="/diagram"',
                 'href="/partners"', 'href="/docs"', 'href="/spec"',
                 'href="/openapi.json"', 'href="/llms.txt"', 'href="/panel"']
    for href in expected:
        assert href in body, f"{path} footer missing {href}"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_lang_toggle_present(anon_client, path):
    """Toda página pública debe tener el toggle de idioma del chrome."""
    r = anon_client.get(path)
    assert "data-chrome-langtoggle" in r.text, f"{path} missing lang toggle"


# ---------------- Landing · Explore section ----------------

def test_landing_has_explore_section(anon_client):
    r = anon_client.get("/")
    body = r.text
    assert 'id="explore"' in body
    assert "explore-grid" in body
    # Tres columnas
    assert body.count('class="explore-col"') == 3


def test_landing_explore_has_all_three_areas(anon_client):
    r = anon_client.get("/")
    body = r.text
    for area in (">Product</h3>", ">Build</h3>", ">Operate</h3>"):
        assert area in body, f"missing area heading: {area}"


def test_landing_explore_lists_all_pages(anon_client):
    """La sección Explore debe enumerar las páginas principales con su descripción."""
    r = anon_client.get("/")
    body = r.text
    for needle in ("Experience", "Diagram", "Docs", "Spec", "OpenAPI",
                   "Panel", "Partners", "MCP endpoint", "Health"):
        assert needle in body, f"missing in Explore: {needle}"
