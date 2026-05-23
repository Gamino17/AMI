"""Portal de documentación interactivo en /docs.

Single-page server-rendered con:
  - Sidebar persistente con todas las secciones (search-as-you-type)
  - Tabs de código por sección (curl / Python SDK / TypeScript SDK / MCP)
  - "Try it" buttons que ejecutan requests reales contra el backend
    (usando /v1/demo/quick que es público)
  - Bilingüe ES/EN via lang param

Mantenemos el contenido aquí en lugar de markdown porque queremos:
  1. Bilingüe con bloques inline (no archivos paralelos como SPEC.md/en)
  2. Tabs interactivos y "try-it" buttons que requieren controlar HTML+JS
  3. Cero deps externas: stdlib pura, igual que el resto del backend

Para añadir una sección nueva: añadir entry a SECTIONS con id, title_es/en,
y body_html. El sidebar y el TOC se auto-generan.
"""
from __future__ import annotations

import json as _json


def json_dumps(obj):
    """Serializa compacto JSON para snippets de curl."""
    return _json.dumps(obj, separators=(",", ":"))


# Reusamos la paleta dark del resto de páginas. CSS aislado bajo .docs-app
# para no chocar con la landing si en algún momento se embebe.
_DOCS_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --surface-hover: #1a1a25;
    --line: #1f1f2c;
    --line-soft: #16161f;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-bg: rgba(139,108,255,0.10);
    --green: #4ade80;
    --amber: #fbbf24;
    --red: #ff6b8a;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  html { scroll-behavior: smooth; scroll-padding-top: 72px; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background: var(--bg);
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { color: #b9e6ff; }
  code { font-family: var(--mono); font-size: 0.88em; }

  /* HEADER */
  .docs-header {
    position: sticky; top: 0; z-index: 50;
    background: rgba(6,6,10,0.85);
    backdrop-filter: saturate(140%) blur(12px);
    -webkit-backdrop-filter: saturate(140%) blur(12px);
    border-bottom: 1px solid var(--line);
  }
  .docs-header .wrap {
    max-width: 1400px; margin: 0 auto; padding: 0.9rem 1.5rem;
    display: flex; align-items: center; gap: 1.5rem;
  }
  .docs-brand {
    font-family: var(--mono); font-weight: 700; font-size: 0.95rem;
    letter-spacing: 0.02em; display: flex; align-items: center; gap: 0.5rem;
    color: var(--ink);
  }
  .docs-brand .dot { color: var(--accent); }
  .docs-brand .sep { color: var(--ink-mute); font-weight: 400; margin: 0 0.25rem; }
  .docs-search {
    flex: 1; max-width: 480px;
    position: relative;
  }
  .docs-search input {
    width: 100%;
    background: var(--surface);
    border: 1px solid var(--line);
    color: var(--ink);
    font-family: var(--sans); font-size: 0.85rem;
    padding: 0.5rem 0.85rem 0.5rem 2.2rem;
    border-radius: 6px;
  }
  .docs-search input:focus { outline: none; border-color: var(--accent); }
  .docs-search::before {
    content: "⌕"; position: absolute; left: 0.75rem; top: 50%;
    transform: translateY(-50%); color: var(--ink-mute);
    font-size: 1rem;
  }
  .docs-nav { display: flex; gap: 1.2rem; align-items: center; }
  .docs-nav a, .docs-nav button {
    color: var(--ink-soft); font-family: var(--mono); font-size: 0.78rem;
    background: none; border: 0; cursor: pointer; padding: 0;
  }
  .docs-nav a:hover, .docs-nav button:hover { color: var(--ink); }
  .docs-nav .pill {
    border: 1px solid var(--line); padding: 0.3rem 0.65rem; border-radius: 99px;
  }

  /* LAYOUT */
  .docs-shell {
    max-width: 1400px; margin: 0 auto;
    display: grid; grid-template-columns: 240px 1fr 220px; gap: 2rem;
    padding: 2rem 1.5rem 6rem;
  }
  @media (max-width: 1024px) {
    .docs-shell { grid-template-columns: 220px 1fr; }
    .docs-toc { display: none; }
  }
  @media (max-width: 720px) {
    .docs-shell { grid-template-columns: 1fr; padding: 1rem; }
    .docs-sidebar { display: none; }
  }

  /* SIDEBAR */
  .docs-sidebar {
    position: sticky; top: 88px;
    align-self: start;
    max-height: calc(100vh - 100px);
    overflow-y: auto;
    padding-right: 0.5rem;
  }
  .docs-sidebar h3 {
    font-family: var(--mono); font-size: 0.66rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.14em;
    color: var(--ink-mute); margin: 1.4rem 0 0.5rem;
  }
  .docs-sidebar h3:first-child { margin-top: 0; }
  .docs-sidebar ul { list-style: none; margin: 0; padding: 0; }
  .docs-sidebar li a {
    display: block; padding: 0.35rem 0.6rem;
    font-size: 0.84rem; color: var(--ink-soft);
    border-radius: 4px; line-height: 1.4;
    border-left: 2px solid transparent;
  }
  .docs-sidebar li a:hover {
    color: var(--ink); background: var(--surface);
  }
  .docs-sidebar li a.active {
    color: var(--ink); border-left-color: var(--accent);
    background: var(--accent-bg);
  }
  .docs-sidebar li.hidden { display: none; }

  /* CONTENT */
  .docs-content { min-width: 0; }
  .docs-content > section {
    padding: 2rem 0 3rem;
    border-bottom: 1px solid var(--line-soft);
  }
  .docs-content > section:last-child { border-bottom: 0; }
  .docs-content h1 {
    font-size: 2rem; font-weight: 700; letter-spacing: -0.025em;
    margin: 0 0 0.5rem;
  }
  .docs-content h2 {
    font-size: 1.4rem; font-weight: 700; letter-spacing: -0.02em;
    margin: 2rem 0 0.6rem;
    scroll-margin-top: 80px;
  }
  .docs-content h3 {
    font-size: 1.05rem; font-weight: 600; letter-spacing: -0.01em;
    color: var(--ink); margin: 1.8rem 0 0.5rem;
  }
  .docs-content .lead {
    color: var(--ink-soft); font-size: 1.05rem; margin: 0 0 1.5rem;
  }
  .docs-content p { margin: 0 0 1rem; }
  .docs-content ul, .docs-content ol { padding-left: 1.5rem; margin: 0 0 1rem; }
  .docs-content li { margin-bottom: 0.4rem; }
  .docs-content strong { color: var(--ink); font-weight: 600; }
  .docs-content em { color: var(--accent-2); font-style: normal; }
  .docs-content blockquote {
    margin: 1rem 0; padding: 0.75rem 1rem;
    border-left: 3px solid var(--accent);
    background: var(--accent-bg);
    color: var(--ink-soft);
    border-radius: 0 6px 6px 0;
  }
  .docs-content blockquote strong { color: var(--accent); }
  .docs-content code:not(pre code) {
    background: var(--surface); padding: 0.1em 0.4em;
    border-radius: 4px; color: var(--accent-2);
    border: 1px solid var(--line);
  }
  .docs-content table {
    width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem;
  }
  .docs-content th, .docs-content td {
    text-align: left; padding: 0.6rem 0.7rem;
    border-bottom: 1px solid var(--line);
  }
  .docs-content th {
    font-family: var(--mono); font-size: 0.72rem; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--ink-mute);
  }
  .docs-content td.mono, .docs-content .mono { font-family: var(--mono); font-size: 0.82rem; }

  /* CODE BLOCKS + TABS */
  .docs-code {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 8px;
    margin: 1rem 0 1.5rem;
    overflow: hidden;
  }
  .docs-code-tabs {
    display: flex; gap: 0.2rem;
    border-bottom: 1px solid var(--line);
    background: var(--surface);
    padding: 0.2rem 0.4rem 0;
  }
  .docs-code-tab {
    background: none; border: 0; cursor: pointer;
    color: var(--ink-mute);
    font-family: var(--mono); font-size: 0.74rem;
    padding: 0.55rem 0.9rem;
    border-radius: 6px 6px 0 0;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
  }
  .docs-code-tab:hover { color: var(--ink-soft); }
  .docs-code-tab.active {
    color: var(--accent-2);
    border-bottom-color: var(--accent);
    background: var(--bg-soft);
  }
  .docs-code-panes { position: relative; }
  .docs-code-pane {
    display: none;
    padding: 0;
    margin: 0;
  }
  .docs-code-pane.active { display: block; }
  .docs-code pre {
    margin: 0; padding: 1rem 1.2rem;
    font-family: var(--mono); font-size: 0.82rem; line-height: 1.6;
    color: var(--ink); background: transparent;
    overflow-x: auto;
  }
  .docs-code .copy-btn {
    position: absolute; top: 0.5rem; right: 0.5rem;
    background: var(--surface);
    border: 1px solid var(--line);
    color: var(--ink-soft);
    cursor: pointer; padding: 0.2rem 0.55rem;
    border-radius: 4px; font-family: var(--mono); font-size: 0.7rem;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .docs-code:hover .copy-btn { opacity: 1; }
  .docs-code .copy-btn:hover { color: var(--ink); border-color: var(--accent); }

  /* TRY-IT */
  .docs-tryit {
    margin: 1rem 0 1.5rem;
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 1rem 1.2rem;
  }
  .docs-tryit-row {
    display: flex; align-items: center; gap: 0.7rem;
    margin-bottom: 0.8rem;
  }
  .docs-tryit-label {
    font-family: var(--mono); font-size: 0.72rem; text-transform: uppercase;
    letter-spacing: 0.14em; color: var(--ink-mute);
  }
  .docs-tryit-btn {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; border: 0; cursor: pointer;
    padding: 0.5rem 1rem; border-radius: 6px;
    font-family: var(--mono); font-size: 0.78rem; font-weight: 500;
  }
  .docs-tryit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .docs-tryit-out {
    background: var(--bg); border: 1px solid var(--line);
    border-radius: 6px; padding: 0.8rem;
    font-family: var(--mono); font-size: 0.78rem; line-height: 1.5;
    color: var(--ink-soft);
    max-height: 360px; overflow: auto;
    white-space: pre-wrap; word-break: break-word;
  }
  .docs-tryit-out.hidden { display: none; }
  .docs-tryit-status {
    display: inline-block;
    font-family: var(--mono); font-size: 0.7rem;
    padding: 0.15rem 0.5rem; border-radius: 99px;
    margin-right: 0.6rem;
  }
  .docs-tryit-status.ok { color: var(--green); background: rgba(74,222,128,0.10); }
  .docs-tryit-status.err { color: var(--red); background: rgba(255,107,138,0.10); }

  /* TOC right rail */
  .docs-toc {
    position: sticky; top: 88px;
    align-self: start;
    max-height: calc(100vh - 100px);
    overflow-y: auto;
    padding-left: 0.5rem;
    border-left: 1px solid var(--line);
  }
  .docs-toc h4 {
    font-family: var(--mono); font-size: 0.66rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.14em;
    color: var(--ink-mute); margin: 0 0 0.6rem; padding-left: 0.8rem;
  }
  .docs-toc ul { list-style: none; padding: 0 0 0 0.8rem; margin: 0; }
  .docs-toc li { margin-bottom: 0.25rem; }
  .docs-toc a {
    color: var(--ink-soft); font-size: 0.78rem; line-height: 1.4;
  }
  .docs-toc a:hover { color: var(--ink); }
  .docs-toc a.active { color: var(--accent-2); font-weight: 500; }

  /* LANG bilingual */
  html[lang="es"] [data-lang="en"] { display: none; }
  html[lang="en"] [data-lang="es"] { display: none; }
"""


def _code_block(samples: list[tuple[str, str]]) -> str:
    """Genera un bloque con tabs de código. samples = [(label, code), ...].
    Cada code se HTML-escapa para evitar romper el HTML."""
    import html as _html
    if not samples:
        return ""
    tabs_html = "".join(
        f'<button class="docs-code-tab{" active" if i == 0 else ""}" data-tab="{i}">{_html.escape(label)}</button>'
        for i, (label, _) in enumerate(samples)
    )
    panes_html = "".join(
        f'<div class="docs-code-pane{" active" if i == 0 else ""}" data-pane="{i}"><pre>{_html.escape(code)}</pre></div>'
        for i, (_, code) in enumerate(samples)
    )
    return f"""
      <div class="docs-code">
        <div class="docs-code-tabs">{tabs_html}</div>
        <div class="docs-code-panes">
          {panes_html}
          <button class="copy-btn" data-copy>copy</button>
        </div>
      </div>
    """


def _tryit(endpoint: str, label_es: str, label_en: str, method: str = "POST",
           body: dict | None = None) -> str:
    """Genera un widget try-it que dispara un fetch al endpoint y muestra
    la response. Pensado para endpoints públicos (/v1/demo/quick, /v1/health,
    /openapi.json). Para endpoints autenticados mostraría 401 — el usuario
    pone su API key en una caja de texto si quisiéramos extender."""
    import html as _html, json
    body_json = json.dumps(body or {})
    eid = endpoint.replace("/", "_").replace("?", "").replace("=", "").replace("&", "_")
    return f"""
      <div class="docs-tryit" data-tryit data-endpoint="{_html.escape(endpoint)}"
           data-method="{method}" data-body='{_html.escape(body_json)}'>
        <div class="docs-tryit-row">
          <span class="docs-tryit-label">{_html.escape(method)} {_html.escape(endpoint)}</span>
        </div>
        <div class="docs-tryit-row">
          <button class="docs-tryit-btn" data-tryit-run>
            <span data-lang="es">{_html.escape(label_es)}</span>
            <span data-lang="en">{_html.escape(label_en)}</span>
          </button>
          <span class="docs-tryit-status" data-tryit-status></span>
        </div>
        <div class="docs-tryit-out hidden" data-tryit-out></div>
      </div>
    """


# ====================== CONTENIDO DE LAS SECCIONES ======================

def _sections() -> list[dict]:
    """Lista de secciones del portal. Cada una con id, group, title bilingüe,
    y body_html. El sidebar y el TOC se auto-generan a partir de esto."""
    return [
        # ===== GETTING STARTED =====
        {
            "id": "welcome",
            "group": "getting_started",
            "group_es": "Empezar", "group_en": "Get started",
            "title_es": "Bienvenida", "title_en": "Welcome",
            "body": f"""
              <h1 data-lang="es">AMI — documentación interactiva</h1>
              <h1 data-lang="en">AMI — interactive documentation</h1>
              <p class="lead" data-lang="es">
                AMI es un protocolo abierto que permite a un agente AI obtener
                su propia identidad móvil — un número real con SMS y voz —
                de forma autónoma, programable y auditable.
              </p>
              <p class="lead" data-lang="en">
                AMI is an open protocol that lets an AI agent obtain its own
                mobile identity — a real number with SMS and voice — autonomously,
                programmatically, and auditably.
              </p>

              <h2>TL;DR</h2>
              <p data-lang="es">
                Tu agente llama tres tools MCP (o tres POST REST) y obtiene un
                número de teléfono real en menos de un segundo. Después puede
                enviar SMS y hacer llamadas usando un <em>agent_token</em>
                scoped a ese número. La voz se bridgea a tu propio motor
                Realtime — AMI es la pipa, tu agente es el cerebro.
              </p>
              <p data-lang="en">
                Your agent calls three MCP tools (or three REST POSTs) and gets
                a real phone number in under a second. From there it can send
                SMS and place calls using an <em>agent_token</em> scoped to
                that number. Voice gets SIP-bridged to your own real-time
                engine — AMI is the pipe, your agent is the brain.
              </p>

              <h2 data-lang="es">Mapa del documento</h2>
              <h2 data-lang="en">Document map</h2>
              <ul>
                <li><a href="#quickstart"><span data-lang="es">Quick start</span><span data-lang="en">Quick start</span></a> —
                    <span data-lang="es">de cero a un SMS enviado en 5 líneas</span>
                    <span data-lang="en">from zero to a sent SMS in 5 lines</span></li>
                <li><a href="#concepts"><span data-lang="es">Conceptos</span><span data-lang="en">Concepts</span></a> —
                    <span data-lang="es">MobileIdentity, agent_token, bridge-by-API</span>
                    <span data-lang="en">MobileIdentity, agent_token, bridge-by-API</span></li>
                <li><a href="#auth"><span data-lang="es">Autenticación</span><span data-lang="en">Authentication</span></a> —
                    <span data-lang="es">los dos niveles + master admin</span>
                    <span data-lang="en">the two levels + admin master</span></li>
                <li><a href="#contracting">Contracting</a> · <a href="#sms">SMS</a> · <a href="#voice">Voice</a> ·
                    <a href="#webhooks">Webhooks</a> · <a href="#limits">Limits</a> · <a href="#admin">Admin</a> ·
                    <a href="#errors">Errors</a> · <a href="#sdks">SDKs</a></li>
              </ul>

              <h2 data-lang="es">Otros recursos</h2>
              <h2 data-lang="en">Other resources</h2>
              <ul>
                <li><a href="/spec">/spec</a> — <span data-lang="es">spec técnica completa</span><span data-lang="en">full technical spec</span></li>
                <li><a href="/openapi.json">/openapi.json</a> — OpenAPI 3.1</li>
                <li><a href="/diagram">/diagram</a> — <span data-lang="es">animación del ciclo de vida</span><span data-lang="en">lifecycle animation</span></li>
                <li><a href="/llms.txt">/llms.txt</a> — <span data-lang="es">índice para LLMs</span><span data-lang="en">index for LLMs</span></li>
                <li><a href="/partners">/partners</a> — <span data-lang="es">para operadores telco</span><span data-lang="en">for telco operators</span></li>
              </ul>
            """,
        },
        {
            "id": "quickstart",
            "group": "getting_started",
            "group_es": "Empezar", "group_en": "Get started",
            "title_es": "Quick start", "title_en": "Quick start",
            "body": f"""
              <h1>Quick start</h1>
              <p data-lang="es">
                Obtén un número de teléfono real y envía tu primer SMS con cinco
                líneas de código. Los SDKs oficiales hacen todo el flujo de
                contratación en una sola llamada.
              </p>
              <p data-lang="en">
                Get a real phone number and send your first SMS in five lines
                of code. The official SDKs handle the entire contracting flow
                in a single call.
              </p>

              <h2 data-lang="es">1. Instala el SDK</h2>
              <h2 data-lang="en">1. Install the SDK</h2>
              {_code_block([
                ("Python", "pip install ami-protocol"),
                ("TypeScript", "npm install @ami-protocol/sdk"),
                ("curl", "# no install needed"),
              ])}

              <h2 data-lang="es">2. Provisiona un número + envía un SMS</h2>
              <h2 data-lang="en">2. Provision a number + send an SMS</h2>
              {_code_block([
                ("Python", "from ami import AmiClient\n\n"
                 'client = AmiClient(api_key="amik_live_...",\n'
                 '                   base_url="https://api.protocolami.com")\n'
                 'creds = client.provision_number(country="ES", customer={\n'
                 '    "legal_name": "Acme S.L.", "tax_id": "B12345678",\n'
                 '    "billing_email": "billing@acme.test",\n'
                 '    "address": "Madrid, Spain",\n'
                 '    "representative_name": "Ada Lovelace",\n'
                 '})\n\n'
                 'agent = client.as_agent(creds.agent_token)\n'
                 'agent.send_sms(to="+34600...", body="Hello from my agent")\n'
                 'print(f"Number: {creds.phone_number}")\n'
                 'print(f"Token (save it!): {creds.agent_token[:20]}...")'),
                ("TypeScript",
                 'import { AmiClient } from "@ami-protocol/sdk";\n\n'
                 'const client = new AmiClient({\n'
                 '  apiKey: process.env.AMI_API_KEY!,\n'
                 '  baseUrl: "https://api.protocolami.com",\n'
                 '});\n\n'
                 'const creds = await client.provisionNumber({\n'
                 '  country: "ES",\n'
                 '  customer: {\n'
                 '    legalName: "Acme S.L.", taxId: "B12345678",\n'
                 '    billingEmail: "billing@acme.test",\n'
                 '    address: "Madrid, Spain",\n'
                 '    representativeName: "Ada Lovelace",\n'
                 '  },\n'
                 '});\n\n'
                 'const agent = client.asAgent(creds.agentToken);\n'
                 'await agent.sendSms({ to: "+34600...", body: "Hello from my agent" });'),
                ("curl",
                 "# 1) request + offer\n"
                 'curl -X POST https://api.protocolami.com/v1/sim-requests \\\n'
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 '  -H "Content-Type: application/json" \\\n'
                 '  -d \'{"country":"ES"}\'\n\n'
                 "# 2-5) accept offer / customer-data / contract / mock-sign\n"
                 "# 6) activate → returns agent_token ONCE\n"
                 'curl -X POST https://api.protocolami.com/v1/mobile-identities/activate \\\n'
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 '  -d \'{"contract_id":"contract_..."}\'\n\n'
                 "# 7) send SMS using the agent_token\n"
                 'curl -X POST https://api.protocolami.com/v1/agent/sms/send \\\n'
                 '  -H "Authorization: Bearer $AGENT_TOKEN" \\\n'
                 '  -d \'{"to":"+34600...","body":"Hello"}\''),
              ])}

              <h2 data-lang="es">3. Pruébalo ahora</h2>
              <h2 data-lang="en">3. Try it now</h2>
              <p data-lang="es">
                El endpoint <code>POST /v1/demo/quick</code> es público (sin auth)
                y ejecuta el flujo entero contra el backend mock. Útil para ver
                el shape de las respuestas sin necesidad de API key.
              </p>
              <p data-lang="en">
                The <code>POST /v1/demo/quick</code> endpoint is public (no auth)
                and runs the full flow against the mock backend. Useful to see
                the shape of responses without an API key.
              </p>
              {_tryit("/v1/demo/quick", "Ejecutar demo", "Run demo", method="POST")}
            """,
        },

        # ===== CONCEPTOS =====
        {
            "id": "concepts",
            "group": "getting_started",
            "group_es": "Empezar", "group_en": "Get started",
            "title_es": "Conceptos", "title_en": "Concepts",
            "body": """
              <h1 data-lang="es">Conceptos clave</h1>
              <h1 data-lang="en">Key concepts</h1>

              <h2>MobileIdentity (MID)</h2>
              <p data-lang="es">
                Una <em>MobileIdentity</em> es la unidad central del protocolo:
                un número de teléfono activo con un conjunto de capacidades
                (sms, voice) asignado a un agente concreto bajo un contrato
                firmado con un customer. Su id tiene formato <code>mid_&lt;hex&gt;</code>.
              </p>
              <p data-lang="en">
                A <em>MobileIdentity</em> is the protocol's central unit: an
                active phone number with a set of capabilities (sms, voice)
                assigned to a specific agent under a signed contract. Its id
                has format <code>mid_&lt;hex&gt;</code>.
              </p>

              <h2>agent_token vs API key</h2>
              <p data-lang="es">
                Hay <strong>dos niveles de auth</strong>: la <em>API key</em>
                del customer cubre la contratación y administración
                (provisión, límites, webhooks, rotación); el <em>agent_token</em>
                está scoped a una MID concreta y cubre la operación diaria
                (enviar SMS, hacer llamadas). Si el agent_token se filtra,
                el blast radius es esa MID y nada más.
              </p>
              <p data-lang="en">
                There are <strong>two auth levels</strong>: the customer's
                <em>API key</em> covers contracting and admin (provisioning,
                limits, webhooks, rotation); the <em>agent_token</em> is scoped
                to one specific MID and covers daily operation (sending SMS,
                placing calls). If the agent_token leaks, the blast radius is
                that one MID and nothing else.
              </p>

              <h2 data-lang="es">Bridge-by-API · voz</h2>
              <h2 data-lang="en">Bridge-by-API · voice</h2>
              <p data-lang="es">
                AMI es el operador / SIP provider, <strong>no</strong> ejecuta
                voz Realtime. Cuando tu agente origina una llamada, AMI marca
                al PSTN y bridgea la llamada por SIP al
                <code>callback_sip_uri</code> que tu agente proporciona
                (típicamente el endpoint SIP de tu motor de voz en tiempo real).
                El audio en sí pasa por la pipa de AMI; el "cerebro" del agente
                vive en tu endpoint.
              </p>
              <p data-lang="en">
                AMI is the operator / SIP provider, it does <strong>not</strong>
                run real-time voice. When your agent places a call, AMI dials
                the PSTN and SIP-bridges the call to the
                <code>callback_sip_uri</code> your agent provides (typically
                your real-time voice engine's SIP endpoint). Audio flows
                through AMI's pipe; the agent "brain" lives at your endpoint.
              </p>
              <p>
                <a href="/diagram"><span data-lang="es">Ver la animación →</span><span data-lang="en">See the animation →</span></a>
              </p>

              <h2 data-lang="es">Multi-tenancy</h2>
              <h2 data-lang="en">Multi-tenancy</h2>
              <p data-lang="es">
                Cada empresa cliente es un <em>customer_account</em> con su
                propia API key. Cada recurso (sim_request, contract, MID,
                customer legal) lleva <code>account_id</code>, y AMI filtra
                cualquier acceso para que un customer NO pueda ver ni mutar
                recursos de otro. Los endpoints <code>/v1/admin/*</code>
                (gestión de customers) requieren un master key separado.
              </p>
              <p data-lang="en">
                Each client company is a <em>customer_account</em> with its
                own API key. Every resource (sim_request, contract, MID, legal
                customer) carries <code>account_id</code>, and AMI filters all
                access so one customer cannot see or mutate another's resources.
                <code>/v1/admin/*</code> endpoints (customer management) require
                a separate master key.
              </p>

              <h2 data-lang="es">Todo real menos el partner telco</h2>
              <h2 data-lang="en">All real except the telco partner</h2>
              <p data-lang="es">
                Contratos, firma, estados, audit, webhooks, rate-limits — todo
                el plano funcional es real y persiste en SQLite. La única pieza
                "mockeable" es el adaptador hacia el SMSC (Kannel) y el PBX
                (Asterisk): por defecto en modo <code>mock</code>, y conmutable
                a <code>live</code> via <code>AMI_TELCO_MODE</code> cuando el
                partner telco aporte credenciales SMPP y trunk SIP.
              </p>
              <p data-lang="en">
                Contracts, signing, state machine, audit log, webhooks,
                rate-limits — the entire functional plane is real and persists
                to SQLite. The only "mockable" piece is the adapter towards the
                SMSC (Kannel) and the PBX (Asterisk): mock by default,
                switchable to <code>live</code> via <code>AMI_TELCO_MODE</code>
                once the telco partner supplies SMPP credentials and a SIP trunk.
              </p>
            """,
        },

        # ===== AUTH =====
        {
            "id": "auth",
            "group": "core",
            "group_es": "Core", "group_en": "Core",
            "title_es": "Autenticación", "title_en": "Authentication",
            "body": f"""
              <h1 data-lang="es">Autenticación</h1>
              <h1 data-lang="en">Authentication</h1>

              <table>
                <thead>
                  <tr>
                    <th data-lang="es">Nivel</th><th data-lang="en">Level</th>
                    <th>Header</th>
                    <th data-lang="es">Formato</th><th data-lang="en">Format</th>
                    <th data-lang="es">Cubre</th><th data-lang="en">Covers</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>1 — customer</td>
                    <td><code>Authorization: Bearer ...</code></td>
                    <td class="mono">amik_live_&lt;48hex&gt;</td>
                    <td data-lang="es">Contratación + admin del MID</td>
                    <td data-lang="en">Contracting + MID admin</td>
                  </tr>
                  <tr>
                    <td>2 — agent</td>
                    <td><code>Authorization: Bearer ...</code></td>
                    <td class="mono">amiagt_live_&lt;64hex&gt;</td>
                    <td data-lang="es">SMS y voz scoped a UN MID</td>
                    <td data-lang="en">SMS and voice scoped to ONE MID</td>
                  </tr>
                  <tr>
                    <td>admin</td>
                    <td><code>Authorization: Bearer ...</code></td>
                    <td class="mono">AMI_ADMIN_KEY (env)</td>
                    <td data-lang="es">CRUD de customer-accounts</td>
                    <td data-lang="en">CRUD of customer-accounts</td>
                  </tr>
                  <tr>
                    <td>telco</td>
                    <td><code>X-Telco-Key</code></td>
                    <td class="mono">AMI_TELCO_INBOUND_KEY (env)</td>
                    <td data-lang="es">Webhooks del partner (/v1/_telco/*)</td>
                    <td data-lang="en">Partner webhooks (/v1/_telco/*)</td>
                  </tr>
                </tbody>
              </table>

              <h2 data-lang="es">Cómo se obtiene cada uno</h2>
              <h2 data-lang="en">How to get each one</h2>
              <ul>
                <li><strong>API key del customer</strong> — <span data-lang="es">creada por el admin con</span><span data-lang="en">created by the admin with</span>
                    <code>POST /v1/admin/customers</code>. <span data-lang="es">Devuelta una sola vez en plano.</span><span data-lang="en">Returned in plaintext once.</span></li>
                <li><strong>agent_token</strong> — <span data-lang="es">devuelto al activar la MID con</span><span data-lang="en">returned when activating the MID with</span>
                    <code>POST /v1/mobile-identities/activate</code>. <span data-lang="es">Una sola vez.</span><span data-lang="en">One time only.</span></li>
                <li><strong>Admin master key</strong> — <span data-lang="es">se setea en la variable de entorno</span><span data-lang="en">set in the environment variable</span>
                    <code>AMI_ADMIN_KEY</code>. <span data-lang="es">Nunca se devuelve por la API.</span><span data-lang="en">Never returned by the API.</span></li>
                <li><strong>Telco inbound key</strong> — <span data-lang="es">env</span><span data-lang="en">env</span>
                    <code>AMI_TELCO_INBOUND_KEY</code>, <span data-lang="es">compartida con Kannel/Asterisk en la VPS del partner.</span><span data-lang="en">shared with Kannel/Asterisk on the partner VPS.</span></li>
              </ul>

              <h2 data-lang="es">Rotación</h2>
              <h2 data-lang="en">Rotation</h2>
              <p data-lang="es">
                Si sospechas de filtración de un agent_token, hard-rota con:
              </p>
              <p data-lang="en">
                If you suspect an agent_token leak, hard-rotate with:
              </p>
              {_code_block([
                ("curl", "curl -X POST https://api.protocolami.com/v1/mobile-identities/$MID/rotate-token \\\n  -H \"Authorization: Bearer $AMI_API_KEY\""),
                ("Python", "client.rotate_agent_token(mid)"),
                ("TypeScript", "await client.rotateAgentToken(mid);"),
              ])}
              <p data-lang="es">
                El token anterior queda invalidado al instante. La respuesta
                trae el nuevo agent_token en plano (única vez).
              </p>
              <p data-lang="en">
                The previous token is invalidated instantly. The response
                returns the new agent_token in plaintext (one time only).
              </p>
            """,
        },

        # ===== CONTRACTING =====
        {
            "id": "contracting",
            "group": "core",
            "group_es": "Core", "group_en": "Core",
            "title_es": "Flujo de contratación", "title_en": "Contracting flow",
            "body": f"""
              <h1 data-lang="es">Flujo de contratación</h1>
              <h1 data-lang="en">Contracting flow</h1>
              <p class="lead" data-lang="es">
                Seis pasos: request → offer → customer-data → contract → sign → activate.
                Cada paso devuelve el estado de la SIMRequest que actúa como
                máquina de estados (ver §17.6 del spec).
              </p>
              <p class="lead" data-lang="en">
                Six steps: request → offer → customer-data → contract → sign → activate.
                Each step returns the SIMRequest state which acts as the state
                machine (see §17.6 of the spec).
              </p>

              <h2>1. SIMRequest + Offer</h2>
              {_code_block([
                ("curl", "curl -X POST https://api.protocolami.com/v1/sim-requests \\\n"
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 '  -H "Content-Type: application/json" \\\n'
                 "  -d '{\"country\":\"ES\",\"sim_type\":\"eSIM\",\"capabilities\":[\"sms\",\"voice\"]}'"),
                ("Python", 'sim = client.request_number(country="ES",\n'
                 '                              capabilities=["sms","voice"],\n'
                 '                              agent_name="agent01")\n'
                 "offer_id = sim.id  # OfferAccepted.id"),
                ("TypeScript", "const { simRequest, offer } = await client.requestNumber({\n"
                 '  country: "ES", capabilities: ["sms","voice"], agentName: "agent01",\n'
                 "});"),
              ])}

              <h2>2. Accept offer</h2>
              {_code_block([
                ("curl", "curl -X POST https://api.protocolami.com/v1/offers/$OFFER_ID/accept \\\n  -H \"Authorization: Bearer $AMI_API_KEY\""),
                ("Python", "client.accept_offer(offer.id)"),
                ("TypeScript", "await client.acceptOffer(offer.id);"),
              ])}

              <h2>3. Customer data</h2>
              {_code_block([
                ("curl",
                 "curl -X POST https://api.protocolami.com/v1/sim-requests/$SIM_ID/customer-data \\\n"
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 "  -d '" + json_dumps({
                   "customer": {
                     "legal_name": "Acme S.L.", "tax_id": "B12345678",
                     "billing_email": "billing@acme.test",
                     "address": "Madrid, Spain",
                     "representative_name": "Ada Lovelace",
                   }
                 }) + "'"),
                ("Python", 'customer = client.submit_customer_data(\n'
                 '    sim_request_id=sim.sim_request_id,\n'
                 '    legal_name="Acme S.L.", tax_id="B12345678",\n'
                 '    billing_email="billing@acme.test",\n'
                 '    address="Madrid, Spain",\n'
                 '    representative_name="Ada Lovelace",\n'
                 ')'),
              ])}

              <h2>4. Contract → 5. Sign → 6. Activate</h2>
              <p data-lang="es">
                <code>POST /v1/contracts</code> crea el contrato y devuelve un
                <code>signature_url</code> con el <em>signing_token</em> de
                128 bits. El firmante humano abre esa URL y hace clic; si
                automatizas el flujo, <code>POST /v1/contracts/{{id}}/mock-sign</code>
                es el atajo programático. Después, <code>POST /v1/mobile-identities/activate</code>
                devuelve la MID activa con el <em>agent_token</em> en plano.
              </p>
              <p data-lang="en">
                <code>POST /v1/contracts</code> creates the contract and returns
                a <code>signature_url</code> with the 128-bit <em>signing_token</em>.
                A human signer opens that URL and clicks; if you automate the
                flow, <code>POST /v1/contracts/{{id}}/mock-sign</code> is the
                programmatic shortcut. Then <code>POST /v1/mobile-identities/activate</code>
                returns the active MID with the <em>agent_token</em> in plaintext.
              </p>
              {_code_block([
                ("Python", '''contract = client.create_contract(offer_id=offer.id, customer_id=customer.id)
client.mock_sign(contract.id)  # o abrir contract.signature_url en navegador
identity = client.activate_identity(contract.id)
# identity.agent_token disponible AQUÍ — una sola vez
print(identity.phone_number, identity.agent_token)'''),
              ])}

              <h2 data-lang="es">Atajo todo-en-uno</h2>
              <h2 data-lang="en">One-shot helper</h2>
              <p data-lang="es">
                Los SDKs exponen <code>provision_number()</code> que hace los 6
                pasos en una sola llamada y devuelve <code>{{mid, phone, agent_token}}</code>.
              </p>
              <p data-lang="en">
                The SDKs expose <code>provision_number()</code> which runs all
                6 steps in a single call and returns <code>{{mid, phone, agent_token}}</code>.
              </p>
            """,
        },

        # ===== SMS =====
        {
            "id": "sms",
            "group": "operations",
            "group_es": "Operations", "group_en": "Operations",
            "title_es": "SMS", "title_en": "SMS",
            "body": f"""
              <h1 data-lang="es">SMS</h1>
              <h1 data-lang="en">SMS</h1>

              <h2 data-lang="es">Enviar SMS saliente</h2>
              <h2 data-lang="en">Send outbound SMS</h2>
              <p data-lang="es">
                Auth: <em>agent_token</em>. El SMS sale en estado <code>queued</code>;
                el DLR del SMSC (o el simulador mock en dev) lo lleva a
                <code>sent</code> y luego <code>delivered</code>.
              </p>
              <p data-lang="en">
                Auth: <em>agent_token</em>. SMS goes out in <code>queued</code>;
                the SMSC's DLR (or the mock simulator in dev) takes it through
                <code>sent</code> and then <code>delivered</code>.
              </p>
              {_code_block([
                ("Python", 'agent.send_sms(to="+34611000000", body="Hello")'),
                ("TypeScript", 'await agent.sendSms({ to: "+34611000000", body: "Hello" });'),
                ("curl",
                 'curl -X POST https://api.protocolami.com/v1/agent/sms/send \\\n'
                 '  -H "Authorization: Bearer $AGENT_TOKEN" \\\n'
                 "  -d '" + json_dumps({"to": "+34611000000", "body": "Hello"}) + "'"),
                ("MCP", 'ami.send_sms(to="+34611000000", body="Hello")'),
              ])}

              <h2 data-lang="es">Listar SMS</h2>
              <h2 data-lang="en">List SMS</h2>
              {_code_block([
                ("Python", '''msgs = agent.list_sms(limit=20, direction="outbound")
for m in msgs:
    print(m.id, m.status, m.body)'''),
                ("curl", '''curl "https://api.protocolami.com/v1/agent/sms?limit=20&direction=outbound" \\
  -H "Authorization: Bearer $AGENT_TOKEN"'''),
              ])}

              <h2 data-lang="es">SMS entrante</h2>
              <h2 data-lang="en">Inbound SMS</h2>
              <p data-lang="es">
                Llega al backend vía el webhook del SMSC en
                <code>POST /v1/_telco/sms/inbound</code>. Se asocia automáticamente
                al MID correspondiente al número receptor. El agente lo ve en
                <code>list_sms(direction="inbound")</code> o (mejor) recibe
                un webhook saliente <code>sms.inbound</code> en su URL registrada.
              </p>
              <p data-lang="en">
                Arrives at the backend via the SMSC webhook at
                <code>POST /v1/_telco/sms/inbound</code>. Auto-associates to the
                MID matching the destination number. The agent sees it in
                <code>list_sms(direction="inbound")</code> or (better) receives
                an outbound <code>sms.inbound</code> webhook at its registered URL.
              </p>

              <h2 data-lang="es">Estados</h2>
              <h2 data-lang="en">Statuses</h2>
              <table>
                <tr><td class="mono">queued</td><td data-lang="es">aceptado, esperando salir</td><td data-lang="en">accepted, awaiting send</td></tr>
                <tr><td class="mono">sent</td><td data-lang="es">cursado al SMSC, en camino</td><td data-lang="en">sent to SMSC, in flight</td></tr>
                <tr><td class="mono">delivered</td><td data-lang="es">DLR final positivo</td><td data-lang="en">final positive DLR</td></tr>
                <tr><td class="mono">failed</td><td data-lang="es">DLR de fallo — retry automático hasta 3 veces</td><td data-lang="en">failure DLR — auto-retried up to 3 times</td></tr>
                <tr><td class="mono">received</td><td data-lang="es">MO entrante</td><td data-lang="en">inbound MO</td></tr>
              </table>
            """,
        },

        # ===== VOICE =====
        {
            "id": "voice",
            "group": "operations",
            "group_es": "Operations", "group_en": "Operations",
            "title_es": "Voz (bridge-by-API)", "title_en": "Voice (bridge-by-API)",
            "body": f"""
              <h1 data-lang="es">Voz · bridge-by-API</h1>
              <h1 data-lang="en">Voice · bridge-by-API</h1>
              <blockquote data-lang="es">
                <strong>Recordatorio:</strong> AMI <strong>NO</strong> ejecuta voz Realtime.
                AMI es la pipa SIP/RTP. El cerebro de la conversación vive en tu
                <code>callback_sip_uri</code>.
              </blockquote>
              <blockquote data-lang="en">
                <strong>Reminder:</strong> AMI does <strong>NOT</strong> run real-time voice.
                AMI is the SIP/RTP pipe. The conversation brain lives at your
                <code>callback_sip_uri</code>.
              </blockquote>

              <h2 data-lang="es">Originar una llamada saliente</h2>
              <h2 data-lang="en">Place an outbound call</h2>
              {_code_block([
                ("Python", 'call = agent.place_call(\n'
                 '    to="+34600999888",\n'
                 '    callback_sip_uri="sip:proj@sip.api.example;transport=tls",\n'
                 ')\n'
                 'print(call.id, call.status)  # initiated'),
                ("TypeScript", 'const call = await agent.placeCall({\n'
                 '  to: "+34600999888",\n'
                 '  callbackSipUri: "sip:proj@sip.api.example;transport=tls",\n'
                 '});'),
                ("curl",
                 'curl -X POST https://api.protocolami.com/v1/agent/calls/place \\\n'
                 '  -H "Authorization: Bearer $AGENT_TOKEN" \\\n'
                 "  -d '" + json_dumps({
                   "to": "+34600999888",
                   "callback_sip_uri": "sip:proj@sip.api.example;transport=tls",
                 }) + "'"),
              ])}

              <h2 data-lang="es">Colgar una llamada</h2>
              <h2 data-lang="en">Hang up a call</h2>
              {_code_block([
                ("Python", "agent.hangup_call(call.id)"),
                ("TypeScript", "await agent.hangupCall(call.id);"),
                ("curl", '''curl -X POST https://api.protocolami.com/v1/agent/calls/$CALL_ID/hangup \\
  -H "Authorization: Bearer $AGENT_TOKEN"'''),
              ])}

              <h2 data-lang="es">Llamadas entrantes</h2>
              <h2 data-lang="en">Inbound calls</h2>
              <p data-lang="es">
                Configura un <code>inbound_sip_uri</code> por MID. Cuando entra
                una llamada al número, el PBX consulta a AMI a dónde reenviarla
                y la bridgea por SIP a tu URI configurada. Sin URI configurado,
                la llamada se rechaza limpiamente con 486 Busy Here.
              </p>
              <p data-lang="en">
                Configure an <code>inbound_sip_uri</code> per MID. When a call
                comes in to the number, the PBX asks AMI where to forward and
                bridges it via SIP to your configured URI. Without a configured
                URI, the call is cleanly rejected with 486 Busy Here.
              </p>
              {_code_block([
                ("Python", 'client.set_inbound_sip_uri(mid,\n'
                 '    "sip:agent@your-pbx.example;transport=tls")'),
                ("curl",
                 'curl -X POST https://api.protocolami.com/v1/mobile-identities/$MID/inbound-config \\\n'
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 "  -d '" + json_dumps({
                   "inbound_sip_uri": "sip:agent@your-pbx.example;transport=tls",
                 }) + "'"),
              ])}

              <h2 data-lang="es">Lifecycle de una llamada</h2>
              <h2 data-lang="en">Call lifecycle</h2>
              <table>
                <tr><td class="mono">initiated → ringing → in_progress → completed</td><td data-lang="es">camino feliz</td><td data-lang="en">happy path</td></tr>
                <tr><td class="mono">failed</td><td data-lang="es">error técnico (retry automático si pre-answer)</td><td data-lang="en">technical error (auto-retry if pre-answer)</td></tr>
                <tr><td class="mono">no_answer</td><td data-lang="es">timeout sin contestar (retry auto)</td><td data-lang="en">timeout no answer (auto-retry)</td></tr>
                <tr><td class="mono">busy</td><td data-lang="es">línea ocupada (NO se reintenta)</td><td data-lang="en">line busy (NOT retried)</td></tr>
                <tr><td class="mono">cancelled</td><td data-lang="es">hangup explícito antes de answer</td><td data-lang="en">explicit hangup before answer</td></tr>
              </table>
            """,
        },

        # ===== WEBHOOKS =====
        {
            "id": "webhooks",
            "group": "operations",
            "group_es": "Operations", "group_en": "Operations",
            "title_es": "Webhooks", "title_en": "Webhooks",
            "body": f"""
              <h1>Webhooks</h1>
              <p class="lead" data-lang="es">
                AMI te hace POST a una URL tuya cuando ocurre un evento del
                MID. Firmamos con HMAC-SHA256 sobre <code>timestamp.body</code>
                para que puedas verificar autenticidad y rechazar replays.
              </p>
              <p class="lead" data-lang="en">
                AMI POSTs to a URL of yours when an event happens for a MID.
                We sign with HMAC-SHA256 over <code>timestamp.body</code> so you
                can verify authenticity and reject replays.
              </p>

              <h2 data-lang="es">Registrar un webhook</h2>
              <h2 data-lang="en">Register a webhook</h2>
              {_code_block([
                ("Python", 'wh = client.create_webhook(mid,\n'
                 '    url="https://your-app.example/ami/hook",\n'
                 '    events=["sms.inbound","call.completed"])\n'
                 '# wh.secret está aquí UNA SOLA VEZ — guárdalo\n'
                 'print(wh.secret)'),
                ("curl",
                 'curl -X POST https://api.protocolami.com/v1/mobile-identities/$MID/webhooks \\\n'
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 "  -d '" + json_dumps({
                   "url": "https://your-app.example/ami/hook",
                   "events": ["sms.inbound", "call.completed"],
                 }) + "'"),
              ])}

              <h2 data-lang="es">Eventos disponibles</h2>
              <h2 data-lang="en">Available events</h2>
              <table>
                <tr><td class="mono">sms.inbound</td><td data-lang="es">MO recibido en el MID</td><td data-lang="en">MO received at the MID</td></tr>
                <tr><td class="mono">sms.delivered</td><td data-lang="es">DLR positivo de MT enviado</td><td data-lang="en">positive DLR for sent MT</td></tr>
                <tr><td class="mono">sms.failed</td><td data-lang="es">DLR de fallo</td><td data-lang="en">failure DLR</td></tr>
                <tr><td class="mono">call.inbound</td><td data-lang="es">llamada entrante (antes de answer)</td><td data-lang="en">inbound call (before answer)</td></tr>
                <tr><td class="mono">call.completed</td><td data-lang="es">llamada terminada con duración</td><td data-lang="en">call ended with duration</td></tr>
                <tr><td class="mono">call.failed</td><td data-lang="es">llamada caída (failed/no_answer/busy/cancelled)</td><td data-lang="en">call dropped (failed/no_answer/busy/cancelled)</td></tr>
                <tr><td class="mono">*</td><td data-lang="es">wildcard — todos</td><td data-lang="en">wildcard — all</td></tr>
              </table>

              <h2 data-lang="es">Verificar la firma en tu handler</h2>
              <h2 data-lang="en">Verify the signature in your handler</h2>
              {_code_block([
                ("Python", '''from ami.webhooks import verify_signature

def my_handler(request):
    body = request.body
    sig = request.headers["X-Ami-Signature"]
    ts  = request.headers["X-Ami-Timestamp"]
    if not verify_signature(secret="whsec_...",
                            body=body,
                            signature_header=sig,
                            timestamp_header=ts):
        return 401
    payload = json.loads(body)
    # payload = {{"event": "sms.inbound", "mid": "mid_...", "data": {{...}}, "delivered_at": "..."}}
    ...'''),
                ("TypeScript", '''import {{ verifySignature }} from "@ami-protocol/sdk/webhooks";

export async function handler(req) {{
  const body = await readRawBody(req);
  const sig  = req.headers["x-ami-signature"];
  const ts   = req.headers["x-ami-timestamp"];
  const ok = verifySignature({{
    secret: process.env.AMI_WEBHOOK_SECRET,
    body, signatureHeader: sig, timestampHeader: ts,
  }});
  if (!ok) return new Response(null, {{ status: 401 }});
  const payload = JSON.parse(body);
  ...
}}'''),
                ("manual", '''# pseudocode — equivalente en cualquier lenguaje
ts = headers["X-Ami-Timestamp"]
sig_header = headers["X-Ami-Signature"]   # "sha256=<hex>"
if abs(now() - int(ts)) > 300: reject("stale")
expected = "sha256=" + hmac_sha256(secret, ts + "." + raw_body).hex()
if not constant_time_eq(expected, sig_header): reject("bad sig")'''),
              ])}

              <h2 data-lang="es">Política de reintentos</h2>
              <h2 data-lang="en">Retry policy</h2>
              <p data-lang="es">
                AMI reintenta hasta 4 veces con backoff <code>[0.5s, 2s, 8s]</code>.
                Tras 10 fallos consecutivos el webhook se auto-deshabilita
                (status <code>disabled</code>). Defensa contra SSRF: validamos
                la URL antes de cada delivery (no solo al registrar) y rechazamos
                redirects 3xx automáticos.
              </p>
              <p data-lang="en">
                AMI retries up to 4 times with backoff <code>[0.5s, 2s, 8s]</code>.
                After 10 consecutive failures the webhook auto-disables
                (status <code>disabled</code>). SSRF defense: we validate the
                URL before each delivery (not only at register time) and reject
                automatic 3xx redirects.
              </p>
            """,
        },

        # ===== LIMITS =====
        {
            "id": "limits",
            "group": "operations",
            "group_es": "Operations", "group_en": "Operations",
            "title_es": "Límites y gasto", "title_en": "Limits & spending",
            "body": f"""
              <h1 data-lang="es">Rate limits y spending</h1>
              <h1 data-lang="en">Rate limits and spending</h1>
              <p class="lead" data-lang="es">
                Cada MID tiene topes configurables: SMS/hora, SMS/día,
                llamadas/hora, llamadas/día, budget mensual en EUR, y allowlist
                de prefijos de país. El backend hace enforcement antes de
                cursar al telco — si bloquea, devuelve 429 con la razón concreta.
              </p>
              <p class="lead" data-lang="en">
                Every MID has configurable caps: SMS/hour, SMS/day, calls/hour,
                calls/day, monthly EUR budget, and country-prefix allowlist.
                Backend enforces before cursing to telco — if blocked, returns
                429 with the specific reason.
              </p>

              <h2 data-lang="es">Defaults al activar</h2>
              <h2 data-lang="en">Defaults at activation</h2>
              <ul>
                <li>sms_per_hour: 60 · sms_per_day: 500</li>
                <li>calls_per_hour: 20 · calls_per_day: 200</li>
                <li>monthly_budget_eur: 100.00</li>
                <li>allowed_country_prefixes: <code>["+34"]</code> <span data-lang="es">(España)</span><span data-lang="en">(Spain)</span></li>
              </ul>

              <h2 data-lang="es">Cambiar los límites</h2>
              <h2 data-lang="en">Change the limits</h2>
              {_code_block([
                ("Python", 'client.update_limits(mid,\n'
                 '    sms_per_day=1000,\n'
                 '    monthly_budget_eur=50,\n'
                 '    allowed_country_prefixes=["+34","+33","+1"])'),
                ("curl",
                 'curl -X POST https://api.protocolami.com/v1/mobile-identities/$MID/limits \\\n'
                 '  -H "Authorization: Bearer $AMI_API_KEY" \\\n'
                 "  -d '" + json_dumps({"sms_per_day": 1000, "monthly_budget_eur": 50}) + "'"),
              ])}

              <h2 data-lang="es">Ver uso en tiempo real</h2>
              <h2 data-lang="en">Real-time usage</h2>
              {_code_block([
                ("Python", '''u = agent.usage()
print(f"SMS today: {{u.sms_count_today}} / {{u.sms_per_day}}")
print(f"Spend: €{{u.spend_this_month_eur}} / €{{u.monthly_budget_eur}}")'''),
                ("curl", '''curl https://api.protocolami.com/v1/agent/usage \\
  -H "Authorization: Bearer $AGENT_TOKEN"'''),
              ])}

              <h2 data-lang="es">Razones de bloqueo (429)</h2>
              <h2 data-lang="en">Block reasons (429)</h2>
              <ul>
                <li><code>country_not_allowed</code></li>
                <li><code>sms_hourly_limit_exceeded</code> · <code>sms_daily_limit_exceeded</code></li>
                <li><code>calls_hourly_limit_exceeded</code> · <code>calls_daily_limit_exceeded</code></li>
                <li><code>monthly_budget_exceeded</code></li>
              </ul>
            """,
        },

        # ===== ADMIN =====
        {
            "id": "admin",
            "group": "operations",
            "group_es": "Operations", "group_en": "Operations",
            "title_es": "Admin (multi-tenancy)", "title_en": "Admin (multi-tenancy)",
            "body": f"""
              <h1>Admin · multi-tenancy</h1>
              <p class="lead" data-lang="es">
                Los endpoints <code>/v1/admin/*</code> gestionan customers (cuentas
                con su propia API key). Auth: master key
                <code>AMI_ADMIN_KEY</code> (env), separada de la API key del customer.
              </p>
              <p class="lead" data-lang="en">
                <code>/v1/admin/*</code> endpoints manage customers (accounts
                with their own API key). Auth: master key
                <code>AMI_ADMIN_KEY</code> (env), separate from any customer's API key.
              </p>

              <h2 data-lang="es">Crear un customer-account</h2>
              <h2 data-lang="en">Create a customer-account</h2>
              {_code_block([
                ("curl",
                 "curl -X POST https://api.protocolami.com/v1/admin/customers \\\n"
                 '  -H "Authorization: Bearer $AMI_ADMIN_KEY" \\\n'
                 "  -d '" + json_dumps({"name": "Acme Robotics",
                                         "billing_email": "billing@acme.test"}) + "'\n"
                 "# devuelve api_key UNA SOLA VEZ"),
              ])}

              <h2 data-lang="es">Listar / rotar / suspender</h2>
              <h2 data-lang="en">List / rotate / suspend</h2>
              {_code_block([
                ("curl",
                 "# listar\n"
                 "curl https://api.protocolami.com/v1/admin/customers \\\n"
                 '  -H "Authorization: Bearer $AMI_ADMIN_KEY"\n\n'
                 "# rotar API key (hard rotate)\n"
                 "curl -X POST https://api.protocolami.com/v1/admin/customers/$ID/rotate-key \\\n"
                 '  -H "Authorization: Bearer $AMI_ADMIN_KEY"\n\n'
                 "# suspender / reactivar\n"
                 "curl -X POST https://api.protocolami.com/v1/admin/customers/$ID/suspend \\\n"
                 '  -H "Authorization: Bearer $AMI_ADMIN_KEY" \\\n'
                 "  -d '" + json_dumps({"reason": "non_payment"}) + "'\n"
                 "curl -X POST https://api.protocolami.com/v1/admin/customers/$ID/activate \\\n"
                 '  -H "Authorization: Bearer $AMI_ADMIN_KEY"'),
              ])}

              <h2 data-lang="es">Aislamiento entre customers</h2>
              <h2 data-lang="en">Isolation between customers</h2>
              <p data-lang="es">
                Cada recurso (sim_request, contract, MID, customer legal) lleva
                <code>account_id</code> desde su creación. El backend filtra todo
                acceso por este campo — un customer A no puede ver, mutar ni
                operar ningún recurso de B. El intento devuelve 404 (no 403)
                para no leakear la existencia del id.
              </p>
              <p data-lang="en">
                Every resource (sim_request, contract, MID, legal customer)
                carries <code>account_id</code> from creation. The backend
                filters all access by this field — customer A cannot see,
                mutate or operate any resource of B. The attempt returns 404
                (not 403) to avoid leaking the id's existence.
              </p>
            """,
        },

        # ===== ERRORS =====
        {
            "id": "errors",
            "group": "reference",
            "group_es": "Referencia", "group_en": "Reference",
            "title_es": "Errores", "title_en": "Errors",
            "body": """
              <h1 data-lang="es">Códigos de error</h1>
              <h1 data-lang="en">Error codes</h1>

              <table>
                <thead>
                  <tr>
                    <th>HTTP</th><th>error</th>
                    <th data-lang="es">Significado</th><th data-lang="en">Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  <tr><td class="mono">400</td><td class="mono">invalid_*</td><td data-lang="es">campo malformado</td><td data-lang="en">malformed field</td></tr>
                  <tr><td class="mono">400</td><td class="mono">missing_fields</td><td data-lang="es">faltan campos requeridos</td><td data-lang="en">missing required fields</td></tr>
                  <tr><td class="mono">401</td><td class="mono">unauthorized</td><td data-lang="es">API key inválida o faltante</td><td data-lang="en">invalid or missing API key</td></tr>
                  <tr><td class="mono">401</td><td class="mono">invalid_agent_token</td><td data-lang="es">agent_token inválido</td><td data-lang="en">invalid agent_token</td></tr>
                  <tr><td class="mono">401</td><td class="mono">unauthorized_admin</td><td data-lang="es">admin master key inválida</td><td data-lang="en">invalid admin master key</td></tr>
                  <tr><td class="mono">404</td><td class="mono">*_not_found</td><td data-lang="es">recurso no existe o pertenece a otro account</td><td data-lang="en">resource not found or owned by another account</td></tr>
                  <tr><td class="mono">409</td><td class="mono">invalid_state / *_not_active</td><td data-lang="es">transición no permitida o recurso suspended</td><td data-lang="en">transition not allowed or suspended resource</td></tr>
                  <tr><td class="mono">409</td><td class="mono">webhook_limit_reached</td><td data-lang="es">10 webhooks por MID max</td><td data-lang="en">10 webhooks per MID max</td></tr>
                  <tr><td class="mono">429</td><td class="mono">sms_hourly_limit_exceeded</td><td data-lang="es">SMS/hora superado</td><td data-lang="en">SMS/hour exceeded</td></tr>
                  <tr><td class="mono">429</td><td class="mono">monthly_budget_exceeded</td><td data-lang="es">presupuesto mensual agotado</td><td data-lang="en">monthly budget depleted</td></tr>
                  <tr><td class="mono">429</td><td class="mono">country_not_allowed</td><td data-lang="es">prefijo de país no en la allowlist</td><td data-lang="en">country prefix not in allowlist</td></tr>
                  <tr><td class="mono">503</td><td class="mono">telco_inbound_disabled</td><td data-lang="es">AMI_TELCO_INBOUND_KEY no configurada</td><td data-lang="en">AMI_TELCO_INBOUND_KEY not set</td></tr>
                </tbody>
              </table>

              <h2 data-lang="es">Reintentos</h2>
              <h2 data-lang="en">Retries</h2>
              <p data-lang="es">
                Los SDKs reintentan GET en 5xx con backoff exponencial. POST
                <strong>NO</strong> se reintenta automáticamente — duplicar un
                SMS o un contrato sobre un blip es peor que devolver el error.
              </p>
              <p data-lang="en">
                The SDKs retry GETs on 5xx with exponential backoff. POSTs are
                <strong>NOT</strong> auto-retried — duplicating an SMS or a
                contract on a blip is worse than surfacing the error.
              </p>
            """,
        },

        # ===== SDKs =====
        {
            "id": "sdks",
            "group": "reference",
            "group_es": "Referencia", "group_en": "Reference",
            "title_es": "SDKs", "title_en": "SDKs",
            "body": """
              <h1>SDKs</h1>

              <table>
                <thead>
                  <tr><th>SDK</th><th>Package</th><th>Repo</th><th>Status</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Python ≥ 3.9</td>
                    <td class="mono">ami-protocol</td>
                    <td><a href="https://github.com/Gamino17/AMI/tree/main/sdk/python">sdk/python</a></td>
                    <td>0.1.0</td>
                  </tr>
                  <tr>
                    <td>TypeScript / Node ≥ 18</td>
                    <td class="mono">@ami-protocol/sdk</td>
                    <td><a href="https://github.com/Gamino17/AMI/tree/main/sdk/typescript">sdk/typescript</a></td>
                    <td>0.1.0</td>
                  </tr>
                  <tr>
                    <td>MCP server</td>
                    <td class="mono">ami_mcp.py</td>
                    <td><a href="https://github.com/Gamino17/AMI/blob/main/ami_mcp.py">ami_mcp.py</a></td>
                    <td>23 tools</td>
                  </tr>
                </tbody>
              </table>

              <h2 data-lang="es">Conectar tu cliente MCP</h2>
              <h2 data-lang="en">Connect your MCP client</h2>
              <p data-lang="es">
                Apunta tu cliente MCP (streamable-http) a:
              </p>
              <p data-lang="en">
                Point your MCP client (streamable-http) to:
              </p>
              <p><code>https://mcp.protocolami.com/mcp/</code></p>

              <h2>REST direct</h2>
              <p data-lang="es">
                Para clientes no-MCP, la API REST tiene 47 endpoints documentados
                en <a href="/openapi.json">/openapi.json</a> (OpenAPI 3.1) y
                <a href="/spec">/spec</a> (markdown/HTML con ejemplos).
              </p>
              <p data-lang="en">
                For non-MCP clients, the REST API has 47 endpoints documented
                at <a href="/openapi.json">/openapi.json</a> (OpenAPI 3.1) and
                <a href="/spec">/spec</a> (markdown/HTML with examples).
              </p>
            """,
        },
    ]


# ====================== RENDER ======================

def render_docs_page(lang: str = "es") -> str:
    """Genera la página /docs entera. Bilingüe — el lang inicial viene del
    detector del dispatcher; el toggle del header alterna en cliente."""
    import html as _html
    sections = _sections()

    # Sidebar: agrupado por group
    groups: dict[str, list[dict]] = {}
    for s in sections:
        groups.setdefault(s["group"], []).append(s)
    group_order = ["getting_started", "core", "operations", "reference"]
    sidebar_html = []
    for g in group_order:
        if g not in groups: continue
        items = groups[g]
        title_es = items[0]["group_es"]
        title_en = items[0]["group_en"]
        sidebar_html.append(
            f'<h3><span data-lang="es">{_html.escape(title_es)}</span>'
            f'<span data-lang="en">{_html.escape(title_en)}</span></h3>'
        )
        sidebar_html.append('<ul>')
        for s in items:
            sidebar_html.append(
                f'<li><a href="#{s["id"]}" data-sidebar-link="{s["id"]}">'
                f'<span data-lang="es">{_html.escape(s["title_es"])}</span>'
                f'<span data-lang="en">{_html.escape(s["title_en"])}</span></a></li>'
            )
        sidebar_html.append('</ul>')
    sidebar = "\n".join(sidebar_html)

    # Content
    content_html = []
    for s in sections:
        content_html.append(f'<section id="{s["id"]}">{s["body"]}</section>')
    content = "\n".join(content_html)

    # TOC simple (derivado de las secciones, sin h2 internos por ahora)
    toc_items = "".join(
        f'<li><a href="#{s["id"]}" data-toc-link="{s["id"]}">'
        f'<span data-lang="es">{_html.escape(s["title_es"])}</span>'
        f'<span data-lang="en">{_html.escape(s["title_en"])}</span></a></li>'
        for s in sections
    )

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Documentation</title>
<meta name="description" content="Interactive documentation for AMI — the open protocol for AI agents to provision and operate their own mobile identity.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_DOCS_CSS}</style>
<script>
  // Restaurar lang antes de paint
  (function() {{
    try {{
      var l = localStorage.getItem('ami-lang');
      if (l === 'es' || l === 'en') document.documentElement.lang = l;
    }} catch(e) {{}}
  }})();
</script>
</head>
<body>

  <header class="docs-header">
    <div class="wrap">
      <a class="docs-brand" href="/">AMI<span class="dot">.</span><span class="sep">/</span><span data-lang="es">docs</span><span data-lang="en">docs</span></a>
      <div class="docs-search">
        <input type="text" id="docsSearch" placeholder="Search docs...">
      </div>
      <nav class="docs-nav">
        <a href="/spec">spec</a>
        <a href="/openapi.json">openapi</a>
        <a href="/diagram">diagram</a>
        <a href="https://github.com/Gamino17/AMI">github</a>
        <button class="pill" id="langToggle">EN</button>
      </nav>
    </div>
  </header>

  <main class="docs-shell">
    <aside class="docs-sidebar">
      {sidebar}
    </aside>

    <article class="docs-content">
      {content}
    </article>

    <aside class="docs-toc">
      <h4 data-lang="es">en esta página</h4>
      <h4 data-lang="en">on this page</h4>
      <ul>{toc_items}</ul>
    </aside>
  </main>

<script>
  // ====== Lang toggle ======
  (function() {{
    var btn = document.getElementById('langToggle');
    if (!btn) return;
    function sync() {{
      var cur = document.documentElement.lang || 'es';
      btn.textContent = cur === 'es' ? 'EN' : 'ES';
    }}
    btn.addEventListener('click', function() {{
      var next = (document.documentElement.lang === 'es') ? 'en' : 'es';
      document.documentElement.lang = next;
      try {{ localStorage.setItem('ami-lang', next); }} catch(e) {{}}
      sync();
    }});
    sync();
  }})();

  // ====== Code tabs ======
  document.querySelectorAll('.docs-code').forEach(function(block) {{
    var tabs = block.querySelectorAll('.docs-code-tab');
    var panes = block.querySelectorAll('.docs-code-pane');
    tabs.forEach(function(tab) {{
      tab.addEventListener('click', function() {{
        var idx = tab.getAttribute('data-tab');
        tabs.forEach(function(t) {{ t.classList.toggle('active', t === tab); }});
        panes.forEach(function(p) {{ p.classList.toggle('active', p.getAttribute('data-pane') === idx); }});
      }});
    }});
  }});

  // ====== Copy buttons ======
  document.querySelectorAll('.docs-code [data-copy]').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var block = btn.closest('.docs-code');
      var activePane = block.querySelector('.docs-code-pane.active pre');
      if (!activePane) return;
      navigator.clipboard.writeText(activePane.textContent).then(function() {{
        var orig = btn.textContent;
        btn.textContent = 'copied!';
        setTimeout(function() {{ btn.textContent = orig; }}, 1200);
      }});
    }});
  }});

  // ====== Try-it widgets ======
  document.querySelectorAll('[data-tryit]').forEach(function(widget) {{
    var endpoint = widget.getAttribute('data-endpoint');
    var method = widget.getAttribute('data-method');
    var bodyJson = widget.getAttribute('data-body') || '{{}}';
    var btn = widget.querySelector('[data-tryit-run]');
    var out = widget.querySelector('[data-tryit-out]');
    var status = widget.querySelector('[data-tryit-status]');
    if (!btn) return;
    btn.addEventListener('click', async function() {{
      btn.disabled = true;
      status.className = 'docs-tryit-status';
      status.textContent = '...';
      out.classList.remove('hidden');
      out.textContent = 'Running...';
      try {{
        var opts = {{ method: method, headers: {{ 'Accept': 'application/json' }} }};
        if (method !== 'GET' && method !== 'HEAD') {{
          opts.headers['Content-Type'] = 'application/json';
          opts.body = bodyJson;
        }}
        var r = await fetch(endpoint, opts);
        var text = await r.text();
        var pretty = text;
        try {{ pretty = JSON.stringify(JSON.parse(text), null, 2); }} catch(e) {{}}
        status.className = 'docs-tryit-status ' + (r.ok ? 'ok' : 'err');
        status.textContent = r.status + (r.ok ? ' OK' : ' Error');
        out.textContent = pretty;
      }} catch(e) {{
        status.className = 'docs-tryit-status err';
        status.textContent = 'Network error';
        out.textContent = String(e);
      }} finally {{
        btn.disabled = false;
      }}
    }});
  }});

  // ====== Sidebar active link + scroll spy ======
  var sidebarLinks = document.querySelectorAll('[data-sidebar-link]');
  var tocLinks = document.querySelectorAll('[data-toc-link]');
  var sections = document.querySelectorAll('.docs-content > section');
  function highlight() {{
    var pos = window.scrollY + 100;
    var current = sections[0] ? sections[0].id : null;
    sections.forEach(function(s) {{
      if (s.offsetTop <= pos) current = s.id;
    }});
    sidebarLinks.forEach(function(l) {{
      l.classList.toggle('active', l.getAttribute('data-sidebar-link') === current);
    }});
    tocLinks.forEach(function(l) {{
      l.classList.toggle('active', l.getAttribute('data-toc-link') === current);
    }});
  }}
  window.addEventListener('scroll', highlight, {{ passive: true }});
  highlight();

  // ====== Sidebar filter ======
  var search = document.getElementById('docsSearch');
  if (search) {{
    search.addEventListener('input', function() {{
      var q = search.value.trim().toLowerCase();
      sidebarLinks.forEach(function(a) {{
        var li = a.parentElement;
        if (!q) {{ li.classList.remove('hidden'); return; }}
        li.classList.toggle('hidden', a.textContent.toLowerCase().indexOf(q) === -1);
      }});
    }});
  }}
</script>

</body>
</html>"""
