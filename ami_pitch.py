"""Página /pitch — deck navegable para presentar a socios.

Sustituye al powerpoint en la reunión. 14 slides estructurados:

  1. Title           — AMI · protocolo de identidad móvil para agentes
  2. The problem     — el agente AI puede hacer todo... menos tener teléfono
  3. Why now         — agentes explotan + teléfono sigue siendo backbone
  4. Solution        — AMI: número real en 3 segundos
  5. How it works    — los 3 planos (contract / operate / govern)
  6. Stack propio    — SMSC + PBX nuestros, no wrappers
  7. Market          — mercado direccionable + momentum
  8. Business model  — precios por número + uso, no SaaS plano
  9. Traction        — qué hay construido hoy
 10. Roadmap         — 6 meses · 12 meses · 24 meses
 11. Equipo          — placeholder a rellenar con info real
 12. Pedido          — qué necesitamos del partner
 13. Demo            — link grande a /live
 14. Q&A             — closing

Navegación: flechas ←/→ del teclado, Space, swipe en móvil.
Atajos: F para fullscreen, número 1-9 para saltar, Home/End.
Fullscreen sin chrome común (es modo presentación).
"""
from __future__ import annotations


_PITCH_CSS = """
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
    --accent-3: #ff5cb6;
    --green: #4ade80;
    --amber: #fbbf24;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; overflow: hidden; height: 100%; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background:
      radial-gradient(ellipse 90% 60% at 50% 0%, rgba(139,108,255,0.10), transparent 70%),
      radial-gradient(ellipse 70% 50% at 50% 100%, rgba(93,209,255,0.06), transparent 70%),
      var(--bg);
    background-attachment: fixed;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--accent); color: #fff; }

  /* SLIDE container ocupa toda la pantalla */
  .pitch-shell { position: relative; height: 100vh; overflow: hidden; }

  .pitch-slide {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    padding: 4vh 6vw;
    opacity: 0; visibility: hidden;
    transition: opacity 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .pitch-slide.active { opacity: 1; visibility: visible; }
  .pitch-slide .inner {
    width: 100%; max-width: 1100px;
    text-align: center;
  }

  .pitch-eyebrow {
    font-family: var(--mono); font-size: 0.78rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.22em;
    margin-bottom: 1.6rem;
  }
  .pitch-slide h1 {
    font-size: clamp(2.4rem, 6vw, 5rem);
    font-weight: 800; letter-spacing: -0.03em;
    margin: 0 0 1.2rem; line-height: 1.04;
  }
  .pitch-slide h1 .grad {
    background: linear-gradient(180deg, #c2b3ff 10%, #7a5cff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .pitch-slide h2 {
    font-size: clamp(1.6rem, 3.5vw, 2.6rem);
    font-weight: 700; letter-spacing: -0.022em;
    margin: 0 0 0.8rem;
  }
  .pitch-slide p.lead {
    font-size: clamp(1rem, 1.6vw, 1.35rem);
    color: var(--ink-soft); max-width: 800px; margin: 0 auto 1.6rem;
    line-height: 1.55;
  }
  .pitch-slide p.huge {
    font-size: clamp(2rem, 4vw, 3.2rem); font-weight: 700;
    line-height: 1.15; margin: 0 0 1rem;
  }
  .pitch-slide .num {
    font-family: var(--mono); font-size: clamp(3rem, 8vw, 6rem);
    font-weight: 800; letter-spacing: -0.04em;
    background: linear-gradient(180deg, #c2b3ff, #7a5cff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .pitch-slide .num-label {
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--ink-soft); text-transform: uppercase; letter-spacing: 0.14em;
    margin-top: -0.4rem;
  }

  /* GRID de 3 columnas */
  .pitch-grid-3 {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.4rem;
    margin: 2rem 0 0; text-align: left;
  }
  @media (max-width: 760px) {
    .pitch-grid-3 { grid-template-columns: 1fr; }
  }
  .pitch-card {
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 14px; padding: 1.5rem 1.6rem;
  }
  .pitch-card h3 {
    font-family: var(--mono); font-size: 0.72rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.18em;
    color: var(--accent); margin: 0 0 0.7rem;
  }
  .pitch-card .big {
    font-size: 1.25rem; font-weight: 600; margin: 0 0 0.5rem;
    color: var(--ink);
  }
  .pitch-card p {
    font-size: 0.92rem; color: var(--ink-soft); margin: 0;
    line-height: 1.5;
  }

  /* BULLET list grande */
  .pitch-bullets {
    text-align: left; max-width: 760px; margin: 1.5rem auto 0;
    list-style: none; padding: 0;
  }
  .pitch-bullets li {
    font-size: 1.15rem; margin-bottom: 1rem; padding-left: 2.2rem;
    position: relative; color: var(--ink-soft); line-height: 1.5;
  }
  .pitch-bullets li::before {
    content: "→"; position: absolute; left: 0; top: 0;
    color: var(--accent); font-family: var(--mono); font-weight: 600;
  }
  .pitch-bullets li strong { color: var(--ink); font-weight: 600; }

  /* COMPARISON tabla */
  .pitch-compare {
    margin: 2rem auto 0; max-width: 900px;
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 14px; overflow: hidden;
  }
  .pitch-compare table { width: 100%; border-collapse: collapse; }
  .pitch-compare th, .pitch-compare td {
    padding: 0.95rem 1.1rem; text-align: left;
    border-bottom: 1px solid var(--line);
    font-size: 1rem;
  }
  .pitch-compare th {
    font-family: var(--mono); font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.14em;
    color: var(--ink-mute); font-weight: 600;
    background: var(--surface);
  }
  .pitch-compare .ami { color: var(--green); }
  .pitch-compare .ami::before { content: "✓ "; }
  .pitch-compare .others { color: var(--ink-mute); }
  .pitch-compare .others::before { content: "× "; color: var(--red, #ff6b8a); }

  /* TIMELINE */
  .pitch-timeline {
    margin: 2rem auto 0; max-width: 900px;
    text-align: left;
  }
  .pitch-timeline-item {
    display: grid; grid-template-columns: 130px 1fr;
    gap: 1.5rem; padding: 1rem 0;
    border-bottom: 1px solid var(--line);
  }
  .pitch-timeline-when {
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--accent); font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .pitch-timeline-what { font-size: 1rem; line-height: 1.55; }
  .pitch-timeline-what strong { color: var(--ink); display: block; margin-bottom: 0.2rem; }
  .pitch-timeline-what span { color: var(--ink-soft); }

  /* CTA */
  .pitch-cta {
    display: inline-flex; align-items: center; gap: 0.6rem;
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; text-decoration: none;
    padding: 1rem 1.8rem; border-radius: 10px;
    font-family: var(--sans); font-size: 1.05rem; font-weight: 600;
    box-shadow: 0 12px 36px -12px rgba(123,92,255,0.55);
    margin-top: 1.5rem;
  }
  .pitch-cta:hover { transform: translateY(-1px); }

  /* CHROME inferior — controles */
  .pitch-chrome {
    position: fixed; bottom: 0; left: 0; right: 0;
    z-index: 100;
    padding: 1rem 1.5rem;
    display: flex; align-items: center; justify-content: space-between;
    background: linear-gradient(180deg, transparent, rgba(6,6,10,0.85));
    pointer-events: none;
  }
  .pitch-brand {
    font-family: var(--mono); font-weight: 700; font-size: 0.85rem;
    color: var(--ink-soft); pointer-events: auto;
    text-decoration: none;
  }
  .pitch-brand .dot { color: var(--accent); }
  .pitch-brand:hover { color: var(--ink); }
  .pitch-nav {
    display: flex; align-items: center; gap: 0.5rem;
    pointer-events: auto;
  }
  .pitch-nav button {
    background: var(--surface); border: 1px solid var(--line);
    color: var(--ink-soft); cursor: pointer;
    width: 36px; height: 36px; border-radius: 8px;
    font-family: var(--mono); font-size: 1.1rem;
  }
  .pitch-nav button:hover { color: var(--ink); border-color: var(--accent); }
  .pitch-nav button:disabled { opacity: 0.3; cursor: not-allowed; }
  .pitch-counter {
    font-family: var(--mono); font-size: 0.75rem; color: var(--ink-mute);
    padding: 0 0.6rem;
  }
  .pitch-counter strong { color: var(--ink); font-weight: 700; }

  /* PROGRESS bar arriba */
  .pitch-progress {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    height: 3px; background: rgba(255,255,255,0.04);
  }
  .pitch-progress i {
    display: block; height: 100%;
    background: linear-gradient(90deg, var(--accent-2), var(--accent));
    width: 0%; transition: width 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  }

  /* HINT teclado solo al primer load */
  .pitch-hint {
    position: fixed; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    background: var(--surface); border: 1px solid var(--line);
    padding: 1.3rem 1.8rem; border-radius: 12px;
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--ink-soft);
    z-index: 200;
    text-align: center;
    box-shadow: 0 30px 80px -30px rgba(0,0,0,0.7);
    animation: pitchHintIn 0.5s, pitchHintOut 0.5s 3.2s forwards;
  }
  @keyframes pitchHintIn {
    from { opacity: 0; transform: translate(-50%, -45%); }
    to { opacity: 1; transform: translate(-50%, -50%); }
  }
  @keyframes pitchHintOut {
    to { opacity: 0; transform: translate(-50%, -55%); pointer-events: none; }
  }
  .pitch-hint kbd {
    display: inline-block;
    background: var(--bg-soft); border: 1px solid var(--line);
    padding: 0.15rem 0.45rem; border-radius: 4px;
    margin: 0 0.2rem; color: var(--ink); font-family: var(--mono);
    font-size: 0.78rem;
  }

  /* bilingual */
  html[lang="es"] [data-lang="en"] { display: none; }
  html[lang="en"] [data-lang="es"] { display: none; }
"""


# Defino las slides como lista de dicts. Cada slide tiene id, eyebrow,
# body (HTML). Esto las hace fáciles de reordenar / añadir.
def _slides() -> list[dict]:
    return [
        {
            "id": "title",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">Parallax IEI · 2026</span>
                  <span data-lang="en">Parallax IEI · 2026</span>
                </div>
                <h1>
                  <span data-lang="es">Identidad móvil <span class="grad">para agentes AI.</span></span>
                  <span data-lang="en">Mobile identity <span class="grad">for AI agents.</span></span>
                </h1>
                <p class="lead">
                  <span data-lang="es">El protocolo abierto que da a un agente su propio número de teléfono — para enviar SMS, hacer llamadas, ser localizable. Real. En segundos.</span>
                  <span data-lang="en">The open protocol that gives an agent its own phone number — to send SMS, make calls, be reachable. Real. In seconds.</span>
                </p>
                <p style="font-family: var(--mono); font-size: 0.85rem; color: var(--ink-mute); margin-top: 3rem;">
                  AMI · Agent Mobile Identity Protocol · v1.0
                </p>
              </div>
            """,
        },
        {
            "id": "problem",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">el problema</span>
                  <span data-lang="en">the problem</span>
                </div>
                <h1>
                  <span data-lang="es">Tu agente AI puede hacer casi todo.</span>
                  <span data-lang="en">Your AI agent can do almost anything.</span>
                </h1>
                <p class="huge" style="color: var(--accent);">
                  <span data-lang="es">Menos tener teléfono.</span>
                  <span data-lang="en">Except have a phone.</span>
                </p>
                <p class="lead" style="margin-top: 1.5rem;">
                  <span data-lang="es">No puede enviar un SMS de recordatorio. No puede recibir un código 2FA. No puede llamar para confirmar una cita. La identidad móvil sigue siendo un derecho humano que los agentes no tienen.</span>
                  <span data-lang="en">It can't send a reminder SMS. It can't receive a 2FA code. It can't call to confirm an appointment. Mobile identity is still a human privilege that agents don't have.</span>
                </p>
              </div>
            """,
        },
        {
            "id": "why-now",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">por qué ahora</span>
                  <span data-lang="en">why now</span>
                </div>
                <h2>
                  <span data-lang="es">El momento perfecto se está abriendo.</span>
                  <span data-lang="en">The perfect moment is opening up.</span>
                </h2>
                <div class="pitch-grid-3">
                  <div class="pitch-card">
                    <h3><span data-lang="es">explosión de agentes</span><span data-lang="en">agents are exploding</span></h3>
                    <p class="big">100M+</p>
                    <p><span data-lang="es">agentes AI desplegados antes de 2027 según estimación conservadora del mercado.</span><span data-lang="en">AI agents deployed before 2027 (conservative market estimate).</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3><span data-lang="es">el teléfono no muere</span><span data-lang="en">phones don't die</span></h3>
                    <p class="big">~80%</p>
                    <p><span data-lang="es">de servicios reales aún pasan por SMS o voz: bancos, salud, logística, citas.</span><span data-lang="en">of real services still go through SMS or voice: banks, health, logistics, appointments.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3><span data-lang="es">nadie lo resuelve</span><span data-lang="en">nobody solves it</span></h3>
                    <p class="big">0</p>
                    <p><span data-lang="es">protocolos nativos para identidad móvil de agentes. Los wrappers de APIs siguen siendo SaaS para humanos.</span><span data-lang="en">native protocols for agent mobile identity. API wrappers are still SaaS for humans.</span></p>
                  </div>
                </div>
              </div>
            """,
        },
        {
            "id": "solution",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">la solución</span>
                  <span data-lang="en">the solution</span>
                </div>
                <h1>
                  <span data-lang="es">AMI = <span class="grad">protocolo + stack propio.</span></span>
                  <span data-lang="en">AMI = <span class="grad">protocol + own stack.</span></span>
                </h1>
                <p class="lead">
                  <span data-lang="es">Un agente AI llama tres tools MCP (o tres POST REST), pasa por contratación + firma + activación en menos de un segundo, y se queda con un número real y un agent_token scoped para operar SMS y voz.</span>
                  <span data-lang="en">An AI agent calls three MCP tools (or three REST POSTs), goes through contracting + signing + activation in under a second, and gets a real number plus a scoped agent_token to operate SMS and voice.</span>
                </p>
                <ul class="pitch-bullets">
                  <li><strong><span data-lang="es">Identidad real</span><span data-lang="en">Real identity</span></strong>: <span data-lang="es">número activo en operador real, no virtual.</span><span data-lang="en">active number on real operator, not virtual.</span></li>
                  <li><strong><span data-lang="es">Programable</span><span data-lang="en">Programmable</span></strong>: <span data-lang="es">REST + MCP + SDKs Python y TypeScript publicables.</span><span data-lang="en">REST + MCP + publishable Python and TypeScript SDKs.</span></li>
                  <li><strong><span data-lang="es">Auditable</span><span data-lang="en">Auditable</span></strong>: <span data-lang="es">cada acción persistida con HMAC + audit log + webhooks firmados.</span><span data-lang="en">every action persisted with HMAC + audit log + signed webhooks.</span></li>
                </ul>
              </div>
            """,
        },
        {
            "id": "how-it-works",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">cómo funciona</span>
                  <span data-lang="en">how it works</span>
                </div>
                <h2>
                  <span data-lang="es">Tres planos. Una API.</span>
                  <span data-lang="en">Three planes. One API.</span>
                </h2>
                <div class="pitch-grid-3">
                  <div class="pitch-card">
                    <h3><span data-lang="es">1. Contratar</span><span data-lang="en">1. Contract</span></h3>
                    <p class="big"><span data-lang="es">Solicitud → Oferta → Firma → MID activo</span><span data-lang="en">Request → Offer → Sign → MID active</span></p>
                    <p><span data-lang="es">El agente pasa una sola vez por el flujo legal. Devuelve número + agent_token.</span><span data-lang="en">Agent goes through legal flow once. Returns number + agent_token.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3><span data-lang="es">2. Operar</span><span data-lang="en">2. Operate</span></h3>
                    <p class="big"><span data-lang="es">SMS · voz · webhooks</span><span data-lang="en">SMS · voice · webhooks</span></p>
                    <p><span data-lang="es">Con el agent_token: enviar SMS, originar llamadas, recibir entrantes. AMI cursa la pipa SIP/SMPP.</span><span data-lang="en">With agent_token: send SMS, place calls, receive inbound. AMI carries the SIP/SMPP pipe.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3><span data-lang="es">3. Gobernar</span><span data-lang="en">3. Govern</span></h3>
                    <p class="big"><span data-lang="es">Límites · gasto · multi-tenant</span><span data-lang="en">Limits · spending · multi-tenant</span></p>
                    <p><span data-lang="es">Rate limits, budget mensual, allowlist de países, scoping entre customers, panel del cliente.</span><span data-lang="en">Rate limits, monthly budget, country allowlist, scoping between customers, customer panel.</span></p>
                  </div>
                </div>
                <p style="margin-top: 2rem;">
                  <a class="pitch-cta" href="/live" target="_blank">
                    <span data-lang="es">▶ Ver la demo en vivo</span>
                    <span data-lang="en">▶ See the live demo</span>
                  </a>
                </p>
              </div>
            """,
        },
        {
            "id": "stack-propio",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">diferencial</span>
                  <span data-lang="en">moat</span>
                </div>
                <h1>
                  <span data-lang="es">Todo <span class="grad">bajo nuestro stack.</span></span>
                  <span data-lang="en">Everything <span class="grad">under our own stack.</span></span>
                </h1>
                <p class="lead">
                  <span data-lang="es">SMSC propio (Kannel sobre SMPP), PBX propio (Asterisk sobre SIP), peering directo con el partner telco. No revendemos APIs ajenas.</span>
                  <span data-lang="en">Own SMSC (Kannel over SMPP), own PBX (Asterisk over SIP), direct peering with telco partner. We don't resell anyone's APIs.</span>
                </p>
                <div class="pitch-compare">
                  <table>
                    <thead>
                      <tr>
                        <th><span data-lang="es">Capa</span><span data-lang="en">Layer</span></th>
                        <th><span data-lang="es">Wrappers tradicionales</span><span data-lang="en">Traditional wrappers</span></th>
                        <th>AMI</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><span data-lang="es">Mensajería SMS</span><span data-lang="en">SMS messaging</span></td>
                        <td class="others">API resale</td>
                        <td class="ami"><span data-lang="es">SMSC propio · SMPP directo</span><span data-lang="en">Own SMSC · direct SMPP</span></td>
                      </tr>
                      <tr>
                        <td><span data-lang="es">Voz</span><span data-lang="en">Voice</span></td>
                        <td class="others">API resale</td>
                        <td class="ami"><span data-lang="es">PBX propio · SIP nativo</span><span data-lang="en">Own PBX · native SIP</span></td>
                      </tr>
                      <tr>
                        <td><span data-lang="es">Numeración</span><span data-lang="en">Numbering</span></td>
                        <td class="others"><span data-lang="es">numpool del provider</span><span data-lang="en">provider's numpool</span></td>
                        <td class="ami"><span data-lang="es">inventario propio asignado por partner telco</span><span data-lang="en">own inventory from telco partner</span></td>
                      </tr>
                      <tr>
                        <td><span data-lang="es">Margen</span><span data-lang="en">Margin</span></td>
                        <td class="others">~10-15%</td>
                        <td class="ami"><span data-lang="es">~50-70% (sin intermediario)</span><span data-lang="en">~50-70% (no middleman)</span></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            """,
        },
        {
            "id": "market",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">mercado</span>
                  <span data-lang="en">market</span>
                </div>
                <h2>
                  <span data-lang="es">Mercado direccionable en 24 meses.</span>
                  <span data-lang="en">Addressable market in 24 months.</span>
                </h2>
                <div class="pitch-grid-3">
                  <div class="pitch-card">
                    <h3>TAM</h3>
                    <p class="big">€12B+</p>
                    <p><span data-lang="es">mercado global de mensajería + voz B2B (excl. operadores tradicionales).</span><span data-lang="en">global B2B messaging + voice market (excl. traditional operators).</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>SAM</h3>
                    <p class="big">€1.8B</p>
                    <p><span data-lang="es">subsegmento "agents-first": empresas que despliegan agentes que necesitan voz o SMS.</span><span data-lang="en">"agents-first" subsegment: companies deploying agents that need voice or SMS.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>SOM 24m</h3>
                    <p class="big">€60M</p>
                    <p><span data-lang="es">objetivo realista 24 meses con foco UE + LATAM.</span><span data-lang="en">realistic 24-month target with EU + LATAM focus.</span></p>
                  </div>
                </div>
                <p class="lead" style="margin-top: 2rem;">
                  <span data-lang="es">El número de agentes desplegados crece 4-6× anual. Cada agente que toque cliente final necesita identidad móvil más temprano que tarde.</span>
                  <span data-lang="en">Deployed agents grow 4-6× annually. Every agent that touches an end user needs mobile identity sooner rather than later.</span>
                </p>
              </div>
            """,
        },
        {
            "id": "business-model",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">modelo de negocio</span>
                  <span data-lang="en">business model</span>
                </div>
                <h2>
                  <span data-lang="es">Por número activo + por uso.</span>
                  <span data-lang="en">Per active number + per usage.</span>
                </h2>
                <div class="pitch-grid-3">
                  <div class="pitch-card">
                    <h3>Starter</h3>
                    <p class="big">€0</p>
                    <p><span data-lang="es">+ €0.05/SMS + €0.04/min · hasta 5 MIDs · mock telco</span><span data-lang="en">+ €0.05/SMS + €0.04/min · up to 5 MIDs · mock telco</span></p>
                  </div>
                  <div class="pitch-card" style="border-color: var(--accent); position: relative;">
                    <h3>Growth <span style="color: var(--accent);">★</span></h3>
                    <p class="big">€9/MID/mo</p>
                    <p><span data-lang="es">+ €0.04/SMS + €0.03/min · hasta 500 MIDs · live telco</span><span data-lang="en">+ €0.04/SMS + €0.03/min · up to 500 MIDs · live telco</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>Enterprise</h3>
                    <p class="big"><span data-lang="es">Custom</span><span data-lang="en">Custom</span></p>
                    <p><span data-lang="es">SLA · soporte 24/7 · integraciones a medida · ilimitado</span><span data-lang="en">SLA · 24/7 support · custom integrations · unlimited</span></p>
                  </div>
                </div>
                <p class="lead" style="margin-top: 2rem;">
                  <span data-lang="es"><strong style="color: var(--ink);">ARPU estimado: €40-120/MID/mes</strong> según uso. 1.000 MIDs activos = ~€60K MRR.</span>
                  <span data-lang="en"><strong style="color: var(--ink);">Estimated ARPU: €40-120/MID/month</strong> by usage. 1,000 active MIDs = ~€60K MRR.</span>
                </p>
              </div>
            """,
        },
        {
            "id": "traction",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">qué hay construido hoy</span>
                  <span data-lang="en">what's built today</span>
                </div>
                <h2>
                  <span data-lang="es">Protocolo cerrado · stack operacional listo.</span>
                  <span data-lang="en">Protocol closed · operational stack ready.</span>
                </h2>
                <div class="pitch-grid-3">
                  <div class="pitch-card">
                    <h3><span data-lang="es">backend</span><span data-lang="en">backend</span></h3>
                    <p class="big">23 tools MCP · 47 endpoints REST</p>
                    <p><span data-lang="es">contratación + operación + governance + multi-tenant + auditoría completa. Python stdlib puro, sin dependencias innecesarias.</span><span data-lang="en">contracting + operation + governance + multi-tenant + full audit. Pure Python stdlib, no unnecessary dependencies.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>SDKs</h3>
                    <p class="big">Python · TypeScript</p>
                    <p><span data-lang="es">publicables a PyPI y npm. Tipados completos. 60+ tests cada uno.</span><span data-lang="en">publishable to PyPI and npm. Fully typed. 60+ tests each.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>infra</h3>
                    <p class="big">docker-compose ready</p>
                    <p><span data-lang="es">Kannel + Asterisk + simulator SMPP. Solo falta el SMPP + SIP trunk del partner.</span><span data-lang="en">Kannel + Asterisk + SMPP simulator. Only missing the partner SMPP + SIP trunk.</span></p>
                  </div>
                </div>
                <ul class="pitch-bullets" style="margin-top: 2rem;">
                  <li><strong>305 tests</strong> <span data-lang="es">verdes · seguridad auditada externamente · 0 marcas externas en superficies públicas (regla del socio)</span><span data-lang="en">passing · externally audited security · 0 third-party brands in public surfaces (partner rule)</span></li>
                  <li><strong>13 páginas públicas</strong> <span data-lang="es">desplegadas: landing, docs interactivo, spec, pricing, calculator, use cases, live demo, sandbox, status, etc.</span><span data-lang="en">deployed: landing, interactive docs, spec, pricing, calculator, use cases, live demo, sandbox, status, etc.</span></li>
                </ul>
              </div>
            """,
        },
        {
            "id": "roadmap",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">roadmap</span>
                  <span data-lang="en">roadmap</span>
                </div>
                <h2>
                  <span data-lang="es">Lo que viene.</span>
                  <span data-lang="en">What's coming.</span>
                </h2>
                <div class="pitch-timeline">
                  <div class="pitch-timeline-item">
                    <div class="pitch-timeline-when">Q3 2026</div>
                    <div class="pitch-timeline-what">
                      <strong><span data-lang="es">Live launch</span><span data-lang="en">Live launch</span></strong>
                      <span data-lang="es">Activación del trunk SMPP + SIP del partner telco. Primer cliente real en producción. Beta cerrada.</span>
                      <span data-lang="en">Partner telco SMPP + SIP trunk activation. First real customer in production. Closed beta.</span>
                    </div>
                  </div>
                  <div class="pitch-timeline-item">
                    <div class="pitch-timeline-when">Q4 2026</div>
                    <div class="pitch-timeline-what">
                      <strong><span data-lang="es">100 customers · GA</span><span data-lang="en">100 customers · GA</span></strong>
                      <span data-lang="es">Apertura pública. SDKs en PyPI y npm. WhatsApp Business como capacidad opcional.</span>
                      <span data-lang="en">Public opening. SDKs on PyPI and npm. WhatsApp Business as optional capability.</span>
                    </div>
                  </div>
                  <div class="pitch-timeline-item">
                    <div class="pitch-timeline-when">Q1-Q2 2027</div>
                    <div class="pitch-timeline-what">
                      <strong><span data-lang="es">Expansión LATAM</span><span data-lang="en">LATAM expansion</span></strong>
                      <span data-lang="es">Trunks adicionales: México, Brasil, Colombia. 1.000 MIDs activos. Equipo comercial.</span>
                      <span data-lang="en">Additional trunks: Mexico, Brazil, Colombia. 1,000 active MIDs. Sales team.</span>
                    </div>
                  </div>
                  <div class="pitch-timeline-item">
                    <div class="pitch-timeline-when">2027+</div>
                    <div class="pitch-timeline-what">
                      <strong><span data-lang="es">Protocolo de referencia</span><span data-lang="en">Reference protocol</span></strong>
                      <span data-lang="es">AMI como estándar de facto para identidad móvil de agentes. Open governance, contribuciones de la comunidad.</span>
                      <span data-lang="en">AMI as de-facto standard for agent mobile identity. Open governance, community contributions.</span>
                    </div>
                  </div>
                </div>
              </div>
            """,
        },
        {
            "id": "team",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">equipo</span>
                  <span data-lang="en">team</span>
                </div>
                <h2>
                  <span data-lang="es">Parallax IEI.</span>
                  <span data-lang="en">Parallax IEI.</span>
                </h2>
                <p class="lead">
                  <span data-lang="es">Equipo fundador con experiencia complementaria en producto AI, infraestructura telco y desarrollo de protocolo.</span>
                  <span data-lang="en">Founding team with complementary experience in AI product, telco infrastructure and protocol development.</span>
                </p>
                <div class="pitch-grid-3">
                  <div class="pitch-card">
                    <h3>Daniel</h3>
                    <p class="big"><span data-lang="es">Producto + Protocolo</span><span data-lang="en">Product + Protocol</span></p>
                    <p><span data-lang="es">Lead de Parallax IEI. Diseño de producto, arquitectura del protocolo, GTM.</span><span data-lang="en">Lead at Parallax IEI. Product design, protocol architecture, GTM.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>Jaime</h3>
                    <p class="big"><span data-lang="es">Telco + Operaciones</span><span data-lang="en">Telco + Operations</span></p>
                    <p><span data-lang="es">Partner. Acceso a infraestructura telco real, peering con operadores, operación carrier-grade.</span><span data-lang="en">Partner. Access to real telco infrastructure, carrier peering, carrier-grade operations.</span></p>
                  </div>
                  <div class="pitch-card">
                    <h3>[+]</h3>
                    <p class="big"><span data-lang="es">Próximos socios</span><span data-lang="en">Next partners</span></p>
                    <p><span data-lang="es">Equipo en formación. Buscando perfiles comerciales para LATAM y devrel para community.</span><span data-lang="en">Team in formation. Looking for sales profiles for LATAM and devrel for community.</span></p>
                  </div>
                </div>
              </div>
            """,
        },
        {
            "id": "ask",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">qué necesitamos</span>
                  <span data-lang="en">what we need</span>
                </div>
                <h1>
                  <span data-lang="es">El partnership <span class="grad">desbloquea todo.</span></span>
                  <span data-lang="en">The partnership <span class="grad">unlocks everything.</span></span>
                </h1>
                <ul class="pitch-bullets">
                  <li><strong><span data-lang="es">Licencia telco</span><span data-lang="en">Telco license</span></strong>: <span data-lang="es">acceso al trunk SMPP + SIP con tu operador. Es el único bloqueo para activar tráfico real.</span><span data-lang="en">access to SMPP + SIP trunk with your operator. The only blocker to activate real traffic.</span></li>
                  <li><strong><span data-lang="es">Inventario de números</span><span data-lang="en">Number inventory</span></strong>: <span data-lang="es">rango MSISDN asignado a la cuenta AMI para repartir a customers.</span><span data-lang="en">MSISDN range assigned to the AMI account to distribute to customers.</span></li>
                  <li><strong><span data-lang="es">Reparto del revenue</span><span data-lang="en">Revenue split</span></strong>: <span data-lang="es">acuerdo sobre el modelo (por tramos de uso, fee fijo por MID, mix). Negociable.</span><span data-lang="en">agreement on the model (usage tiers, fixed fee per MID, mix). Negotiable.</span></li>
                  <li><strong><span data-lang="es">Roadmap conjunto</span><span data-lang="en">Joint roadmap</span></strong>: <span data-lang="es">compromiso de soporte 24/7 + SLA para clientes Enterprise + expansion plan.</span><span data-lang="en">commitment to 24/7 support + SLA for Enterprise customers + expansion plan.</span></li>
                </ul>
              </div>
            """,
        },
        {
            "id": "demo",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">vámoslo</span>
                  <span data-lang="en">let's see it</span>
                </div>
                <h1>
                  <span data-lang="es">Demo <span class="grad">en vivo.</span></span>
                  <span data-lang="en">Live <span class="grad">demo.</span></span>
                </h1>
                <p class="lead">
                  <span data-lang="es">El ciclo completo en pantalla, narrado: provisión del número, SMS bidireccional, llamada bridgeada por SIP. Backend real funcionando.</span>
                  <span data-lang="en">Full cycle on screen, narrated: number provisioning, two-way SMS, SIP-bridged call. Real backend working.</span>
                </p>
                <a class="pitch-cta" href="/live" target="_blank" style="font-size: 1.3rem; padding: 1.3rem 2.4rem;">
                  <span data-lang="es">▶ Abrir /live demo</span>
                  <span data-lang="en">▶ Open /live demo</span>
                </a>
                <p style="font-family: var(--mono); font-size: 0.85rem; color: var(--ink-mute); margin-top: 2rem;">
                  <span data-lang="es">o navega a</span>
                  <span data-lang="en">or navigate to</span>
                  &nbsp; protocolami.com/live
                </p>
              </div>
            """,
        },
        {
            "id": "qa",
            "body": """
              <div class="inner">
                <div class="pitch-eyebrow">
                  <span data-lang="es">vuestro turno</span>
                  <span data-lang="en">your turn</span>
                </div>
                <h1>
                  <span data-lang="es">Q<span class="grad"> & </span>A.</span>
                  <span data-lang="en">Q<span class="grad"> & </span>A.</span>
                </h1>
                <p class="lead">
                  <span data-lang="es">Preguntad sin filtro. Si no sabemos la respuesta lo decimos.</span>
                  <span data-lang="en">Ask anything. If we don't know we'll say so.</span>
                </p>
                <div style="margin-top: 3rem; display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap;">
                  <a class="pitch-cta" href="/docs" target="_blank">
                    <span data-lang="es">/docs</span><span data-lang="en">/docs</span>
                  </a>
                  <a class="pitch-cta" href="/spec" target="_blank" style="background: var(--surface); color: var(--ink);">
                    /spec
                  </a>
                  <a class="pitch-cta" href="/waitlist" target="_blank" style="background: var(--surface); color: var(--ink);">
                    /waitlist
                  </a>
                </div>
              </div>
            """,
        },
    ]


def render_pitch_page(lang: str = "es") -> str:
    """Página /pitch — deck navegable. NO usa el chrome común porque es
    modo presentación full-screen."""
    slides_data = _slides()
    slides_html = "".join(
        f'<div class="pitch-slide" data-slide="{i}" data-id="{s["id"]}">{s["body"]}</div>'
        for i, s in enumerate(slides_data)
    )
    n = len(slides_data)

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Pitch deck</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_PITCH_CSS}</style>
</head>
<body>

  <div class="pitch-progress"><i id="pitchProgress"></i></div>

  <main class="pitch-shell" id="pitchShell">
    {slides_html}
  </main>

  <div class="pitch-hint" id="pitchHint">
    <span data-lang="es">Navega con</span><span data-lang="en">Navigate with</span>
    <kbd>←</kbd><kbd>→</kbd>
    · <span data-lang="es">salir</span><span data-lang="en">exit</span>
    <kbd>Esc</kbd>
    · <span data-lang="es">fullscreen</span><span data-lang="en">fullscreen</span>
    <kbd>F</kbd>
  </div>

  <div class="pitch-chrome">
    <a class="pitch-brand" href="/">AMI<span class="dot">.</span> ← <span data-lang="es">salir</span><span data-lang="en">exit</span></a>
    <div class="pitch-nav">
      <button id="pitchPrev" aria-label="Previous">←</button>
      <span class="pitch-counter"><strong id="pitchCurrent">1</strong> / {n}</span>
      <button id="pitchNext" aria-label="Next">→</button>
    </div>
  </div>

<script>
(function() {{
  var slides = document.querySelectorAll('.pitch-slide');
  var total = slides.length;
  var current = 0;
  var prevBtn = document.getElementById('pitchPrev');
  var nextBtn = document.getElementById('pitchNext');
  var currentEl = document.getElementById('pitchCurrent');
  var progressEl = document.getElementById('pitchProgress');

  function show(idx) {{
    idx = Math.max(0, Math.min(total - 1, idx));
    slides.forEach(function(s, i) {{
      s.classList.toggle('active', i === idx);
    }});
    current = idx;
    currentEl.textContent = idx + 1;
    progressEl.style.width = ((idx + 1) / total * 100) + '%';
    prevBtn.disabled = idx === 0;
    nextBtn.disabled = idx === total - 1;
    // Update URL hash sin scroll
    history.replaceState(null, '', '#' + slides[idx].getAttribute('data-id'));
  }}

  // Permitir entrar por hash directo: /pitch#market
  function loadFromHash() {{
    var h = window.location.hash.replace('#', '');
    if (!h) return show(0);
    for (var i = 0; i < total; i++) {{
      if (slides[i].getAttribute('data-id') === h) return show(i);
    }}
    show(0);
  }}

  // Teclado: flechas, Space, números, Home, End, F (fullscreen)
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {{
      e.preventDefault(); show(current + 1);
    }} else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {{
      e.preventDefault(); show(current - 1);
    }} else if (e.key === 'Home') {{
      e.preventDefault(); show(0);
    }} else if (e.key === 'End') {{
      e.preventDefault(); show(total - 1);
    }} else if (e.key === 'f' || e.key === 'F') {{
      e.preventDefault();
      if (!document.fullscreenElement) {{
        document.documentElement.requestFullscreen().catch(function() {{}});
      }} else {{
        document.exitFullscreen();
      }}
    }} else if (e.key >= '1' && e.key <= '9') {{
      var n = parseInt(e.key, 10) - 1;
      if (n < total) show(n);
    }}
  }});

  // Click buttons
  prevBtn.addEventListener('click', function() {{ show(current - 1); }});
  nextBtn.addEventListener('click', function() {{ show(current + 1); }});

  // Swipe móvil
  var touchStart = null;
  document.addEventListener('touchstart', function(e) {{
    touchStart = e.changedTouches[0].screenX;
  }});
  document.addEventListener('touchend', function(e) {{
    if (touchStart === null) return;
    var diff = e.changedTouches[0].screenX - touchStart;
    if (Math.abs(diff) > 50) {{
      if (diff < 0) show(current + 1);
      else show(current - 1);
    }}
    touchStart = null;
  }});

  // Hash navigation (← →  browser back/forward)
  window.addEventListener('hashchange', loadFromHash);

  // Init
  loadFromHash();
}})();
</script>

</body>
</html>"""
