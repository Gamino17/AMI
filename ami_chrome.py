"""Chrome común (header + footer) reutilizable en todas las páginas HTML.

Antes cada render_*_page tenía su propio <header> con un nav distinto:
unas linkeaban a /docs, otras no; unas mostraban Partners, otras solo Spec;
los footers tenían entre 1 y 6 enlaces y a veces eran monolingües aunque
la página fuera bilingüe. La auditoría de IA listó 13 inconsistencias.

Esta tanda centraliza en un solo sitio:
  - `header_html(active, lang)` — nav superior con el mismo set de enlaces
    en toda página pública. `active` es el id de la página actual y se usa
    para marcar el link como `.active` (CSS) y `aria-current="page"`.
  - `footer_html(lang)` — footer con 3 columnas: Product / Build / Operate.
  - `chrome_css()` — el CSS mínimo para que el chrome se vea bien;
    cada página lo embebe junto a su CSS local.

Páginas como /panel, /panel/login, /v1/sign/{id}, /v1/identity/{mid}
son "specific-purpose" y NO usan este chrome — tienen su propia nav
minimal porque el flujo del usuario es distinto (autenticación, firma).
"""
from __future__ import annotations
import os


REPO_URL = os.environ.get("AMI_REPO_URL", "https://github.com/Gamino17/AMI")
MCP_HTTP_URL = os.environ.get("AMI_MCP_HTTP_URL", "https://mcp.protocolami.com/mcp/")


# Los items del nav. Cada uno tiene id (para marcar active), href, label.
# El orden importa: aparece de izquierda a derecha en el header.
_NAV_ITEMS = [
    ("docs",       "/docs",        "Docs",       "Docs"),
    ("spec",       "/spec",        "Spec",       "Spec"),
    ("experience", "/experience",  "Experience", "Experience"),
    ("diagram",    "/diagram",     "Diagram",    "Diagram"),
    ("partners",   "/partners",    "Partners",   "Partners"),
    ("api",        "/openapi.json","API",        "API"),
    ("github",     REPO_URL,       "GitHub",     "GitHub"),
]


def chrome_css() -> str:
    """CSS aislado bajo .ami-chrome-* para no chocar con CSS local de cada
    página. Reutilizable en cualquier render."""
    return """
      .ami-chrome-header {
        position: sticky; top: 0; z-index: 100;
        background: rgba(6,6,10,0.85);
        backdrop-filter: saturate(140%) blur(12px);
        -webkit-backdrop-filter: saturate(140%) blur(12px);
        border-bottom: 1px solid rgba(255,255,255,0.06);
        font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
      }
      .ami-chrome-header .wrap {
        max-width: 1400px; margin: 0 auto;
        padding: 0.9rem 1.5rem;
        display: flex; align-items: center; justify-content: space-between;
        gap: 1rem;
      }
      .ami-chrome-brand {
        font-family: "JetBrains Mono", "SF Mono", monospace;
        font-weight: 700; font-size: 1rem; letter-spacing: 0.01em;
        color: #ededf2; text-decoration: none;
        display: flex; align-items: center; gap: 0.5rem;
      }
      .ami-chrome-brand .dot { color: #8b6cff; }
      .ami-chrome-nav {
        display: flex; align-items: center; gap: 1.1rem;
        flex-wrap: wrap;
      }
      .ami-chrome-nav a {
        color: #8888a0; font-family: "JetBrains Mono", monospace;
        font-size: 0.78rem; text-decoration: none;
        padding: 0.25rem 0;
        border-bottom: 1px solid transparent;
      }
      .ami-chrome-nav a:hover { color: #ededf2; }
      .ami-chrome-nav a.active {
        color: #ededf2; border-bottom-color: #8b6cff;
      }
      .ami-chrome-langtoggle {
        background: none; border: 1px solid rgba(255,255,255,0.10);
        color: #8888a0; cursor: pointer;
        font-family: "JetBrains Mono", monospace; font-size: 0.7rem;
        padding: 0.3rem 0.65rem; border-radius: 99px;
      }
      .ami-chrome-langtoggle:hover {
        color: #ededf2; border-color: #8b6cff;
      }
      @media (max-width: 760px) {
        .ami-chrome-nav { gap: 0.7rem; }
        .ami-chrome-nav a { font-size: 0.72rem; }
        .ami-chrome-nav .ami-chrome-nav-secondary { display: none; }
      }

      .ami-chrome-footer {
        margin-top: 4rem;
        border-top: 1px solid rgba(255,255,255,0.06);
        background: #06060a;
        font-family: "Inter", sans-serif;
        color: #8888a0;
      }
      .ami-chrome-footer .wrap {
        max-width: 1400px; margin: 0 auto;
        padding: 2.5rem 1.5rem 2rem;
        display: grid;
        grid-template-columns: 1.4fr 1fr 1fr 1fr;
        gap: 2rem;
      }
      @media (max-width: 760px) {
        .ami-chrome-footer .wrap {
          grid-template-columns: 1fr 1fr;
        }
      }
      .ami-chrome-footer h5 {
        font-family: "JetBrains Mono", monospace;
        font-size: 0.7rem; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.16em;
        color: #5a5a70;
        margin: 0 0 0.85rem;
      }
      .ami-chrome-footer ul {
        list-style: none; padding: 0; margin: 0;
      }
      .ami-chrome-footer li { margin-bottom: 0.45rem; }
      .ami-chrome-footer a {
        color: #8888a0; text-decoration: none;
        font-family: "JetBrains Mono", monospace; font-size: 0.78rem;
      }
      .ami-chrome-footer a:hover { color: #ededf2; }
      .ami-chrome-footer .ami-chrome-about {
        font-family: "Inter", sans-serif; font-size: 0.85rem;
        line-height: 1.55;
      }
      .ami-chrome-footer .ami-chrome-about .brand {
        font-family: "JetBrains Mono", monospace; font-weight: 700;
        font-size: 1rem; color: #ededf2;
        display: block; margin-bottom: 0.5rem;
      }
      .ami-chrome-footer .ami-chrome-about .brand .dot { color: #8b6cff; }
      .ami-chrome-footer .ami-chrome-legal {
        max-width: 1400px; margin: 0 auto;
        padding: 1rem 1.5rem 1.5rem;
        border-top: 1px solid rgba(255,255,255,0.04);
        display: flex; justify-content: space-between; align-items: center;
        flex-wrap: wrap; gap: 0.5rem;
        font-family: "JetBrains Mono", monospace; font-size: 0.7rem;
        color: #5a5a70;
      }
      .ami-chrome-footer .ami-chrome-legal a { color: #5a5a70; font-size: 0.7rem; }
      .ami-chrome-footer .ami-chrome-legal a:hover { color: #8888a0; }

      /* Idioma toggle (compat con páginas que usan data-lang inline) */
      html[lang="es"] [data-chrome-lang="en"] { display: none; }
      html[lang="en"] [data-chrome-lang="es"] { display: none; }
    """


def header_html(active: str = "", lang: str = "es") -> str:
    """Header común. `active` = id de la página actual (docs, spec, etc.)."""
    items = []
    for nid, href, label_es, label_en in _NAV_ITEMS:
        cls = "active" if nid == active else ""
        aria = ' aria-current="page"' if nid == active else ""
        # Marcar como secondary los menos críticos para que se oculten en móvil
        secondary = ' ami-chrome-nav-secondary' if nid in ("api", "github") else ''
        items.append(
            f'<a href="{href}" class="{cls}{secondary}"{aria}>'
            f'<span data-chrome-lang="es">{label_es}</span>'
            f'<span data-chrome-lang="en">{label_en}</span></a>'
        )
    nav = "\n".join(items)
    return f"""
      <header class="ami-chrome-header">
        <div class="wrap">
          <a href="/" class="ami-chrome-brand">AMI<span class="dot">.</span></a>
          <nav class="ami-chrome-nav">
            {nav}
            <button class="ami-chrome-langtoggle" type="button" data-chrome-langtoggle aria-label="Toggle language">EN</button>
          </nav>
        </div>
      </header>
    """


def footer_html(lang: str = "es") -> str:
    """Footer común con 4 columnas: About + Product + Build + Operate."""
    return f"""
      <footer class="ami-chrome-footer">
        <div class="wrap">
          <div class="ami-chrome-about">
            <span class="brand">AMI<span class="dot">.</span></span>
            <span data-chrome-lang="es">Protocolo abierto para que un agente AI obtenga su propia identidad móvil — número real, SMS, voz — de forma autónoma, programable y auditable.</span>
            <span data-chrome-lang="en">Open protocol for an AI agent to obtain its own mobile identity — real number, SMS, voice — autonomously, programmatically, and auditably.</span>
          </div>
          <div>
            <h5>Product</h5>
            <ul>
              <li><a href="/"><span data-chrome-lang="es">Inicio</span><span data-chrome-lang="en">Home</span></a></li>
              <li><a href="/experience">Experience</a></li>
              <li><a href="/diagram">Diagram</a></li>
              <li><a href="/partners">Partners</a></li>
            </ul>
          </div>
          <div>
            <h5>Build</h5>
            <ul>
              <li><a href="/docs">Docs</a></li>
              <li><a href="/spec">Spec</a></li>
              <li><a href="/openapi.json">OpenAPI</a></li>
              <li><a href="/llms.txt">llms.txt</a></li>
              <li><a href="/install.sh">Install</a></li>
              <li><a href="{REPO_URL}">GitHub</a></li>
            </ul>
          </div>
          <div>
            <h5>Operate</h5>
            <ul>
              <li><a href="/panel">Panel</a></li>
              <li><a href="{MCP_HTTP_URL}">MCP endpoint</a></li>
              <li><a href="/v1/health">Status</a></li>
              <li><a href="/metrics">Metrics</a></li>
            </ul>
          </div>
        </div>
        <div class="ami-chrome-legal">
          <span>Parallax IEI · AMI v1.0 · <span data-chrome-lang="es">Licencia Apache 2.0</span><span data-chrome-lang="en">Apache 2.0 License</span></span>
          <a href="{REPO_URL}/blob/main/LICENSE">LICENSE</a>
        </div>
      </footer>
    """


def chrome_js() -> str:
    """JS común para el toggle de idioma. Idempotente: detecta si la página
    ya tiene un toggle existente (landing tiene `#langToggle`, docs tiene
    `#langToggle`) y respeta esa instancia; en el resto añade el binding."""
    return """
      (function() {
        // Sync inicial: leer lang del localStorage si la página no lo trae
        try {
          var l = localStorage.getItem('ami-lang');
          if (l === 'es' || l === 'en') document.documentElement.lang = l;
        } catch(e) {}

        function persistAndReload(next) {
          try { localStorage.setItem('ami-lang', next); } catch(e) {}
          document.cookie = 'ami-lang=' + next + '; path=/; max-age=' + (60*60*24*365);
          var url = new URL(window.location.href);
          url.searchParams.set('lang', next);
          window.location.href = url.toString();
        }

        document.querySelectorAll('[data-chrome-langtoggle]').forEach(function(btn) {
          function sync() {
            var cur = document.documentElement.lang || 'es';
            btn.textContent = cur === 'es' ? 'EN' : 'ES';
          }
          btn.addEventListener('click', function() {
            var next = (document.documentElement.lang === 'es') ? 'en' : 'es';
            persistAndReload(next);
          });
          sync();
        });
      })();
    """
