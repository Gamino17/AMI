"""Páginas /pricing y /calculator de AMI.

Self-contained: cada función devuelve un HTML completo con su <head>,
<style>, <header> (placeholder, lo sustituye `_with_chrome` del dispatcher
de ami_api.py) y <footer> (igual, lo sustituye `_with_chrome`).

Diseño dark ambient consistente con /spec, /docs, /experience, /diagram,
/partners — paleta #06060a / #8b6cff / #5dd1ff, tipografía Inter + JetBrains
Mono, halos blur en hero, gradient text en títulos.

Bilingüe ES/EN inline con <span data-lang="es"> / <span data-lang="en">
(CSS oculta el opuesto leyendo `html[lang="..."]`).

Brand-safe: jamás se mencionan competidores. "Wrappers de APIs",
"proveedores tradicionales", "API resellers" — la diferencia se argumenta
por arquitectura, no por contra-marca.

Las tarifas baseline para la calculadora son números genéricos de mercado
(€0.08/SMS, €0.06/min) usados sólo como punto de comparación; AMI cobra
€0.05/SMS, €0.04/min, €9/MID/mes en el plan Growth.
"""
from __future__ import annotations


# CSS común para ambas páginas. Cada función lo embebe en su <style> propio
# para mantener cada render self-contained (no comparten import en runtime
# más allá de este módulo).
_BASE_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --surface-2: #1a1a24;
    --line: #1f1f2c;
    --line-soft: #16161f;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-bg: rgba(139, 108, 255, 0.10);
    --green: #4ade80;
    --amber: #fbbf24;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    background: var(--bg);
    color: var(--ink);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }
  html[lang="es"] [data-lang="en"] { display: none; }
  html[lang="en"] [data-lang="es"] { display: none; }
  ::selection { background: var(--accent); color: #fff; }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { color: #b9e6ff; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 1.75rem; }

  /* HERO con halos */
  .hero {
    position: relative; padding: 6rem 0 3.5rem; overflow: hidden;
  }
  .hero::before, .hero::after {
    content: ""; position: absolute; pointer-events: none; z-index: 0;
    border-radius: 50%; filter: blur(120px);
  }
  .hero::before {
    width: 620px; height: 620px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 65%);
    top: -240px; left: -160px;
    opacity: 0.45;
  }
  .hero::after {
    width: 480px; height: 480px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 65%);
    top: 80px; right: -120px;
    opacity: 0.28;
  }
  .hero .wrap { position: relative; z-index: 1; }

  .eyebrow {
    display: inline-flex; align-items: center; gap: 0.5rem;
    background: var(--surface);
    border: 1px solid var(--line);
    padding: 0.35rem 0.85rem; border-radius: 999px;
    font-family: var(--mono); font-size: 0.72rem; font-weight: 500;
    color: var(--ink-soft); letter-spacing: 0.04em; text-transform: uppercase;
    margin-bottom: 1.6rem;
  }
  .eyebrow .live {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 8px var(--green);
  }

  h1.hero-title {
    font-weight: 700;
    font-size: clamp(2.6rem, 6.4vw, 4.6rem);
    line-height: 1.02; letter-spacing: -0.04em;
    margin: 0 0 1.25rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 120%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .hero-sub {
    font-size: clamp(1rem, 1.7vw, 1.2rem);
    color: var(--ink-soft);
    max-width: 640px; line-height: 1.55;
    margin: 0;
  }

  /* Section helpers */
  section.block { padding: 3.5rem 0; }
  h2.section-title {
    font-size: clamp(1.6rem, 3vw, 2.1rem);
    font-weight: 700; letter-spacing: -0.02em;
    margin: 0 0 0.6rem 0; color: var(--ink);
  }
  .section-sub {
    color: var(--ink-soft); font-size: 1rem;
    max-width: 640px; margin: 0 0 2.4rem 0;
  }
"""


def render_pricing_page(lang: str = "es") -> str:
    """Página /pricing — 3 planes side-by-side + FAQ + cierre.

    El plan central (Growth) lleva un badge "Recomendado" porque queremos
    que sea el default cognitivo. Starter es para devs probando con mock
    telco (sin riesgo, sin contrato); Enterprise es talk-to-us con SLA.

    El copy del CTA en cada plan es distinto a propósito:
      - Starter: "Empezar gratis" (no fricción)
      - Growth: "Empezar prueba" (commitment soft, sin downside framing)
      - Enterprise: "Hablamos" (humano, no formal "Contact sales")
    """
    pricing_css = _BASE_CSS + """
      /* GRID de planes */
      .plans {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1.5rem;
        margin: 0 0 1.5rem 0;
      }
      @media (max-width: 960px) {
        .plans { grid-template-columns: 1fr; }
      }
      .plan {
        position: relative;
        background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 2rem 1.75rem 2.2rem;
        display: flex; flex-direction: column;
        transition: transform 0.18s ease, border-color 0.18s ease;
      }
      .plan:hover { transform: translateY(-2px); border-color: rgba(139,108,255,0.35); }
      .plan.featured {
        border-color: rgba(139,108,255,0.55);
        background: linear-gradient(180deg, rgba(139,108,255,0.06) 0%, var(--bg-soft) 100%);
        box-shadow: 0 0 0 1px rgba(139,108,255,0.18), 0 30px 80px -30px rgba(139,108,255,0.35);
      }
      .plan .badge {
        position: absolute; top: -10px; left: 1.5rem;
        background: linear-gradient(90deg, #8b6cff, #5dd1ff);
        color: #06060a;
        font-family: var(--mono); font-size: 0.66rem; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.08em;
        padding: 0.3rem 0.7rem; border-radius: 999px;
      }
      .plan h3 {
        font-family: var(--mono); font-size: 0.78rem; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.12em;
        color: var(--ink-soft); margin: 0 0 0.4rem 0;
      }
      .plan .desc {
        font-size: 0.95rem; color: var(--ink-soft);
        margin: 0 0 1.5rem 0; min-height: 2.8rem;
      }
      .plan .price {
        font-size: 2.1rem; font-weight: 700; line-height: 1.1;
        color: var(--ink); letter-spacing: -0.02em;
        margin: 0 0 0.2rem 0;
      }
      .plan .price small {
        display: block; font-size: 0.78rem; font-weight: 500;
        color: var(--ink-soft); font-family: var(--mono);
        margin-top: 0.35rem; letter-spacing: 0;
      }
      .plan .price.custom {
        font-size: 1.7rem; color: var(--accent-2);
        font-family: var(--mono);
      }
      .plan ul.features {
        list-style: none; padding: 0; margin: 1.6rem 0 1.8rem 0;
        flex-grow: 1;
      }
      .plan ul.features li {
        padding: 0.55rem 0 0.55rem 1.5rem;
        position: relative;
        font-size: 0.92rem; color: var(--ink);
        border-bottom: 1px solid var(--line-soft);
      }
      .plan ul.features li:last-child { border-bottom: 0; }
      .plan ul.features li::before {
        content: ""; position: absolute;
        left: 0; top: 0.95rem;
        width: 6px; height: 6px; border-radius: 50%;
        background: var(--accent);
        box-shadow: 0 0 8px var(--accent);
      }
      .plan .cta {
        display: block; text-align: center;
        padding: 0.85rem 1.2rem; border-radius: 8px;
        font-family: var(--mono); font-weight: 600; font-size: 0.88rem;
        letter-spacing: 0.02em;
        border: 1px solid var(--line);
        background: var(--surface-2);
        color: var(--ink); text-decoration: none;
        transition: all 0.15s ease;
      }
      .plan .cta:hover {
        border-color: var(--accent); color: var(--ink);
      }
      .plan.featured .cta {
        background: linear-gradient(90deg, #8b6cff, #5dd1ff);
        color: #06060a; border-color: transparent;
      }
      .plan.featured .cta:hover {
        filter: brightness(1.1);
        box-shadow: 0 8px 24px rgba(139,108,255,0.35);
      }

      /* FAQ */
      .faq-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1.2rem 2rem;
      }
      @media (max-width: 760px) { .faq-grid { grid-template-columns: 1fr; } }
      .faq-item {
        padding: 1.2rem 1.4rem;
        background: var(--bg-soft);
        border: 1px solid var(--line);
        border-radius: 10px;
      }
      .faq-item h4 {
        font-size: 1rem; font-weight: 600; color: var(--ink);
        margin: 0 0 0.5rem 0;
      }
      .faq-item p {
        margin: 0; color: var(--ink-soft); font-size: 0.92rem;
        line-height: 1.6;
      }

      /* Closing strip */
      .closing {
        margin: 4rem 0 5rem;
        padding: 2.5rem 2rem;
        text-align: center;
        background: radial-gradient(circle at 50% 0%, rgba(139,108,255,0.12) 0%, transparent 60%),
                    var(--bg-soft);
        border: 1px solid var(--line);
        border-radius: 14px;
      }
      .closing p {
        max-width: 620px; margin: 0 auto;
        font-size: 1.1rem; color: var(--ink);
        line-height: 1.6;
      }
      .closing .small {
        margin-top: 0.6rem;
        font-size: 0.85rem; color: var(--ink-soft);
        font-family: var(--mono);
      }
    """

    title_es = "Pricing para la capa de protocolo"
    title_en = "Pricing for the protocol layer"
    sub_es = ("Tarifas planas, sin cargos por número activo idle, sin contrato mínimo. "
              "Pagas por uso real — SMS, minutos de voz, identidades activas.")
    sub_en = ("Flat rates, no idle-number fees, no minimum commitment. "
              "You pay for real usage — SMS, voice minutes, active identities.")

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Pricing</title>
<meta name="description" content="AMI pricing — Starter (free), Growth (€9/MID/month), Enterprise. No hidden fees, no minimums.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{pricing_css}</style>
<script>
  (function() {{
    try {{
      var l = localStorage.getItem('ami-lang');
      if (l === 'es' || l === 'en') document.documentElement.lang = l;
    }} catch(e) {{}}
  }})();
</script>
</head>
<body>

  <header></header>

  <section class="hero">
    <div class="wrap">
      <span class="eyebrow">
        <span class="live"></span>
        <span data-lang="es">Pricing · Mayo 2026</span>
        <span data-lang="en">Pricing · May 2026</span>
      </span>
      <h1 class="hero-title">
        <span data-lang="es">{title_es}</span>
        <span data-lang="en">{title_en}</span>
      </h1>
      <p class="hero-sub">
        <span data-lang="es">{sub_es}</span>
        <span data-lang="en">{sub_en}</span>
      </p>
    </div>
  </section>

  <section class="block" style="padding-top: 1rem;">
    <div class="wrap">
      <div class="plans">

        <div class="plan">
          <h3>Starter</h3>
          <p class="desc">
            <span data-lang="es">Para devs probando el protocolo.</span>
            <span data-lang="en">For devs trying out the protocol.</span>
          </p>
          <div class="price">€0
            <small>
              <span data-lang="es">+ €0.05/SMS · €0.04/min</span>
              <span data-lang="en">+ €0.05/SMS · €0.04/min</span>
            </small>
          </div>
          <ul class="features">
            <li><span data-lang="es">Hasta 5 MIDs</span><span data-lang="en">Up to 5 MIDs</span></li>
            <li><span data-lang="es">Mock telco únicamente</span><span data-lang="en">Mock telco only</span></li>
            <li><span data-lang="es">Soporte community (GitHub)</span><span data-lang="en">Community support (GitHub)</span></li>
            <li><span data-lang="es">Open API completo</span><span data-lang="en">Full open API</span></li>
            <li><span data-lang="es">MCP endpoint público</span><span data-lang="en">Public MCP endpoint</span></li>
            <li><span data-lang="es">Sin contrato</span><span data-lang="en">No contract</span></li>
          </ul>
          <a href="/install.sh" class="cta">
            <span data-lang="es">Empezar gratis</span>
            <span data-lang="en">Get started free</span>
          </a>
        </div>

        <div class="plan featured">
          <span class="badge">
            <span data-lang="es">Recomendado</span>
            <span data-lang="en">Recommended</span>
          </span>
          <h3>Growth</h3>
          <p class="desc">
            <span data-lang="es">Para producto en crecimiento, con telco real.</span>
            <span data-lang="en">For products scaling on a real telco.</span>
          </p>
          <div class="price">€9
            <small>
              <span data-lang="es">/MID/mes + €0.04/SMS · €0.03/min</span>
              <span data-lang="en">/MID/month + €0.04/SMS · €0.03/min</span>
            </small>
          </div>
          <ul class="features">
            <li><span data-lang="es">Hasta 500 MIDs</span><span data-lang="en">Up to 500 MIDs</span></li>
            <li><span data-lang="es">Live telco (partner real)</span><span data-lang="en">Live telco (real partner)</span></li>
            <li><span data-lang="es">Soporte email &lt; 24h</span><span data-lang="en">Email support &lt; 24h</span></li>
            <li><span data-lang="es">Open API + webhooks con SLA</span><span data-lang="en">Open API + webhooks SLA</span></li>
            <li><span data-lang="es">Panel admin + métricas</span><span data-lang="en">Admin panel + metrics</span></li>
            <li><span data-lang="es">Sin contrato anual</span><span data-lang="en">No annual contract</span></li>
          </ul>
          <a href="mailto:hola@protocolami.com?subject=Growth%20trial" class="cta">
            <span data-lang="es">Empezar prueba</span>
            <span data-lang="en">Start trial</span>
          </a>
        </div>

        <div class="plan">
          <h3>Enterprise</h3>
          <p class="desc">
            <span data-lang="es">Para volumen alto, integraciones a medida y SLA estricto.</span>
            <span data-lang="en">For high volume, custom integrations, strict SLAs.</span>
          </p>
          <div class="price custom">
            <span data-lang="es">Hablamos</span>
            <span data-lang="en">Talk to us</span>
          </div>
          <ul class="features">
            <li><span data-lang="es">MIDs ilimitados</span><span data-lang="en">Unlimited MIDs</span></li>
            <li><span data-lang="es">Live telco + SLA 99.95%</span><span data-lang="en">Live telco + 99.95% SLA</span></li>
            <li><span data-lang="es">Soporte 24/7 (teléfono + Slack)</span><span data-lang="en">24/7 support (phone + Slack)</span></li>
            <li><span data-lang="es">Open API + integraciones a medida</span><span data-lang="en">Open API + custom integrations</span></li>
            <li><span data-lang="es">Dedicated account engineer</span><span data-lang="en">Dedicated account engineer</span></li>
            <li><span data-lang="es">Tarifas por volumen</span><span data-lang="en">Volume pricing</span></li>
          </ul>
          <a href="mailto:hola@protocolami.com?subject=Enterprise" class="cta">
            <span data-lang="es">Contáctanos</span>
            <span data-lang="en">Contact us</span>
          </a>
        </div>

      </div>
    </div>
  </section>

  <section class="block">
    <div class="wrap">
      <h2 class="section-title">
        <span data-lang="es">Preguntas frecuentes</span>
        <span data-lang="en">Frequently asked questions</span>
      </h2>
      <p class="section-sub">
        <span data-lang="es">Lo que más nos preguntan los equipos que están evaluando AMI.</span>
        <span data-lang="en">What teams evaluating AMI ask us most often.</span>
      </p>

      <div class="faq-grid">

        <div class="faq-item">
          <h4>
            <span data-lang="es">¿Pago por número activo aunque no lo use?</span>
            <span data-lang="en">Do I pay for an active number even if idle?</span>
          </h4>
          <p>
            <span data-lang="es">No. En Starter es gratis. En Growth pagas €9/MID/mes solo mientras el número está
            asignado a un agente; cuando lo liberas, deja de contar al instante.</span>
            <span data-lang="en">No. Free on Starter. On Growth you pay €9/MID/month only while the number is
            assigned to an agent; the moment you release it, billing stops.</span>
          </p>
        </div>

        <div class="faq-item">
          <h4>
            <span data-lang="es">¿Tienen contrato mínimo?</span>
            <span data-lang="en">Is there a minimum commitment?</span>
          </h4>
          <p>
            <span data-lang="es">Ninguno. Pagas mes a mes. Si dejas de usar AMI, dejas de pagar. Enterprise puede
            firmar contratos anuales para bloquear tarifa, pero es opcional.</span>
            <span data-lang="en">None. Month-to-month. Stop using AMI, stop paying. Enterprise customers can sign
            annual contracts to lock pricing, but it's optional.</span>
          </p>
        </div>

        <div class="faq-item">
          <h4>
            <span data-lang="es">¿Funciona en mi país?</span>
            <span data-lang="en">Does it work in my country?</span>
          </h4>
          <p>
            <span data-lang="es">Live telco está disponible hoy en España y la UE vía nuestro partner. Para otras
            regiones, lanzamos por trimestres; mientras tanto puedes desarrollar contra mock telco.</span>
            <span data-lang="en">Live telco is available today in Spain and the EU via our partner. Other regions
            roll out quarterly; meanwhile you can build against mock telco.</span>
          </p>
        </div>

        <div class="faq-item">
          <h4>
            <span data-lang="es">¿Qué pasa con los números si dejo de pagar?</span>
            <span data-lang="en">What happens to my numbers if I stop paying?</span>
          </h4>
          <p>
            <span data-lang="es">Tienes 30 días de gracia para portarte o reasignar antes de que el número vuelva
            al pool. La identidad criptográfica (MID) sigue siendo tuya y exportable.</span>
            <span data-lang="en">You get a 30-day grace period to port out or reassign before the number returns to
            the pool. The cryptographic identity (MID) stays yours and is exportable.</span>
          </p>
        </div>

        <div class="faq-item">
          <h4>
            <span data-lang="es">¿Por qué es más barato que un wrapper de APIs?</span>
            <span data-lang="en">Why is this cheaper than an API wrapper?</span>
          </h4>
          <p>
            <span data-lang="es">Porque AMI conecta directamente al operador telco — no hay un intermediario que
            añada margen del 40-60% sobre cada SMS y cada minuto.</span>
            <span data-lang="en">Because AMI connects directly to the telco operator — there's no middleman adding
            a 40-60% markup on every SMS and every minute.</span>
          </p>
        </div>

        <div class="faq-item">
          <h4>
            <span data-lang="es">¿Cobran por llamadas entrantes o SMS recibidos?</span>
            <span data-lang="en">Do you charge for inbound calls or received SMS?</span>
          </h4>
          <p>
            <span data-lang="es">No. Solo cobramos por mensajes salientes y minutos hablados. Los entrantes están
            incluidos sin coste adicional en cualquier plan.</span>
            <span data-lang="en">No. We only charge for outbound messages and outbound voice minutes. Inbound is
            included at no extra cost on every plan.</span>
          </p>
        </div>

      </div>
    </div>
  </section>

  <section class="block">
    <div class="wrap">
      <div class="closing">
        <p>
          <span data-lang="es">Precios sin sorpresas. Sin minimum commitment. Si el ROI no encaja con tu caso de
          uso, no pagas. Hablémoslo.</span>
          <span data-lang="en">No-surprise pricing. No minimum commitment. If the ROI doesn't fit your use case,
          you don't pay. Let's talk.</span>
        </p>
        <p class="small">
          <a href="/calculator">
            <span data-lang="es">→ Calcula tu ahorro</span>
            <span data-lang="en">→ Calculate your savings</span>
          </a>
          &nbsp;·&nbsp;
          <a href="mailto:hola@protocolami.com">hola@protocolami.com</a>
        </p>
      </div>
    </div>
  </section>

  <footer></footer>

</body>
</html>
"""


def render_calculator_page(lang: str = "es") -> str:
    """Página /calculator — ROI calculator interactiva.

    Modelo de precio (hardcoded en JS para vanilla, sin deps):

      AMI (Growth):
        - €9/MID activo/mes
        - €0.04/SMS saliente
        - €0.03/min saliente

      Wrapper baseline (genérico, no hay nombres):
        - €0.08/SMS
        - €0.06/min
        - sin fee por número

    Región: España (×1.0), UE (×1.0), Global (×1.15) — multiplicador
    aplicado a SMS y voz porque el peering internacional es más caro.

    Defaults: 10 agentes, 20 SMS/día/agente, 5 min/día/agente, España.
    Eso da un caso suficientemente expresivo para que en la primera vista
    el ahorro sea visible (no cero, no microscópico).
    """
    calc_css = _BASE_CSS + """
      /* CALCULATOR LAYOUT */
      .calc {
        display: grid;
        grid-template-columns: 1fr 1.2fr;
        gap: 2rem;
        margin-top: 1rem;
      }
      @media (max-width: 880px) { .calc { grid-template-columns: 1fr; } }

      .calc-inputs, .calc-outputs {
        background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 1.8rem 1.8rem 2rem;
      }
      .calc-outputs {
        border-color: rgba(139,108,255,0.35);
        background: linear-gradient(180deg, rgba(139,108,255,0.06) 0%, var(--bg-soft) 100%);
        box-shadow: 0 30px 80px -30px rgba(139,108,255,0.3);
      }

      .calc-section-label {
        font-family: var(--mono); font-size: 0.7rem; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.14em;
        color: var(--ink-mute); margin: 0 0 1.4rem 0;
      }

      .input-row {
        margin-bottom: 1.4rem;
      }
      .input-row:last-child { margin-bottom: 0; }
      .input-row label {
        display: flex; justify-content: space-between; align-items: baseline;
        font-size: 0.88rem; color: var(--ink);
        margin-bottom: 0.55rem;
      }
      .input-row label .num-input {
        background: var(--surface-2);
        border: 1px solid var(--line);
        color: var(--ink);
        font-family: var(--mono); font-size: 0.85rem; font-weight: 600;
        padding: 0.25rem 0.5rem;
        border-radius: 5px;
        width: 80px; text-align: right;
        outline: none;
        transition: border-color 0.15s;
      }
      .input-row label .num-input:focus { border-color: var(--accent); }
      .input-row input[type="range"] {
        width: 100%;
        -webkit-appearance: none; appearance: none;
        background: transparent;
        margin: 0; padding: 0;
        height: 22px;
      }
      .input-row input[type="range"]::-webkit-slider-runnable-track {
        height: 4px; border-radius: 2px;
        background: linear-gradient(90deg, var(--accent) 0%, var(--accent) var(--p,50%), var(--line) var(--p,50%), var(--line) 100%);
      }
      .input-row input[type="range"]::-moz-range-track {
        height: 4px; border-radius: 2px; background: var(--line);
      }
      .input-row input[type="range"]::-moz-range-progress {
        height: 4px; border-radius: 2px; background: var(--accent);
      }
      .input-row input[type="range"]::-webkit-slider-thumb {
        -webkit-appearance: none; appearance: none;
        width: 18px; height: 18px; border-radius: 50%;
        background: var(--ink);
        border: 2px solid var(--accent);
        box-shadow: 0 0 12px rgba(139,108,255,0.5);
        margin-top: -7px;
        cursor: pointer;
      }
      .input-row input[type="range"]::-moz-range-thumb {
        width: 18px; height: 18px; border-radius: 50%;
        background: var(--ink); border: 2px solid var(--accent);
        cursor: pointer;
      }

      .select-region {
        width: 100%;
        background: var(--surface-2);
        border: 1px solid var(--line);
        color: var(--ink);
        font-family: var(--mono); font-size: 0.88rem;
        padding: 0.6rem 0.8rem;
        border-radius: 6px;
        outline: none;
        cursor: pointer;
      }
      .select-region:focus { border-color: var(--accent); }

      /* OUTPUTS */
      .out-row {
        display: flex; justify-content: space-between; align-items: baseline;
        padding: 0.85rem 0;
        border-bottom: 1px solid var(--line-soft);
      }
      .out-row:last-child { border-bottom: 0; }
      .out-row .out-label {
        font-size: 0.88rem; color: var(--ink-soft);
      }
      .out-row .out-value {
        font-family: var(--mono); font-size: 0.95rem; font-weight: 600;
        color: var(--ink);
      }

      .out-divider {
        border: 0; border-top: 1px dashed var(--line);
        margin: 1.2rem 0;
      }

      .cost-block {
        padding: 1rem 1.2rem;
        background: var(--bg-soft);
        border: 1px solid var(--line);
        border-radius: 10px;
        margin-bottom: 0.8rem;
        display: flex; align-items: center; justify-content: space-between;
      }
      .cost-block .cost-label {
        font-size: 0.85rem; color: var(--ink-soft);
        font-family: var(--mono);
      }
      .cost-block .cost-value {
        font-family: var(--mono); font-size: 1.5rem; font-weight: 700;
        color: var(--ink); letter-spacing: -0.02em;
      }
      .cost-block.ami {
        background: linear-gradient(90deg, rgba(139,108,255,0.10), rgba(93,209,255,0.05));
        border-color: rgba(139,108,255,0.35);
      }
      .cost-block.ami .cost-value { color: var(--accent-2); }
      .cost-block.wrapper {
        opacity: 0.85;
      }
      .cost-block.wrapper .cost-value {
        color: var(--ink-soft);
        text-decoration: line-through;
        text-decoration-color: rgba(248, 113, 113, 0.6);
        text-decoration-thickness: 2px;
      }

      .savings-hero {
        margin-top: 1.4rem;
        padding: 1.6rem 1.4rem;
        text-align: center;
        background: radial-gradient(circle at 50% 0%, rgba(74,222,128,0.14) 0%, transparent 70%),
                    var(--bg-soft);
        border: 1px solid rgba(74,222,128,0.3);
        border-radius: 12px;
      }
      .savings-hero .savings-amount {
        font-family: var(--mono);
        font-size: clamp(2rem, 4vw, 2.6rem);
        font-weight: 700;
        color: var(--green);
        letter-spacing: -0.02em;
        line-height: 1;
        margin: 0;
      }
      .savings-hero .savings-pct {
        display: inline-block;
        margin-top: 0.4rem;
        padding: 0.3rem 0.7rem;
        background: rgba(74,222,128,0.15);
        color: var(--green);
        border-radius: 999px;
        font-family: var(--mono); font-size: 0.78rem; font-weight: 600;
      }
      .savings-hero .savings-annual {
        margin-top: 0.6rem;
        font-size: 0.85rem; color: var(--ink-soft);
        font-family: var(--mono);
      }

      /* WHY block */
      .why-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1.2rem;
        margin-top: 2rem;
      }
      @media (max-width: 880px) { .why-grid { grid-template-columns: 1fr; } }
      .why-card {
        padding: 1.4rem 1.4rem 1.5rem;
        background: var(--bg-soft);
        border: 1px solid var(--line);
        border-radius: 12px;
      }
      .why-card .num {
        font-family: var(--mono); font-size: 0.78rem; font-weight: 700;
        color: var(--accent);
        margin-bottom: 0.6rem;
      }
      .why-card h4 {
        font-size: 1.02rem; font-weight: 600; color: var(--ink);
        margin: 0 0 0.5rem 0;
      }
      .why-card p {
        margin: 0; color: var(--ink-soft); font-size: 0.9rem;
        line-height: 1.6;
      }

      /* CTA strip */
      .calc-cta {
        margin: 3rem 0 5rem;
        padding: 2.2rem 2rem;
        text-align: center;
        background: radial-gradient(circle at 50% 0%, rgba(139,108,255,0.14) 0%, transparent 60%),
                    var(--bg-soft);
        border: 1px solid var(--line);
        border-radius: 14px;
      }
      .calc-cta h3 {
        font-size: 1.4rem; font-weight: 700; color: var(--ink);
        margin: 0 0 0.5rem 0;
      }
      .calc-cta p {
        color: var(--ink-soft); margin: 0 0 1.4rem 0;
      }
      .calc-cta .btn {
        display: inline-block;
        background: linear-gradient(90deg, #8b6cff, #5dd1ff);
        color: #06060a;
        font-family: var(--mono); font-weight: 700; font-size: 0.88rem;
        padding: 0.85rem 1.6rem; border-radius: 8px;
        text-decoration: none;
        transition: filter 0.15s;
      }
      .calc-cta .btn:hover { filter: brightness(1.1); color: #06060a; }
    """

    title_es = "¿Cuánto ahorras con AMI?"
    title_en = "How much do you save with AMI?"
    sub_es = ("Mueve los sliders. Los cálculos se actualizan en vivo comparando AMI "
              "contra el coste promedio de un wrapper de APIs tradicional.")
    sub_en = ("Slide the inputs. Numbers update live comparing AMI against the "
              "average cost of a traditional API wrapper.")

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · ROI Calculator</title>
<meta name="description" content="Calculate how much your AI agent fleet saves with AMI vs. traditional API wrappers.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{calc_css}</style>
<script>
  (function() {{
    try {{
      var l = localStorage.getItem('ami-lang');
      if (l === 'es' || l === 'en') document.documentElement.lang = l;
    }} catch(e) {{}}
  }})();
</script>
</head>
<body>

  <header></header>

  <section class="hero">
    <div class="wrap">
      <span class="eyebrow">
        <span class="live"></span>
        <span data-lang="es">ROI Calculator · en vivo</span>
        <span data-lang="en">ROI Calculator · live</span>
      </span>
      <h1 class="hero-title">
        <span data-lang="es">{title_es}</span>
        <span data-lang="en">{title_en}</span>
      </h1>
      <p class="hero-sub">
        <span data-lang="es">{sub_es}</span>
        <span data-lang="en">{sub_en}</span>
      </p>
    </div>
  </section>

  <section class="block" style="padding-top: 1rem;">
    <div class="wrap">

      <div class="calc">

        <!-- INPUTS ------------------------------------------------------- -->
        <div class="calc-inputs">
          <p class="calc-section-label">
            <span data-lang="es">Tu flota</span>
            <span data-lang="en">Your fleet</span>
          </p>

          <div class="input-row">
            <label>
              <span>
                <span data-lang="es">Agentes activos</span>
                <span data-lang="en">Active agents</span>
              </span>
              <input type="number" class="num-input" id="agents-num" min="1" max="1000" step="1" value="10">
            </label>
            <input type="range" id="agents" min="1" max="1000" step="1" value="10">
          </div>

          <div class="input-row">
            <label>
              <span>
                <span data-lang="es">SMS por agente / día</span>
                <span data-lang="en">SMS per agent / day</span>
              </span>
              <input type="number" class="num-input" id="sms-num" min="0" max="500" step="1" value="20">
            </label>
            <input type="range" id="sms" min="0" max="500" step="1" value="20">
          </div>

          <div class="input-row">
            <label>
              <span>
                <span data-lang="es">Minutos voz por agente / día</span>
                <span data-lang="en">Voice min per agent / day</span>
              </span>
              <input type="number" class="num-input" id="voice-num" min="0" max="120" step="1" value="5">
            </label>
            <input type="range" id="voice" min="0" max="120" step="1" value="5">
          </div>

          <div class="input-row">
            <label>
              <span>
                <span data-lang="es">Región</span>
                <span data-lang="en">Region</span>
              </span>
            </label>
            <select class="select-region" id="region">
              <option value="1.0">
                Spain · ES
              </option>
              <option value="1.0">EU</option>
              <option value="1.15">Global</option>
            </select>
          </div>
        </div>

        <!-- OUTPUTS ----------------------------------------------------- -->
        <div class="calc-outputs">
          <p class="calc-section-label">
            <span data-lang="es">Volumen mensual</span>
            <span data-lang="en">Monthly volume</span>
          </p>

          <div class="out-row">
            <span class="out-label">
              <span data-lang="es">SMS totales / mes</span>
              <span data-lang="en">Total SMS / month</span>
            </span>
            <span class="out-value" id="out-sms">—</span>
          </div>
          <div class="out-row">
            <span class="out-label">
              <span data-lang="es">Minutos voz totales / mes</span>
              <span data-lang="en">Total voice min / month</span>
            </span>
            <span class="out-value" id="out-voice">—</span>
          </div>
          <div class="out-row">
            <span class="out-label">
              <span data-lang="es">MIDs activos</span>
              <span data-lang="en">Active MIDs</span>
            </span>
            <span class="out-value" id="out-mids">—</span>
          </div>

          <hr class="out-divider">

          <div class="cost-block wrapper">
            <span class="cost-label">
              <span data-lang="es">Wrappers de APIs (estimado)</span>
              <span data-lang="en">API wrappers (estimated)</span>
            </span>
            <span class="cost-value" id="out-wrapper">—</span>
          </div>
          <div class="cost-block ami">
            <span class="cost-label">
              <span data-lang="es">AMI · Growth</span>
              <span data-lang="en">AMI · Growth</span>
            </span>
            <span class="cost-value" id="out-ami">—</span>
          </div>

          <div class="savings-hero">
            <p class="savings-amount" id="out-savings">—</p>
            <span class="savings-pct" id="out-savings-pct">—</span>
            <p class="savings-annual" id="out-annual">—</p>
          </div>
        </div>

      </div>
    </div>
  </section>

  <section class="block">
    <div class="wrap">
      <h2 class="section-title">
        <span data-lang="es">¿De dónde sale el ahorro?</span>
        <span data-lang="en">Where does the saving come from?</span>
      </h2>
      <p class="section-sub">
        <span data-lang="es">No es marketing — es arquitectura. Tres razones técnicas explican
        por qué AMI puede cobrar menos.</span>
        <span data-lang="en">It's not marketing — it's architecture. Three technical reasons
        explain why AMI can charge less.</span>
      </p>

      <div class="why-grid">

        <div class="why-card">
          <div class="num">01</div>
          <h4>
            <span data-lang="es">Cero margen de intermediario</span>
            <span data-lang="en">Zero middleman markup</span>
          </h4>
          <p>
            <span data-lang="es">Los wrappers tradicionales compran al operador y revenden con
            margen del 40-60% sobre cada SMS y cada minuto. AMI conecta directamente al
            operador telco vía partnership, eliminando esa capa de coste.</span>
            <span data-lang="en">Traditional wrappers buy from the operator and resell with a
            40-60% markup on each SMS and each minute. AMI connects directly to the telco
            via partnership, eliminating that cost layer.</span>
          </p>
        </div>

        <div class="why-card">
          <div class="num">02</div>
          <h4>
            <span data-lang="es">Peering directo, sin saltos</span>
            <span data-lang="en">Direct peering, no extra hops</span>
          </h4>
          <p>
            <span data-lang="es">SMS y voz salen del operador directo al destino — sin pasar
            por un PoP intermedio que cobra por byte y añade latencia. Menos infraestructura
            cobrando peaje, menos coste para ti.</span>
            <span data-lang="en">SMS and voice go from the operator straight to destination —
            no intermediate PoP charging per byte and adding latency. Less infrastructure
            taking a toll, lower cost for you.</span>
          </p>
        </div>

        <div class="why-card">
          <div class="num">03</div>
          <h4>
            <span data-lang="es">Modelo nativo para agentes</span>
            <span data-lang="en">Agent-native pricing model</span>
          </h4>
          <p>
            <span data-lang="es">Los wrappers cobran fees por número activo idle, por
            verificación, por feature gateado. AMI cobra solo por uso real — tu agente
            duerme, tu factura duerme también.</span>
            <span data-lang="en">Wrappers charge for idle numbers, verifications, gated
            features. AMI charges only for real usage — your agent sleeps, your invoice
            sleeps too.</span>
          </p>
        </div>

      </div>
    </div>
  </section>

  <section class="block">
    <div class="wrap">
      <div class="calc-cta">
        <h3>
          <span data-lang="es">¿Encaja con tu caso de uso?</span>
          <span data-lang="en">Does it fit your use case?</span>
        </h3>
        <p>
          <span data-lang="es">Si los números no encajan, no es para ti — y nos lo dices. Sin presión.</span>
          <span data-lang="en">If the numbers don't fit, it's not for you — just tell us. No pressure.</span>
        </p>
        <a href="mailto:hola@protocolami.com?subject=ROI" class="btn">
          <span data-lang="es">Hablémoslo →</span>
          <span data-lang="en">Let's talk →</span>
        </a>
      </div>
    </div>
  </section>

  <footer></footer>

<script>
(function() {{
  // Tarifas (EUR). Sliders devuelven strings — siempre parseFloat.
  var AMI = {{ sms: 0.04, voice: 0.03, mid: 9.0 }};
  var WRAP = {{ sms: 0.08, voice: 0.06, mid: 0.0 }};

  function fmt(n) {{
    var lang = document.documentElement.lang || 'es';
    var locale = lang === 'en' ? 'en-US' : 'es-ES';
    return new Intl.NumberFormat(locale, {{ maximumFractionDigits: 0 }}).format(Math.round(n));
  }}
  function eur(n) {{
    var lang = document.documentElement.lang || 'es';
    var locale = lang === 'en' ? 'en-US' : 'es-ES';
    return new Intl.NumberFormat(locale, {{
      style: 'currency', currency: 'EUR', maximumFractionDigits: 0
    }}).format(Math.round(n));
  }}

  function clamp(v, lo, hi) {{ return Math.max(lo, Math.min(hi, v)); }}

  function syncSliderTrack(slider) {{
    var min = +slider.min, max = +slider.max, val = +slider.value;
    var p = ((val - min) / (max - min)) * 100;
    slider.style.setProperty('--p', p + '%');
  }}

  function recompute() {{
    var agents = clamp(parseInt(document.getElementById('agents').value, 10) || 0, 1, 1000);
    var sms = clamp(parseInt(document.getElementById('sms').value, 10) || 0, 0, 500);
    var voice = clamp(parseInt(document.getElementById('voice').value, 10) || 0, 0, 120);
    var region = parseFloat(document.getElementById('region').value) || 1.0;

    // Días/mes aprox. para uso comercial = 30
    var DAYS = 30;
    var totalSms = agents * sms * DAYS;
    var totalVoice = agents * voice * DAYS;

    var amiCost = (totalSms * AMI.sms * region) + (totalVoice * AMI.voice * region) + (agents * AMI.mid);
    var wrapCost = (totalSms * WRAP.sms * region) + (totalVoice * WRAP.voice * region) + (agents * WRAP.mid);
    var savings = wrapCost - amiCost;
    var pct = wrapCost > 0 ? (savings / wrapCost) * 100 : 0;
    var annual = savings * 12;

    document.getElementById('out-sms').textContent = fmt(totalSms);
    document.getElementById('out-voice').textContent = fmt(totalVoice);
    document.getElementById('out-mids').textContent = fmt(agents);
    document.getElementById('out-wrapper').textContent = eur(wrapCost) + ' / mo';
    document.getElementById('out-ami').textContent = eur(amiCost) + ' / mo';

    var lang = document.documentElement.lang || 'es';
    if (savings > 0) {{
      document.getElementById('out-savings').textContent = (lang === 'en' ? '+' : '+') + eur(savings) + ' / mo';
      document.getElementById('out-savings-pct').textContent = '−' + Math.round(pct) + '% ' + (lang === 'en' ? 'vs. wrappers' : 'vs. wrappers');
      document.getElementById('out-annual').textContent = (lang === 'en' ? 'Annual savings: ' : 'Ahorro anual: ') + eur(annual);
    }} else {{
      document.getElementById('out-savings').textContent = eur(0);
      document.getElementById('out-savings-pct').textContent = '—';
      document.getElementById('out-annual').textContent = (lang === 'en' ? 'Adjust inputs to see savings.' : 'Ajusta los inputs para ver el ahorro.');
    }}
  }}

  // Vincular slider <-> number input bidireccional
  function bindPair(sliderId, numId) {{
    var s = document.getElementById(sliderId);
    var n = document.getElementById(numId);
    function fromSlider() {{ n.value = s.value; syncSliderTrack(s); recompute(); }}
    function fromNum() {{
      var v = clamp(parseInt(n.value, 10) || 0, +s.min, +s.max);
      n.value = v; s.value = v; syncSliderTrack(s); recompute();
    }}
    s.addEventListener('input', fromSlider);
    n.addEventListener('input', fromNum);
    n.addEventListener('change', fromNum);
    syncSliderTrack(s);
  }}

  bindPair('agents', 'agents-num');
  bindPair('sms', 'sms-num');
  bindPair('voice', 'voice-num');
  document.getElementById('region').addEventListener('change', recompute);

  recompute();
}})();
</script>

</body>
</html>
"""
