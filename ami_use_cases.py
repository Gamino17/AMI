"""Página /use-cases: galería de 6 recetas reales de uso de AMI.

Pensada para reuniones con socios: cada card es una receta auto-contenida
con resumen, tools usadas, snippet copiable de Python SDK y outputs esperados.

Bilingüe ES/EN inline vía `data-lang="es"/"en"` (compat con el toggle de
ami_chrome.py). Stdlib pura, sin deps externas. CSS embebido para que la
página sea servible standalone sin tocar ami_api.py.

Brand-agnóstica: cuando un caso usa un motor de voz/LLM, se nombra
genéricamente ("real-time voice engine"/"motor de voz en tiempo real") —
nunca marcas de terceros. La regla del socio.
"""
from __future__ import annotations

from ami_chrome import chrome_css, chrome_js, footer_html, header_html


# ---------------------------------------------------------------------------
# Datos de las 6 recetas. Cada caso es brand-agnóstico. Snippets son Python
# SDK (~8-12 líneas), pensados para que un dev los copie literalmente.
# ---------------------------------------------------------------------------

_CASES = [
    {
        "id": "appointment-reminder",
        "icon": "◐",
        "title_es": "Recordatorio de cita médica",
        "title_en": "Medical appointment reminder",
        "sector_es": "Salud",
        "sector_en": "Healthcare",
        "summary_es": (
            "El agente de la clínica manda un SMS 24 h antes de la cita. "
            "Si el paciente responde, AMI dispara un webhook y el agente "
            "confirma, reagenda o cancela en su CRM."
        ),
        "summary_en": (
            "The clinic agent sends an SMS 24 h before the appointment. "
            "When the patient replies, AMI fires a webhook and the agent "
            "confirms, reschedules or cancels in its CRM."
        ),
        "tools": ["ami.send_sms", "webhook: sms.inbound"],
        "code": (
            'from ami import AMI\n'
            'ami = AMI(api_key="ami_live_...")\n'
            '\n'
            'ami.send_sms(\n'
            '    to="+34911234567",\n'
            '    body="Hola Marta, tu cita es mañana 10:00. '
            'Responde OK, R para reagendar.",\n'
            ')\n'
            '\n'
            '# El webhook sms.inbound entrega la respuesta al agente.\n'
        ),
        "output_es": (
            'Respuesta esperada: {"id":"sms_01H...","status":"queued"}. '
            "Cuando el paciente contesta, llega un POST sms.inbound con el cuerpo."
        ),
        "output_en": (
            'Expected response: {"id":"sms_01H...","status":"queued"}. '
            "When the patient replies, a POST sms.inbound is delivered with the body."
        ),
    },
    {
        "id": "friendly-collections",
        "icon": "◆",
        "title_es": "Cobros amistosos escalables",
        "title_en": "Friendly collections, escalated",
        "sector_es": "Fintech",
        "sector_en": "Fintech",
        "summary_es": (
            "Al vencer una factura, el agente empieza por un SMS suave. "
            "Si no hay respuesta en 48 h, escala a una llamada de voz "
            "bridgeada a un motor de voz en tiempo real con tono empático."
        ),
        "summary_en": (
            "When an invoice is overdue, the agent starts with a gentle SMS. "
            "If there is no reply in 48 h, it escalates to a voice call "
            "bridged to a real-time voice engine with an empathetic tone."
        ),
        "tools": ["ami.send_sms", "ami.place_call"],
        "code": (
            'from ami import AMI\n'
            'ami = AMI(api_key="ami_live_...")\n'
            '\n'
            'ami.send_sms(to=customer, body="Recordatorio: tu factura '
            '#A-1042 vence hoy.")\n'
            '\n'
            '# 48 h después, si sigue sin pagar:\n'
            'ami.place_call(\n'
            '    to=customer,\n'
            '    callback_sip_uri="sip:collections@voice.acme.ai",\n'
            ')\n'
        ),
        "output_es": (
            "El SDK devuelve un call_id. Cuando el cliente descuelga, "
            "AMI bridgea la llamada al motor de voz indicado en callback_sip_uri."
        ),
        "output_en": (
            "The SDK returns a call_id. When the customer picks up, "
            "AMI bridges the call to the voice engine at callback_sip_uri."
        ),
    },
    {
        "id": "voicebot-reception",
        "icon": "◉",
        "title_es": "Recepción por voz 24/7",
        "title_en": "24/7 voice reception",
        "sector_es": "Servicios",
        "sector_en": "Services",
        "summary_es": (
            "Cualquier llamada entrante al número del agente se enruta "
            "directamente a un motor de voz en tiempo real que actúa de "
            "recepcionista: transfiere a humano o toma mensaje."
        ),
        "summary_en": (
            "Any inbound call to the agent's number is routed straight "
            "to a real-time voice engine that acts as the receptionist: "
            "transfers to a human or takes a message."
        ),
        "tools": ["ami.set_inbound_sip_uri", "webhook: call.inbound"],
        "code": (
            'from ami import AMI\n'
            'ami = AMI(api_key="ami_live_...")\n'
            '\n'
            '# Una sola vez al provisionar el número:\n'
            'ami.set_inbound_sip_uri(\n'
            '    mid="mid_01H...",\n'
            '    sip_uri="sip:reception@voice.acme.ai",\n'
            ')\n'
            '\n'
            '# A partir de ahora todas las llamadas entrantes\n'
            '# se entregan al motor de voz vía SIP.\n'
        ),
        "output_es": (
            "Cada llamada entrante dispara un webhook call.inbound con "
            "el caller, timestamp y SIP session id para auditoría."
        ),
        "output_en": (
            "Every inbound call fires a call.inbound webhook with the "
            "caller, timestamp and SIP session id for audit."
        ),
    },
    {
        "id": "nps-survey",
        "icon": "◓",
        "title_es": "Encuesta NPS post-venta",
        "title_en": "Post-purchase NPS survey",
        "sector_es": "Retail / SaaS",
        "sector_en": "Retail / SaaS",
        "summary_es": (
            "Tres días después de la compra, el agente envía un SMS con "
            "una sola pregunta. La respuesta vuelve por webhook sms.inbound "
            "y se almacena en el panel del cliente."
        ),
        "summary_en": (
            "Three days after the purchase, the agent sends a one-question "
            "SMS. The reply comes back via sms.inbound webhook and is "
            "stored in the customer panel."
        ),
        "tools": ["ami.send_sms", "webhook: sms.inbound"],
        "code": (
            'from ami import AMI\n'
            'ami = AMI(api_key="ami_live_...")\n'
            '\n'
            'ami.send_sms(\n'
            '    to=customer.phone,\n'
            '    body=(\n'
            '        "Del 0 al 10, ¿qué tan probable es que '
            'recomiendes Acme?\\n"\n'
            '        "Responde con un número."\n'
            '    ),\n'
            ')\n'
        ),
        "output_es": (
            "El webhook sms.inbound entrega la respuesta. Un parser "
            "local extrae el dígito y lo guarda contra el order_id."
        ),
        "output_en": (
            "The sms.inbound webhook delivers the reply. A local parser "
            "extracts the digit and stores it against the order_id."
        ),
    },
    {
        "id": "agent-2fa",
        "icon": "◈",
        "title_es": "2FA entre agentes",
        "title_en": "Agent-to-agent 2FA",
        "sector_es": "Seguridad / Identity",
        "sector_en": "Security / Identity",
        "summary_es": (
            "Un servicio envía un código de verificación al número del "
            "agente. El agente lo recibe vía webhook, lo parsea y lo "
            "presenta en su flujo. Útil para verificación entre agentes."
        ),
        "summary_en": (
            "A service sends a verification code to the agent's number. "
            "The agent receives it via webhook, parses it and presents it "
            "in its flow. Useful for agent-to-agent verification."
        ),
        "tools": ["webhook: sms.inbound", "local parsing"],
        "code": (
            'import re\n'
            '\n'
            'def on_sms_inbound(event):\n'
            '    body = event["sms"]["body"]\n'
            '    match = re.search(r"\\b(\\d{6})\\b", body)\n'
            '    if not match:\n'
            '        return\n'
            '    code = match.group(1)\n'
            '    agent.present_otp(code)   # entrega al flujo que lo pidió\n'
        ),
        "output_es": (
            "El handler corre dentro del worker del agente. El código "
            "queda firmado por AMI en el evento y queda auditado."
        ),
        "output_en": (
            "The handler runs inside the agent worker. The code is "
            "signed by AMI in the event and remains auditable."
        ),
    },
    {
        "id": "delivery-confirm",
        "icon": "◑",
        "title_es": "Confirmación de delivery",
        "title_en": "Delivery confirmation",
        "sector_es": "Logística / E-commerce",
        "sector_en": "Logistics / E-commerce",
        "summary_es": (
            "Cuando el repartidor está a 15 min, el agente avisa por SMS "
            "con un link de tracking. Si el cliente responde 'delay', el "
            "agente revisa el historial y propone una nueva ventana."
        ),
        "summary_en": (
            "When the courier is 15 min away, the agent sends an SMS "
            "with a tracking link. If the customer replies 'delay', the "
            "agent reviews the history and proposes a new window."
        ),
        "tools": ["ami.send_sms", "webhook: sms.inbound", "ami.list_sms"],
        "code": (
            'from ami import AMI\n'
            'ami = AMI(api_key="ami_live_...")\n'
            '\n'
            'ami.send_sms(\n'
            '    to=order.phone,\n'
            '    body=f"Tu pedido llega en ~15 min. Track: '
            '{order.tracking_url}",\n'
            ')\n'
            '\n'
            '# Si responde "delay", recuperamos el hilo completo:\n'
            'history = ami.list_sms(peer=order.phone, limit=20)\n'
        ),
        "output_es": (
            "list_sms devuelve el hilo bidireccional ordenado por timestamp, "
            "listo para que el agente razone sobre el contexto del cliente."
        ),
        "output_en": (
            "list_sms returns the bidirectional thread sorted by timestamp, "
            "ready for the agent to reason over customer context."
        ),
    },
]


# ---------------------------------------------------------------------------
# CSS embebido. Aislado bajo .uc-* para no colisionar con chrome ni con
# otras páginas (la landing y /experience comparten variables similares).
# ---------------------------------------------------------------------------

_CSS = """
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
    --code-bg: #0a0a12;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background: var(--bg);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--accent); color: #fff; }

  /* HERO ------------------------------------------------------------- */
  .uc-hero {
    position: relative;
    padding: 6rem 1.5rem 3rem;
    text-align: center;
    overflow: hidden;
  }
  .uc-hero::before, .uc-hero::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    filter: blur(140px);
    pointer-events: none;
    z-index: 0;
  }
  .uc-hero::before {
    width: 560px; height: 560px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 70%);
    top: -30%; left: -10%;
    opacity: 0.35;
  }
  .uc-hero::after {
    width: 480px; height: 480px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 70%);
    top: -10%; right: -10%;
    opacity: 0.28;
  }
  .uc-hero .wrap {
    position: relative;
    z-index: 1;
    max-width: 880px;
    margin: 0 auto;
  }
  .uc-eyebrow {
    display: inline-block;
    font-family: var(--mono);
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.18em;
    margin-bottom: 1.2rem;
  }
  .uc-hero h1 {
    font-weight: 800;
    letter-spacing: -0.035em;
    line-height: 1.02;
    font-size: clamp(2.4rem, 6vw, 4.4rem);
    margin: 0 0 1.2rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .uc-hero p {
    font-size: 1.1rem;
    color: var(--ink-soft);
    max-width: 640px;
    margin: 0 auto;
  }

  /* GRID -------------------------------------------------------------- */
  .uc-grid {
    max-width: 1280px;
    margin: 0 auto;
    padding: 2rem 1.5rem 5rem;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1.4rem;
  }
  @media (max-width: 1024px) {
    .uc-grid { grid-template-columns: repeat(2, 1fr); }
  }
  @media (max-width: 720px) {
    .uc-grid { grid-template-columns: 1fr; padding: 1rem 1rem 3rem; }
  }

  /* CARD -------------------------------------------------------------- */
  .uc-card {
    background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1.6rem 1.5rem 1.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.9rem;
    position: relative;
    transition: border-color 0.2s, transform 0.2s;
  }
  .uc-card:hover {
    border-color: rgba(139,108,255,0.45);
    transform: translateY(-2px);
  }
  .uc-card::before {
    content: "";
    position: absolute;
    inset: -1px;
    border-radius: 14px;
    padding: 1px;
    background: linear-gradient(135deg, transparent 30%, rgba(139,108,255,0.25) 60%, transparent 90%);
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask-composite: exclude;
    opacity: 0;
    transition: opacity 0.2s;
    pointer-events: none;
  }
  .uc-card:hover::before { opacity: 1; }

  .uc-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.8rem;
  }
  .uc-icon {
    width: 38px; height: 38px;
    display: flex; align-items: center; justify-content: center;
    border-radius: 10px;
    background: var(--accent-bg);
    color: var(--accent);
    font-family: var(--mono);
    font-size: 1.25rem;
    line-height: 1;
    flex-shrink: 0;
  }
  .uc-badge {
    font-family: var(--mono);
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-soft);
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--line);
    border-radius: 99px;
    padding: 0.28rem 0.65rem;
    white-space: nowrap;
  }
  .uc-card h2 {
    font-family: var(--sans);
    font-size: 1.18rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin: 0;
    line-height: 1.25;
  }
  .uc-summary {
    color: var(--ink-soft);
    font-size: 0.92rem;
    line-height: 1.55;
    margin: 0;
  }

  .uc-section-label {
    font-family: var(--mono);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--ink-mute);
    margin: 0.3rem 0 0.45rem;
  }
  .uc-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .uc-chip {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--accent-2);
    background: rgba(93,209,255,0.07);
    border: 1px solid rgba(93,209,255,0.18);
    padding: 0.2rem 0.55rem;
    border-radius: 6px;
  }
  .uc-code {
    background: var(--code-bg);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.85rem 0.9rem;
    margin: 0;
    font-family: var(--mono);
    font-size: 0.76rem;
    line-height: 1.55;
    color: #cdd0e0;
    overflow-x: auto;
    white-space: pre;
  }
  .uc-output {
    font-family: var(--mono);
    font-size: 0.74rem;
    color: var(--ink-soft);
    border-left: 2px solid var(--accent);
    padding: 0.35rem 0.7rem;
    background: rgba(139,108,255,0.05);
    border-radius: 0 6px 6px 0;
    line-height: 1.5;
    margin: 0;
  }
"""


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _card_html(case: dict) -> str:
    """Renderiza una card. Cada texto va duplicado ES/EN inline para que el
    toggle de chrome_js() funcione sin recargar la página."""
    chips = "".join(f'<span class="uc-chip">{t}</span>' for t in case["tools"])
    return f"""
      <article class="uc-card" id="case-{case['id']}">
        <div class="uc-card-head">
          <div class="uc-icon" aria-hidden="true">{case['icon']}</div>
          <span class="uc-badge">
            <span data-chrome-lang="es">{case['sector_es']}</span>
            <span data-chrome-lang="en">{case['sector_en']}</span>
          </span>
        </div>
        <h2>
          <span data-chrome-lang="es">{case['title_es']}</span>
          <span data-chrome-lang="en">{case['title_en']}</span>
        </h2>
        <p class="uc-summary">
          <span data-chrome-lang="es">{case['summary_es']}</span>
          <span data-chrome-lang="en">{case['summary_en']}</span>
        </p>

        <div>
          <p class="uc-section-label">
            <span data-chrome-lang="es">Tools</span>
            <span data-chrome-lang="en">Tools</span>
          </p>
          <div class="uc-chips">{chips}</div>
        </div>

        <div>
          <p class="uc-section-label">
            <span data-chrome-lang="es">Snippet</span>
            <span data-chrome-lang="en">Snippet</span>
          </p>
          <pre class="uc-code"><code>{_escape(case['code'])}</code></pre>
        </div>

        <div>
          <p class="uc-section-label">
            <span data-chrome-lang="es">Salida esperada</span>
            <span data-chrome-lang="en">Expected output</span>
          </p>
          <p class="uc-output">
            <span data-chrome-lang="es">{case['output_es']}</span>
            <span data-chrome-lang="en">{case['output_en']}</span>
          </p>
        </div>
      </article>
    """


def _escape(s: str) -> str:
    """Escape mínimo para HTML dentro de <pre><code>. No tocamos comillas
    porque los snippets las usan tal cual y van dentro de un <code>."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


def render_use_cases_page(lang: str = "es") -> str:
    """Página /use-cases con galería de 6 recetas.

    Bilingüe inline (ES/EN). Se sirve igual con `?lang=es` o `?lang=en`;
    el parámetro `lang` solo decide el atributo `<html lang>` inicial.
    El toggle de chrome cambia entre los dos visualmente sin recargar.
    """
    cards = "\n".join(_card_html(c) for c in _CASES)
    title = "AMI · Use cases" if lang == "en" else "AMI · Casos de uso"
    description = (
        "Six real-world recipes for AI agents using AMI: appointment "
        "reminders, friendly collections, 24/7 voicebot reception, NPS "
        "surveys, agent-to-agent 2FA, and delivery confirmations."
        if lang == "en"
        else
        "Seis recetas reales para agentes AI sobre AMI: recordatorios de "
        "cita, cobros amistosos, recepción por voz 24/7, encuestas NPS, "
        "2FA entre agentes y confirmaciones de delivery."
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
{header_html(active="use-cases", lang=lang)}

<main>
  <section class="uc-hero">
    <div class="wrap">
      <span class="uc-eyebrow">
        <span data-chrome-lang="es">Casos de uso</span>
        <span data-chrome-lang="en">Use cases</span>
      </span>
      <h1>
        <span data-chrome-lang="es">Cómo se usa AMI en la práctica</span>
        <span data-chrome-lang="en">How AMI is used in practice</span>
      </h1>
      <p>
        <span data-chrome-lang="es">Seis recetas reales que un agente AI puede ejecutar hoy con un número, SMS y voz propios. Copia el snippet y empieza.</span>
        <span data-chrome-lang="en">Six real recipes an AI agent can run today with its own number, SMS and voice. Copy the snippet and ship it.</span>
      </p>
    </div>
  </section>

  <section class="uc-grid">
    {cards}
  </section>
</main>

{footer_html(lang=lang)}

<script>
(function() {{
{chrome_js()}
}})();
</script>
</body>
</html>
"""
