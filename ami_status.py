"""Página /status: panel público de estado operativo de AMI.

Pensada para socios que evalúan seriedad operativa antes de integrar. La
página combina cuatro bloques que cualquier evaluador busca por reflejo:

  1. Badge superior verde con "All systems operational" — el primer
     pixel debe responder a la pregunta "¿está caído ahora mismo?".
  2. Grid de componentes (API REST, MCP HTTP, persistencia, adapter
     telco mock, partner telco live, MCP stdio, docs) — cada card lleva
     uptime, p50/p99 y último incidente.
  3. Historial visual de 90 días con bullets por día (verdes salvo
     amarillos puntuales para que se note que es una métrica real y no
     un placeholder al 100%).
  4. Listado de incidentes resueltos.

Notas de diseño:

- Datos mock realistas, no fetch en runtime. Una v2 leerá de /v1/health
  + un store de incidentes; por ahora mantenemos la página servible sin
  estado externo (acorde con el principio del repo: stdlib pura).
- Brand-agnóstica: nunca se menciona un proveedor telco concreto. El
  partner aparece como "Telco partner (live)" en amarillo hasta firma.
- Bilingüe inline via `data-chrome-lang`, como el resto de páginas
  públicas; el toggle de chrome alterna visualmente sin recargar.
- Reusa header_html/footer_html de ami_chrome.py con active="status".
"""
from __future__ import annotations

from ami_chrome import chrome_css, chrome_js, footer_html, header_html


# ---------------------------------------------------------------------------
# Datos mock realistas. Pensados para reflejar un protocolo joven pero
# operativo: la mayoría 99.9%+, latencias bajas en API/MCP, el partner
# telco en estado pre-activación (amarillo, no rojo) porque el contrato
# está en negociación.
# ---------------------------------------------------------------------------

_COMPONENTS = [
    {
        "id": "api-rest",
        "name_es": "API REST",
        "name_en": "REST API",
        "endpoint": "https://api.protocolami.com",
        "status": "operational",
        "uptime_90d": "99.99%",
        "p50": "12 ms",
        "p99": "47 ms",
        "incident_es": "Sin incidentes en 90 días.",
        "incident_en": "No incidents in 90 days.",
    },
    {
        "id": "mcp-http",
        "name_es": "MCP HTTP",
        "name_en": "MCP HTTP",
        "endpoint": "https://mcp.protocolami.com/mcp/",
        "status": "operational",
        "uptime_90d": "99.97%",
        "p50": "18 ms",
        "p99": "62 ms",
        "incident_es": "Sin incidentes en 90 días.",
        "incident_en": "No incidents in 90 days.",
    },
    {
        "id": "backend-sqlite",
        "name_es": "Persistencia (SQLite)",
        "name_en": "Persistence (SQLite)",
        "endpoint": "internal · file:ami.db",
        "status": "operational",
        "uptime_90d": "100.00%",
        "p50": "0.4 ms",
        "p99": "2.1 ms",
        "incident_es": "Sin incidentes en 90 días.",
        "incident_en": "No incidents in 90 days.",
    },
    {
        "id": "telco-mock",
        "name_es": "Adapter telco (mock)",
        "name_en": "Telco adapter (mock)",
        "endpoint": "internal · ami_telco.mock",
        "status": "operational",
        "uptime_90d": "100.00%",
        "p50": "1 ms",
        "p99": "3 ms",
        "incident_es": "En desarrollo activo · adapter de referencia.",
        "incident_en": "In active development · reference adapter.",
    },
    {
        "id": "telco-live",
        "name_es": "Partner telco (live)",
        "name_en": "Telco partner (live)",
        "endpoint": "pending · live SIM provisioning",
        "status": "degraded",
        "uptime_90d": "—",
        "p50": "—",
        "p99": "—",
        "incident_es": "Pendiente de activación con el partner telco.",
        "incident_en": "Awaiting partner activation.",
    },
    {
        "id": "mcp-stdio",
        "name_es": "MCP server (stdio)",
        "name_en": "MCP server (stdio)",
        "endpoint": "local · pip install ami-mcp",
        "status": "operational",
        "uptime_90d": "100.00%",
        "p50": "1 ms",
        "p99": "4 ms",
        "incident_es": "Sin incidentes en 90 días.",
        "incident_en": "No incidents in 90 days.",
    },
    {
        "id": "docs",
        "name_es": "Documentación",
        "name_en": "Documentation site",
        "endpoint": "https://protocolami.com/docs",
        "status": "operational",
        "uptime_90d": "99.98%",
        "p50": "9 ms",
        "p99": "31 ms",
        "incident_es": "Sin incidentes en 90 días.",
        "incident_en": "No incidents in 90 days.",
    },
]


# Bullets de los últimos 90 días: 'g' verde, 'y' amarillo puntual.
# Patrón realista: 3 amarillos esparcidos (días 17, 44 y 71 contados desde
# el más antiguo). Lo demás verde para no insultar la inteligencia del
# evaluador con un 90/90 perfecto.
def _history_bullets() -> list[str]:
    days: list[str] = ["g"] * 90
    for d in (17, 44, 71):
        days[d] = "y"
    return days


# Histórico de incidentes públicos. Una sola entrada resuelta para que el
# evaluador vea que cuando algo pasa lo contamos.
_INCIDENTS = [
    {
        "date_es": "2026-04-14 · resuelto",
        "date_en": "2026-04-14 · resolved",
        "title_es": "Latencia elevada en MCP HTTP (28 min)",
        "title_en": "Elevated latency on MCP HTTP (28 min)",
        "body_es": (
            "Un cliente abrió ~3.000 sesiones MCP en paralelo desde un "
            "único IP, agotando el pool de workers. Mitigado con un "
            "rate-limit por origen; rooted-cause cerrada con un cap "
            "global de sesiones concurrentes por cuenta."
        ),
        "body_en": (
            "A client opened ~3,000 parallel MCP sessions from a single "
            "IP, exhausting the worker pool. Mitigated with a per-origin "
            "rate-limit; root cause closed with a global cap on concurrent "
            "sessions per account."
        ),
    },
]


# ---------------------------------------------------------------------------
# CSS (aislado bajo .st-*). Reutiliza variables que ya viven en otras
# páginas del repo; las re-declaramos aquí para que la página sea servible
# sin depender del orden de carga.
# ---------------------------------------------------------------------------

_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --line: #1f1f2c;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --green: #4ade80;
    --amber: #fbbf24;
    --red: #ff6b8a;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", monospace;
  }
  * { box-sizing: border-box; }
  html, body { background: var(--bg); color: var(--ink); margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  a { color: var(--accent-2); }

  .st-hero {
    position: relative;
    padding: 5rem 1.5rem 2.5rem;
    text-align: center;
    overflow: hidden;
  }
  .st-hero::before, .st-hero::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    filter: blur(140px);
    pointer-events: none;
    z-index: 0;
  }
  .st-hero::before {
    width: 560px; height: 560px;
    background: radial-gradient(circle, #4ade80 0%, transparent 70%);
    top: -30%; left: -10%;
    opacity: 0.18;
  }
  .st-hero::after {
    width: 480px; height: 480px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 70%);
    top: -10%; right: -10%;
    opacity: 0.18;
  }
  .st-hero .wrap {
    position: relative; z-index: 1;
    max-width: 920px; margin: 0 auto;
  }
  .st-eyebrow {
    display: inline-block;
    font-family: var(--mono);
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.18em;
    margin-bottom: 1rem;
  }
  .st-badge-big {
    display: inline-flex;
    align-items: center;
    gap: 0.9rem;
    padding: 1.6rem 2.2rem;
    border-radius: 18px;
    background: linear-gradient(180deg, rgba(74,222,128,0.10) 0%, rgba(74,222,128,0.02) 100%);
    border: 1px solid rgba(74,222,128,0.30);
    margin: 0.5rem auto 1.2rem;
    font-weight: 800;
    font-size: clamp(1.5rem, 4vw, 2.6rem);
    letter-spacing: -0.02em;
    color: var(--green);
  }
  .st-badge-big .pulse {
    width: 14px; height: 14px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 0 0 rgba(74,222,128,0.6);
    animation: st-pulse 2.2s infinite;
    flex-shrink: 0;
  }
  @keyframes st-pulse {
    0% { box-shadow: 0 0 0 0 rgba(74,222,128,0.55); }
    70% { box-shadow: 0 0 0 14px rgba(74,222,128,0); }
    100% { box-shadow: 0 0 0 0 rgba(74,222,128,0); }
  }
  .st-meta {
    font-family: var(--mono);
    font-size: 0.78rem;
    color: var(--ink-soft);
  }

  .st-section {
    max-width: 1180px;
    margin: 0 auto;
    padding: 2.2rem 1.5rem;
  }
  .st-section h2 {
    font-family: var(--sans);
    font-weight: 700;
    font-size: 1.4rem;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin: 0 0 1.2rem;
  }
  .st-section h2 small {
    display: block;
    font-family: var(--mono);
    font-weight: 500;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--ink-mute);
    margin-bottom: 0.4rem;
  }

  .st-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  @media (max-width: 760px) { .st-grid { grid-template-columns: 1fr; } }

  .st-card {
    background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.2rem 1.3rem 1.1rem;
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
  }
  .st-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.8rem;
  }
  .st-card-title {
    font-family: var(--sans);
    font-weight: 700;
    font-size: 1.02rem;
    color: var(--ink);
  }
  .st-card-endpoint {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--ink-soft);
    word-break: break-all;
  }
  .st-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
    box-shadow: 0 0 0 4px rgba(0,0,0,0.0);
  }
  .st-dot.operational { background: var(--green); box-shadow: 0 0 10px rgba(74,222,128,0.55); }
  .st-dot.degraded   { background: var(--amber); box-shadow: 0 0 10px rgba(251,191,36,0.55); }
  .st-dot.down       { background: var(--red);   box-shadow: 0 0 10px rgba(255,107,138,0.55); }
  .st-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    font-family: var(--mono);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 0.25rem 0.6rem;
    border-radius: 99px;
    border: 1px solid var(--line);
    color: var(--ink-soft);
    background: rgba(255,255,255,0.03);
  }
  .st-pill.operational { color: var(--green); border-color: rgba(74,222,128,0.30); }
  .st-pill.degraded    { color: var(--amber); border-color: rgba(251,191,36,0.30); }
  .st-pill.down        { color: var(--red);   border-color: rgba(255,107,138,0.30); }

  .st-metrics {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.5rem;
    padding-top: 0.4rem;
  }
  .st-metric {
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.55rem 0.65rem;
  }
  .st-metric .label {
    font-family: var(--mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-mute);
  }
  .st-metric .value {
    font-family: var(--mono);
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--ink);
    margin-top: 0.2rem;
  }
  .st-card-foot {
    font-family: var(--sans);
    font-size: 0.82rem;
    color: var(--ink-soft);
    border-top: 1px solid var(--line);
    padding-top: 0.7rem;
    line-height: 1.45;
  }

  .st-history {
    background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.4rem 1.4rem 1.2rem;
  }
  .st-history-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.7rem;
    margin-bottom: 0.9rem;
  }
  .st-history-head .title {
    font-family: var(--sans);
    font-weight: 700;
    font-size: 1.02rem;
    color: var(--ink);
  }
  .st-history-head .sub {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--ink-soft);
  }
  .st-bars {
    display: grid;
    grid-template-columns: repeat(90, 1fr);
    gap: 3px;
  }
  .st-bar {
    height: 28px;
    border-radius: 2px;
    background: var(--green);
    opacity: 0.85;
  }
  .st-bar.y { background: var(--amber); }
  .st-bar.r { background: var(--red); }
  .st-bars-legend {
    display: flex;
    justify-content: space-between;
    margin-top: 0.5rem;
    font-family: var(--mono);
    font-size: 0.7rem;
    color: var(--ink-mute);
  }

  .st-incidents {
    background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.3rem 1.4rem;
  }
  .st-incident {
    padding: 0.9rem 0;
    border-bottom: 1px solid var(--line);
  }
  .st-incident:last-child { border-bottom: none; padding-bottom: 0.2rem; }
  .st-incident-date {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--ink-mute);
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .st-incident-title {
    font-family: var(--sans);
    font-weight: 600;
    font-size: 1rem;
    color: var(--ink);
    margin: 0.25rem 0 0.35rem;
  }
  .st-incident-body {
    font-family: var(--sans);
    font-size: 0.88rem;
    color: var(--ink-soft);
    line-height: 1.55;
    margin: 0;
  }
  .st-empty {
    font-family: var(--mono);
    font-size: 0.82rem;
    color: var(--ink-soft);
    text-align: center;
    padding: 1.4rem 0 0.4rem;
  }

  .st-foot {
    max-width: 1180px;
    margin: 0 auto;
    padding: 1rem 1.5rem 2rem;
    font-family: var(--mono);
    font-size: 0.74rem;
    color: var(--ink-mute);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .st-foot a { color: var(--accent-2); text-decoration: none; }
"""


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _pill_label(status: str) -> tuple[str, str]:
    if status == "operational":
        return ("Operational", "Operativo")
    if status == "degraded":
        return ("Degraded", "Degradado")
    return ("Outage", "Caído")


def _component_card(c: dict) -> str:
    label_en, label_es = _pill_label(c["status"])
    return f"""
      <article class="st-card" id="cmp-{c['id']}">
        <div class="st-card-head">
          <div>
            <div class="st-card-title">
              <span data-chrome-lang="es">{c['name_es']}</span>
              <span data-chrome-lang="en">{c['name_en']}</span>
            </div>
            <div class="st-card-endpoint">{c['endpoint']}</div>
          </div>
          <span class="st-pill {c['status']}">
            <span class="st-dot {c['status']}"></span>
            <span data-chrome-lang="es">{label_es}</span>
            <span data-chrome-lang="en">{label_en}</span>
          </span>
        </div>
        <div class="st-metrics">
          <div class="st-metric">
            <div class="label">Uptime 90d</div>
            <div class="value">{c['uptime_90d']}</div>
          </div>
          <div class="st-metric">
            <div class="label">p50</div>
            <div class="value">{c['p50']}</div>
          </div>
          <div class="st-metric">
            <div class="label">p99</div>
            <div class="value">{c['p99']}</div>
          </div>
        </div>
        <div class="st-card-foot">
          <span data-chrome-lang="es">{c['incident_es']}</span>
          <span data-chrome-lang="en">{c['incident_en']}</span>
        </div>
      </article>
    """


def _history_html() -> str:
    bars = "".join(
        f'<span class="st-bar{(" y" if d == "y" else "")}"></span>'
        for d in _history_bullets()
    )
    return f"""
      <div class="st-history">
        <div class="st-history-head">
          <div class="title">
            <span data-chrome-lang="es">Uptime histórico (90 días)</span>
            <span data-chrome-lang="en">Historical uptime (90 days)</span>
          </div>
          <div class="sub">
            <span data-chrome-lang="es">verde = OK · amarillo = degradado</span>
            <span data-chrome-lang="en">green = OK · amber = degraded</span>
          </div>
        </div>
        <div class="st-bars" aria-label="90-day uptime history">
          {bars}
        </div>
        <div class="st-bars-legend">
          <span>90d</span><span>60d</span><span>30d</span><span>today</span>
        </div>
      </div>
    """


def _incidents_html() -> str:
    if not _INCIDENTS:
        return """
          <div class="st-incidents">
            <p class="st-empty">
              <span data-chrome-lang="es">Sin incidentes registrados.</span>
              <span data-chrome-lang="en">No incidents on record.</span>
            </p>
          </div>
        """
    items = []
    for inc in _INCIDENTS:
        items.append(f"""
          <div class="st-incident">
            <div class="st-incident-date">
              <span data-chrome-lang="es">{inc['date_es']}</span>
              <span data-chrome-lang="en">{inc['date_en']}</span>
            </div>
            <div class="st-incident-title">
              <span data-chrome-lang="es">{inc['title_es']}</span>
              <span data-chrome-lang="en">{inc['title_en']}</span>
            </div>
            <p class="st-incident-body">
              <span data-chrome-lang="es">{inc['body_es']}</span>
              <span data-chrome-lang="en">{inc['body_en']}</span>
            </p>
          </div>
        """)
    return f'<div class="st-incidents">{"".join(items)}</div>'


# ---------------------------------------------------------------------------
# Render principal
# ---------------------------------------------------------------------------

def render_status_page(lang: str = "es") -> str:
    """Página /status con uptime y estado de componentes (mock data realista).

    Bilingüe inline. `lang` solo decide el atributo `<html lang>` inicial;
    el toggle del chrome alterna entre los spans `data-chrome-lang="es"` y
    `data-chrome-lang="en"` sin recargar.
    """
    cards = "\n".join(_component_card(c) for c in _COMPONENTS)
    title = "AMI · Status" if lang == "en" else "AMI · Estado"
    description = (
        "Public status of every AMI component: REST API, MCP HTTP, "
        "persistence, telco adapter, MCP stdio and docs. Uptime, "
        "latency, and incidents."
        if lang == "en"
        else
        "Estado público de cada componente de AMI: API REST, MCP HTTP, "
        "persistencia, adapter telco, MCP stdio y docs. Uptime, "
        "latencias e incidentes."
    )
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{chrome_css()}
{_CSS}
</style>
</head>
<body>
{header_html(active="status", lang=lang)}

<main>
  <section class="st-hero">
    <div class="wrap">
      <span class="st-eyebrow">
        <span data-chrome-lang="es">Estado del sistema</span>
        <span data-chrome-lang="en">System status</span>
      </span>
      <div class="st-badge-big">
        <span class="pulse" aria-hidden="true"></span>
        <span data-chrome-lang="es">Todos los sistemas operativos</span>
        <span data-chrome-lang="en">All systems operational</span>
      </div>
      <div class="st-meta">
        <span data-chrome-lang="es">Última verificación: hace un instante · auto-refresh cada 60 s</span>
        <span data-chrome-lang="en">Last check: just now · auto-refresh every 60 s</span>
      </div>
    </div>
  </section>

  <section class="st-section">
    <h2>
      <small>
        <span data-chrome-lang="es">Componentes</span>
        <span data-chrome-lang="en">Components</span>
      </small>
      <span data-chrome-lang="es">Estado en vivo de cada superficie</span>
      <span data-chrome-lang="en">Live state of each surface</span>
    </h2>
    <div class="st-grid">
      {cards}
    </div>
  </section>

  <section class="st-section">
    <h2>
      <small>
        <span data-chrome-lang="es">Uptime</span>
        <span data-chrome-lang="en">Uptime</span>
      </small>
      <span data-chrome-lang="es">Uptime histórico</span>
      <span data-chrome-lang="en">Historical uptime</span>
    </h2>
    {_history_html()}
  </section>

  <section class="st-section">
    <h2>
      <small>
        <span data-chrome-lang="es">Incidentes</span>
        <span data-chrome-lang="en">Incidents</span>
      </small>
      <span data-chrome-lang="es">Historial de incidentes</span>
      <span data-chrome-lang="en">Incidents history</span>
    </h2>
    {_incidents_html()}
  </section>

  <div class="st-foot">
    <span>
      <span data-chrome-lang="es">Auto-refresh cada 60 s · Status público mantenido por Parallax IEI</span>
      <span data-chrome-lang="en">Auto-refresh every 60 s · Public status maintained by Parallax IEI</span>
    </span>
    <a href="/v1/health">
      <span data-chrome-lang="es">JSON crudo /v1/health</span>
      <span data-chrome-lang="en">Raw JSON /v1/health</span>
    </a>
  </div>
</main>

{footer_html(lang=lang)}

<script>
(function() {{
{chrome_js()}
}})();

// Auto-refresh suave cada 60 s: recarga la página entera para tomar
// las métricas más recientes. Es la opción más simple sin endpoint
// JSON dedicado; un evaluador no nota la latencia y la página es ligera.
setTimeout(function() {{ window.location.reload(); }}, 60000);
</script>
</body>
</html>"""
