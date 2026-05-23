"""Página /security: whitepaper de seguridad resumido, formato landing.

Pensada para socios que evalúan AMI antes de firmar acceso a infraestructura
real (números, SMS, voz). Un evaluador serio busca respuestas concretas a
cinco preguntas reflejas: aislamiento, autenticación, salida controlada,
límites duros, y trazabilidad. Esta página las contesta en orden y con la
exactitud técnica del propio repo (rangos IP bloqueados, tamaños de body
cap, ventanas de anti-replay, etc.) — no marketing.

Notas:

- El módulo `ami_security.py` ya existe en el repo con los helpers SSRF.
  Esta página es solo render HTML; va separada como `ami_security_page.py`
  para no pisar nada.
- Bilingüe inline ES/EN, mismo patrón que /use-cases y /pricing.
- Brand-agnóstica: nunca se nombra un proveedor cloud, telco o LLM
  concreto. Las marcas prohibidas del workflow brand-check no aparecen.
- Reusa chrome (header_html + footer_html) con active="security".
"""
from __future__ import annotations

from ami_chrome import REPO_URL, chrome_css, chrome_js, footer_html, header_html


# ---------------------------------------------------------------------------
# Datos de las 8 secciones del whitepaper resumido. Cada sección lleva un
# icono ASCII (mismo lenguaje visual que /use-cases) y 2-3 párrafos densos.
# ---------------------------------------------------------------------------

_SECTIONS = [
    {
        "id": "multitenancy",
        "num": "01",
        "icon": "◐",
        "title_es": "Aislamiento multi-tenant",
        "title_en": "Multi-tenant isolation",
        "body_es": [
            "Todo recurso en AMI vive bajo un <code>account_id</code> "
            "explícito. Cada endpoint GET aplica un scope obligatorio "
            "antes de leer; el cliente A no puede ver recursos del cliente "
            "B aunque conozca su identificador.",
            "Cuando un recurso existe pero pertenece a otra cuenta, "
            "devolvemos <code>404 Not Found</code> en lugar de "
            "<code>403 Forbidden</code> para no filtrar la existencia "
            "del recurso por canal lateral.",
            "Hay 29 tests específicos de multi-tenancy que verifican el "
            "scoping en cada endpoint de lectura, escritura y listado.",
        ],
        "body_en": [
            "Every resource in AMI lives under an explicit "
            "<code>account_id</code>. Each GET endpoint applies a mandatory "
            "scope before reading; client A cannot see client B's resources "
            "even if A knows the identifier.",
            "When a resource exists but belongs to another account we "
            "return <code>404 Not Found</code> instead of "
            "<code>403 Forbidden</code> so existence is not leaked via "
            "side channel.",
            "29 dedicated multi-tenancy tests verify scoping on every "
            "read, write and list endpoint.",
        ],
    },
    {
        "id": "auth",
        "num": "02",
        "icon": "◆",
        "title_es": "Niveles de autenticación",
        "title_en": "Authentication levels",
        "body_es": [
            "<strong>Nivel 1 — Customer API key.</strong> El cliente "
            "humano se autentica con una API key emitida en alta. La "
            "key se almacena exclusivamente como SHA-256 hash: nunca "
            "podemos restaurarla, solo rotarla.",
            "<strong>Nivel 2 — Agent token.</strong> Cada agente recibe "
            "un <code>agent_token</code> scoped por MID (mobile identity). "
            "El token es rotable de forma independiente, por si un agente "
            "se compromete sin afectar al resto de la cuenta.",
            "<strong>Admin master key.</strong> Operaciones de "
            "administración (gestión de cuentas, kill-switch) usan una "
            "master key separada, fuera del flujo de cliente, con "
            "auditoría obligatoria.",
        ],
        "body_en": [
            "<strong>Level 1 — Customer API key.</strong> The human "
            "client authenticates with an API key issued at signup. The "
            "key is stored only as a SHA-256 hash: we can never restore "
            "it, only rotate it.",
            "<strong>Level 2 — Agent token.</strong> Each agent receives "
            "an <code>agent_token</code> scoped to a single MID (mobile "
            "identity). The token rotates independently so a compromised "
            "agent does not blast-radius the rest of the account.",
            "<strong>Admin master key.</strong> Administrative operations "
            "(account management, kill-switch) use a separate master key "
            "outside the customer flow, with mandatory audit trail.",
        ],
    },
    {
        "id": "webhooks",
        "num": "03",
        "icon": "◉",
        "title_es": "Webhooks salientes",
        "title_en": "Outbound webhooks",
        "body_es": [
            "Firmamos cada entrega con HMAC-SHA256 sobre <code>timestamp + "
            "body</code>. El receptor verifica la firma y rechaza payloads "
            "con timestamp fuera de una ventana de 5 minutos, lo que "
            "neutraliza replay attacks.",
            "Antes de cada conexión saliente pasamos la URL por un guard "
            "SSRF que resuelve DNS y rechaza cualquier IP en rangos "
            "<code>loopback</code>, <code>private</code>, <code>link-local</code>, "
            "<code>multicast</code> o <code>reserved</code> — incluyendo "
            "el endpoint de metadata cloud <code>169.254.169.254</code>.",
            "El cliente HTTP no sigue redirects 3xx, y el body de respuesta "
            "se trunca a 64 KiB. Así un endpoint malicioso no nos arrastra "
            "ni a un host interno ni a un buffer overflow de memoria.",
        ],
        "body_en": [
            "We sign every delivery with HMAC-SHA256 over "
            "<code>timestamp + body</code>. The receiver verifies the "
            "signature and rejects payloads whose timestamp is outside "
            "a 5-minute window, which neutralises replay attacks.",
            "Before each outbound connection the URL goes through an "
            "SSRF guard that resolves DNS and rejects any IP in "
            "<code>loopback</code>, <code>private</code>, <code>link-local</code>, "
            "<code>multicast</code> or <code>reserved</code> ranges — "
            "including the cloud metadata endpoint "
            "<code>169.254.169.254</code>.",
            "The HTTP client does not follow 3xx redirects and the "
            "response body is capped at 64 KiB. A malicious endpoint "
            "cannot drag us into an internal host or a memory blow-up.",
        ],
    },
    {
        "id": "limits",
        "num": "04",
        "icon": "◇",
        "title_es": "Rate limits y spending",
        "title_en": "Rate limits and spending",
        "body_es": [
            "Cada MID lleva su propio enforcement: rate limit por segundo, "
            "presupuesto mensual y allowlist de países destino. Los "
            "chequeos se aplican <em>antes</em> de cursar la operación "
            "al telco, no después; un agente que se descontrola no llega "
            "a generar coste real.",
            "Cuando un límite salta, devolvemos <code>429 Too Many "
            "Requests</code> con una razón específica (<code>rate_per_second</code>, "
            "<code>monthly_budget</code>, <code>country_not_allowed</code>) para "
            "que el agente pueda decidir en consecuencia sin reintentar a ciegas.",
        ],
        "body_en": [
            "Each MID carries its own enforcement: per-second rate "
            "limit, monthly spending budget and destination-country "
            "allowlist. Checks run <em>before</em> we forward the "
            "operation to the telco, not after; a runaway agent never "
            "generates real cost.",
            "When a limit fires we return <code>429 Too Many Requests</code> "
            "with a specific reason (<code>rate_per_second</code>, "
            "<code>monthly_budget</code>, <code>country_not_allowed</code>) so "
            "the agent can act on it instead of retrying blindly.",
        ],
    },
    {
        "id": "data-at-rest",
        "num": "05",
        "icon": "◈",
        "title_es": "Datos en reposo",
        "title_en": "Data at rest",
        "body_es": [
            "La base de datos SQLite vive en un fichero con permisos "
            "<code>0o600</code> (lectura/escritura solo del owner). En "
            "Render, el filesystem persistente está aislado a la "
            "instancia y no es accesible desde otros servicios.",
            "Los secretos de webhook se guardan en plano porque hacen "
            "falta para firmar el HMAC saliente — no hay alternativa "
            "criptográficamente honesta. La mitigación está en el "
            "perímetro: solo el owner del proceso lee el fichero.",
        ],
        "body_en": [
            "The SQLite database lives in a file with <code>0o600</code> "
            "permissions (read/write only by owner). On Render, the "
            "persistent filesystem is isolated to the instance and is "
            "not reachable from other services.",
            "Webhook secrets are stored in plaintext because they are "
            "needed to sign the outbound HMAC — there is no "
            "cryptographically honest alternative. Mitigation lives at "
            "the perimeter: only the process owner reads the file.",
        ],
    },
    {
        "id": "audit",
        "num": "06",
        "icon": "◊",
        "title_es": "Audit log",
        "title_en": "Audit log",
        "body_es": [
            "Cada evento relevante (alta, rotación de key, SMS enviado, "
            "llamada, webhook entregado o fallido, kill-switch) queda "
            "persistido en una tabla append-only.",
            "El endpoint <code>GET /v1/events</code> filtra por tenant "
            "de forma obligatoria — un cliente solo ve sus propios "
            "eventos. Los payloads del log llevan lo mínimo para "
            "reconstruir lo ocurrido y omiten PII completa: nunca "
            "logueamos el cuerpo de un SMS ni el audio de una llamada.",
        ],
        "body_en": [
            "Every relevant event (signup, key rotation, SMS sent, "
            "call, webhook delivered or failed, kill-switch) is "
            "persisted to an append-only table.",
            "The endpoint <code>GET /v1/events</code> filters by tenant "
            "by design — a client only sees its own events. Log payloads "
            "carry the minimum needed to reconstruct what happened and "
            "omit full PII: we never log the body of an SMS or the audio "
            "of a call.",
        ],
    },
    {
        "id": "brand-ci",
        "num": "07",
        "icon": "◌",
        "title_es": "Brand check en CI",
        "title_en": "Brand check enforced in CI",
        "body_es": [
            "Un workflow de CI hace grep en cada PR sobre las superficies "
            "públicas (API, docs, README, partnership). Si entra una marca "
            "comercial prohibida, el job falla y bloquea el merge.",
            "Es una regla del socio: AMI se describe por lo que es, no "
            "por contra-marcas. La automatizamos para que ningún humano "
            "tenga que recordarla en cada cambio.",
        ],
        "body_en": [
            "A CI workflow greps each PR against public surfaces (API, "
            "docs, README, partnership). If a prohibited commercial brand "
            "slips in, the job fails and blocks merge.",
            "It is a partner rule: AMI is described by what it is, not "
            "against other brands. We automate it so no human has to "
            "remember on every change.",
        ],
    },
    {
        "id": "tests",
        "num": "08",
        "icon": "◍",
        "title_es": "Tests de regresión",
        "title_en": "Regression tests",
        "body_es": [
            "305 tests verdes en cada commit, de los cuales 29 son "
            "específicos de seguridad: scoping multi-tenant, SSRF, "
            "header injection, validación de entrada, DoS por payload, "
            "y los residuales de la auditoría externa.",
            "Si una regresión rompe cualquiera de los 29, el merge se "
            "bloquea. La seguridad no depende de la disciplina de un "
            "revisor humano.",
        ],
        "body_en": [
            "305 green tests on every commit, of which 29 are dedicated "
            "to security: multi-tenant scoping, SSRF, header injection, "
            "input validation, payload DoS, and the residuals from the "
            "external audit.",
            "If a regression breaks any of the 29, merge is blocked. "
            "Security does not depend on a human reviewer's discipline.",
        ],
    },
]


# ---------------------------------------------------------------------------
# CSS aislado bajo .sec-*
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
    --code-bg: #0a0a12;
  }
  * { box-sizing: border-box; }
  html, body { background: var(--bg); color: var(--ink); margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  a { color: var(--accent-2); }
  code {
    font-family: var(--mono);
    font-size: 0.85em;
    background: rgba(139,108,255,0.08);
    color: #cdd0e0;
    padding: 0.05rem 0.35rem;
    border-radius: 4px;
    border: 1px solid rgba(139,108,255,0.15);
  }

  .sec-hero {
    position: relative;
    padding: 6rem 1.5rem 3rem;
    text-align: center;
    overflow: hidden;
  }
  .sec-hero::before, .sec-hero::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    filter: blur(140px);
    pointer-events: none;
    z-index: 0;
  }
  .sec-hero::before {
    width: 560px; height: 560px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 70%);
    top: -30%; left: -10%;
    opacity: 0.32;
  }
  .sec-hero::after {
    width: 480px; height: 480px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 70%);
    top: -10%; right: -10%;
    opacity: 0.26;
  }
  .sec-hero .wrap {
    position: relative; z-index: 1;
    max-width: 880px; margin: 0 auto;
  }
  .sec-eyebrow {
    display: inline-block;
    font-family: var(--mono);
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.18em;
    margin-bottom: 1.1rem;
  }
  .sec-hero h1 {
    font-weight: 800;
    letter-spacing: -0.035em;
    line-height: 1.04;
    font-size: clamp(2.2rem, 5.5vw, 3.8rem);
    margin: 0 0 1rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .sec-hero p {
    font-size: 1.05rem;
    color: var(--ink-soft);
    max-width: 640px;
    margin: 0 auto;
    line-height: 1.55;
  }

  .sec-toc {
    max-width: 880px;
    margin: 1rem auto 0;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.4rem;
  }
  .sec-toc a {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--ink-soft);
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--line);
    border-radius: 99px;
    padding: 0.32rem 0.7rem;
    text-decoration: none;
  }
  .sec-toc a:hover { color: var(--ink); border-color: rgba(139,108,255,0.35); }

  .sec-body {
    max-width: 880px;
    margin: 0 auto;
    padding: 2rem 1.5rem 1rem;
  }
  .sec-block {
    display: grid;
    grid-template-columns: 64px 1fr;
    gap: 1.2rem;
    padding: 1.8rem 0;
    border-bottom: 1px solid var(--line);
  }
  .sec-block:last-of-type { border-bottom: none; }
  @media (max-width: 720px) {
    .sec-block { grid-template-columns: 1fr; gap: 0.7rem; }
  }
  .sec-icon {
    width: 56px; height: 56px;
    display: flex; align-items: center; justify-content: center;
    border-radius: 14px;
    background: rgba(139,108,255,0.08);
    border: 1px solid rgba(139,108,255,0.22);
    color: var(--accent);
    font-family: var(--mono);
    font-size: 1.6rem;
    line-height: 1;
  }
  .sec-num {
    font-family: var(--mono);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--ink-mute);
    margin-bottom: 0.35rem;
  }
  .sec-block h2 {
    font-family: var(--sans);
    font-weight: 700;
    font-size: 1.5rem;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin: 0 0 0.7rem;
    line-height: 1.2;
  }
  .sec-block p {
    color: var(--ink-soft);
    font-size: 0.97rem;
    line-height: 1.62;
    margin: 0 0 0.7rem;
  }
  .sec-block p strong { color: var(--ink); font-weight: 600; }

  .sec-audit {
    max-width: 880px;
    margin: 2rem auto 5rem;
    padding: 1.8rem 1.8rem 1.6rem;
    background: linear-gradient(180deg, rgba(139,108,255,0.08) 0%, rgba(139,108,255,0.02) 100%);
    border: 1px solid rgba(139,108,255,0.30);
    border-radius: 14px;
  }
  .sec-audit-label {
    font-family: var(--mono);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 0.7rem;
  }
  .sec-audit h3 {
    font-family: var(--sans);
    font-weight: 700;
    font-size: 1.25rem;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin: 0 0 0.6rem;
  }
  .sec-audit p {
    color: var(--ink-soft);
    font-size: 0.95rem;
    line-height: 1.6;
    margin: 0 0 0.5rem;
  }
  .sec-audit-stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.7rem;
    margin: 1.1rem 0 0.4rem;
  }
  @media (max-width: 620px) { .sec-audit-stats { grid-template-columns: 1fr; } }
  .sec-stat {
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.8rem 0.9rem;
  }
  .sec-stat .num {
    font-family: var(--mono);
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--ink);
    line-height: 1;
  }
  .sec-stat .lbl {
    font-family: var(--mono);
    font-size: 0.7rem;
    color: var(--ink-mute);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-top: 0.4rem;
  }
  .sec-audit a {
    font-family: var(--mono);
    font-size: 0.82rem;
    color: var(--accent-2);
    text-decoration: none;
    border-bottom: 1px solid rgba(93,209,255,0.25);
  }
  .sec-audit a:hover { color: var(--ink); }
"""


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _section_html(s: dict) -> str:
    paragraphs_es = "".join(f"<p>{p}</p>" for p in s["body_es"])
    paragraphs_en = "".join(f"<p>{p}</p>" for p in s["body_en"])
    return f"""
      <section class="sec-block" id="sec-{s['id']}">
        <div class="sec-icon" aria-hidden="true">{s['icon']}</div>
        <div>
          <div class="sec-num">{s['num']}</div>
          <h2>
            <span data-chrome-lang="es">{s['title_es']}</span>
            <span data-chrome-lang="en">{s['title_en']}</span>
          </h2>
          <div data-chrome-lang="es">{paragraphs_es}</div>
          <div data-chrome-lang="en">{paragraphs_en}</div>
        </div>
      </section>
    """


def _toc_html() -> str:
    items = []
    for s in _SECTIONS:
        items.append(
            f'<a href="#sec-{s["id"]}">'
            f'<span data-chrome-lang="es">{s["num"]} · {s["title_es"]}</span>'
            f'<span data-chrome-lang="en">{s["num"]} · {s["title_en"]}</span>'
            f'</a>'
        )
    return f'<nav class="sec-toc">{"".join(items)}</nav>'


# ---------------------------------------------------------------------------
# Render principal
# ---------------------------------------------------------------------------

def render_security_page(lang: str = "es") -> str:
    """Página /security con whitepaper resumido."""
    blocks = "\n".join(_section_html(s) for s in _SECTIONS)
    title = "AMI · Security" if lang == "en" else "AMI · Seguridad"
    description = (
        "Security at AMI — a short whitepaper: multi-tenant isolation, "
        "authentication levels, outbound webhook hardening, rate limits, "
        "data at rest, audit log, CI brand check, and regression tests."
        if lang == "en"
        else
        "Seguridad en AMI — whitepaper resumido: aislamiento multi-tenant, "
        "niveles de autenticación, hardening de webhooks salientes, rate "
        "limits, datos en reposo, audit log, brand check en CI y tests "
        "de regresión."
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
{header_html(active="security", lang=lang)}

<main>
  <section class="sec-hero">
    <div class="wrap">
      <span class="sec-eyebrow">
        <span data-chrome-lang="es">Seguridad</span>
        <span data-chrome-lang="en">Security</span>
      </span>
      <h1>
        <span data-chrome-lang="es">Seguridad en AMI · whitepaper resumido</span>
        <span data-chrome-lang="en">Security at AMI · short whitepaper</span>
      </h1>
      <p>
        <span data-chrome-lang="es">Cómo aislamos cuentas, controlamos lo que sale del perímetro, limitamos el blast-radius de un agente comprometido y hacemos que ninguna regresión pase desapercibida.</span>
        <span data-chrome-lang="en">How we isolate accounts, control what leaves the perimeter, limit the blast-radius of a compromised agent, and ensure no regression slips by unnoticed.</span>
      </p>
    </div>
    {_toc_html()}
  </section>

  <article class="sec-body">
    {blocks}
  </article>

  <aside class="sec-audit">
    <div class="sec-audit-label">
      <span data-chrome-lang="es">Auditoría externa</span>
      <span data-chrome-lang="en">External audit</span>
    </div>
    <h3>
      <span data-chrome-lang="es">Auditado por cuatro revisores independientes</span>
      <span data-chrome-lang="en">Audited by four independent reviewers</span>
    </h3>
    <p>
      <span data-chrome-lang="es">Cuatro auditores externos revisaron auth y scoping, SSRF, validación de entrada y DoS, y manejo de PII. Todos los hallazgos CRITICAL y HIGH están cerrados.</span>
      <span data-chrome-lang="en">Four external reviewers checked auth and scoping, SSRF, input validation and DoS, and PII handling. All CRITICAL and HIGH findings are closed.</span>
    </p>
    <div class="sec-audit-stats">
      <div class="sec-stat">
        <div class="num">4</div>
        <div class="lbl">
          <span data-chrome-lang="es">Auditores</span>
          <span data-chrome-lang="en">Reviewers</span>
        </div>
      </div>
      <div class="sec-stat">
        <div class="num">0</div>
        <div class="lbl">
          <span data-chrome-lang="es">Críticos abiertos</span>
          <span data-chrome-lang="en">Open criticals</span>
        </div>
      </div>
      <div class="sec-stat">
        <div class="num">305</div>
        <div class="lbl">
          <span data-chrome-lang="es">Tests verdes</span>
          <span data-chrome-lang="en">Green tests</span>
        </div>
      </div>
    </div>
    <p style="margin-top:1rem;">
      <a href="{REPO_URL}">
        <span data-chrome-lang="es">Ver detalle de la auditoría en GitHub →</span>
        <span data-chrome-lang="en">See audit details on GitHub →</span>
      </a>
    </p>
  </aside>
</main>

{footer_html(lang=lang)}

<script>
(function() {{
{chrome_js()}
}})();
</script>
</body>
</html>"""
