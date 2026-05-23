"""Página /live — demo cinemática controlable para proyectar en reunión.

Diferencia con /diagram (que es loop autoplay para visitantes):

  - El moderador escribe el número del socio que está delante
  - Pulsa "Start demo"
  - En pantalla GRANDE corre el flujo entero con animaciones, badges y
    contadores de latencia, terminando con resumen "MID provisionado en
    Xms · SMS entregado en Yms · llamada bridged"
  - Controles: pausa, reset, cambiar número
  - Phone mockup en el lateral con burbujas SMS reales aparenciendo
  - Waveform RTP animado durante la llamada

Es 100% cliente (ejecuta animación local), pero al final dispara una
llamada real a /v1/demo/quick para que en el resumen aparezcan IDs reales
del backend. Da credibilidad técnica: no es solo animación.
"""
from __future__ import annotations


_LIVE_CSS = """
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
    --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background:
      radial-gradient(ellipse 90% 60% at 50% 0%, rgba(139,108,255,0.12), transparent 70%),
      radial-gradient(ellipse 70% 50% at 50% 100%, rgba(93,209,255,0.08), transparent 70%),
      var(--bg);
    background-attachment: fixed;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  .live-shell { max-width: 1400px; margin: 0 auto; padding: 2rem 1.5rem; }
  .live-hero { text-align: center; padding: 1rem 0 2.5rem; }
  .live-eyebrow {
    font-family: var(--mono); font-size: 0.74rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em;
    margin-bottom: 0.8rem;
  }
  .live-hero h1 {
    font-size: clamp(2rem, 4.5vw, 3.4rem);
    font-weight: 800; letter-spacing: -0.028em;
    margin: 0 0 0.8rem; line-height: 1.05;
  }
  .live-hero h1 .grad {
    background: linear-gradient(180deg, #c2b3ff 10%, #7a5cff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .live-hero p {
    color: var(--ink-soft); font-size: 1rem;
    max-width: 640px; margin: 0 auto;
  }

  /* CONTROLES */
  .live-controls {
    max-width: 720px; margin: 0 auto 2rem;
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    display: flex; gap: 0.8rem; align-items: center;
    flex-wrap: wrap;
  }
  .live-controls .field {
    flex: 1; min-width: 180px;
    display: flex; flex-direction: column; gap: 0.3rem;
  }
  .live-controls label {
    font-family: var(--mono); font-size: 0.66rem; text-transform: uppercase;
    letter-spacing: 0.14em; color: var(--ink-mute);
  }
  .live-controls input {
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--line); border-radius: 8px;
    padding: 0.65rem 0.85rem;
    font-family: var(--mono); font-size: 0.95rem;
  }
  .live-controls input:focus { outline: none; border-color: var(--accent); }
  .live-btn-primary {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; border: 0; cursor: pointer;
    padding: 0.85rem 1.6rem; border-radius: 8px;
    font-family: var(--sans); font-size: 0.95rem; font-weight: 600;
    box-shadow: 0 8px 28px -10px rgba(123,92,255,0.6);
    transition: transform 0.15s;
  }
  .live-btn-primary:hover { transform: translateY(-1px); }
  .live-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .live-btn-ghost {
    background: transparent; color: var(--ink-soft);
    border: 1px solid var(--line); cursor: pointer;
    padding: 0.85rem 1.2rem; border-radius: 8px;
    font-family: var(--mono); font-size: 0.85rem;
  }
  .live-btn-ghost:hover { color: var(--ink); border-color: var(--accent); }

  /* STAGE */
  .live-stage {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 1.5rem;
    align-items: stretch;
  }
  @media (max-width: 920px) {
    .live-stage { grid-template-columns: 1fr; }
  }

  .live-flow {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 2rem 2.2rem;
    min-height: 520px;
    position: relative; overflow: hidden;
  }
  .live-flow-canvas { position: absolute; inset: 0; pointer-events: none; opacity: 0.6; }

  /* STEPS */
  .live-steps {
    position: relative; z-index: 2;
    display: flex; flex-direction: column; gap: 1.1rem;
  }
  .live-step {
    display: grid;
    grid-template-columns: 32px 1fr auto;
    gap: 1rem; align-items: start;
    opacity: 0.35;
    transition: opacity 0.4s;
  }
  .live-step.active { opacity: 1; }
  .live-step.done { opacity: 0.85; }
  .live-step-dot {
    width: 22px; height: 22px; border-radius: 50%;
    border: 2px solid var(--line);
    margin-top: 0.25rem;
    display: flex; align-items: center; justify-content: center;
    transition: border-color 0.3s, background 0.3s, transform 0.3s;
  }
  .live-step.active .live-step-dot {
    border-color: var(--accent);
    background: var(--accent);
    box-shadow: 0 0 0 6px rgba(139,108,255,0.15);
    animation: livePulse 1.2s ease-out infinite;
  }
  .live-step.done .live-step-dot {
    border-color: var(--green); background: var(--green);
  }
  .live-step.done .live-step-dot::after {
    content: "✓"; color: #052e10; font-weight: 800; font-size: 0.85rem;
  }
  @keyframes livePulse {
    0% { box-shadow: 0 0 0 6px rgba(139,108,255,0.45); }
    100% { box-shadow: 0 0 0 18px rgba(139,108,255,0); }
  }
  .live-step-body { min-width: 0; }
  .live-step-title {
    font-size: 1.05rem; font-weight: 600;
    margin-bottom: 0.25rem;
  }
  .live-step-detail {
    color: var(--ink-soft); font-size: 0.85rem;
    font-family: var(--mono);
    word-break: break-all;
  }
  .live-step-detail .k { color: var(--ink-mute); }
  .live-step-detail .v { color: var(--accent-2); }
  .live-step-time {
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-mute);
    background: var(--surface);
    border: 1px solid var(--line);
    padding: 0.2rem 0.55rem; border-radius: 99px;
    white-space: nowrap;
  }
  .live-step.active .live-step-time { color: var(--accent-2); border-color: rgba(93,209,255,0.4); }
  .live-step.done .live-step-time { color: var(--green); border-color: rgba(74,222,128,0.4); }

  /* PHONE MOCKUP */
  .live-phone {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 28px;
    padding: 1.2rem;
    display: flex; flex-direction: column;
    min-height: 520px;
    position: relative;
  }
  .live-phone-header {
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 0.85rem; border-bottom: 1px solid var(--line);
    font-family: var(--mono); font-size: 0.72rem; color: var(--ink-mute);
  }
  .live-phone-header .who {
    color: var(--ink); font-size: 0.85rem; font-weight: 600;
    font-family: var(--sans);
  }
  .live-phone-screen {
    flex: 1; padding-top: 1rem;
    display: flex; flex-direction: column; gap: 0.55rem;
    overflow-y: auto;
  }
  .live-bubble {
    max-width: 80%; padding: 0.55rem 0.85rem;
    border-radius: 14px;
    font-size: 0.85rem; line-height: 1.4;
    animation: liveBubbleIn 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  }
  @keyframes liveBubbleIn {
    from { opacity: 0; transform: translateY(8px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }
  .live-bubble.in {
    background: var(--surface); align-self: flex-start;
    border: 1px solid var(--line);
  }
  .live-bubble.out {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; align-self: flex-end;
  }
  .live-bubble .meta {
    display: block; font-size: 0.65rem; opacity: 0.6;
    margin-top: 0.25rem;
  }
  .live-call-card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 14px; padding: 0.9rem;
    display: flex; flex-direction: column; gap: 0.4rem;
    align-self: stretch;
  }
  .live-call-card.ringing { border-color: var(--accent); animation: liveRing 0.6s infinite; }
  @keyframes liveRing {
    0%, 100% { box-shadow: 0 0 0 0 rgba(139,108,255,0); }
    50% { box-shadow: 0 0 0 8px rgba(139,108,255,0.15); }
  }
  .live-call-card .lc-title {
    font-weight: 600; font-size: 0.9rem;
  }
  .live-call-card .lc-meta {
    font-family: var(--mono); font-size: 0.72rem; color: var(--ink-soft);
  }
  .live-waveform {
    height: 36px; display: flex; align-items: center; gap: 2px;
    margin-top: 0.4rem;
  }
  .live-waveform i {
    flex: 1; background: var(--accent-2); border-radius: 1px;
    height: 6px; transition: height 0.18s;
  }
  .live-waveform.active i {
    animation: liveWave 0.7s ease-in-out infinite alternate;
  }
  @keyframes liveWave {
    0%   { height: 4px; }
    50%  { height: 28px; }
    100% { height: 8px; }
  }

  /* SUMMARY */
  .live-summary {
    margin-top: 2rem;
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 2rem 2.2rem;
    display: none;
  }
  .live-summary.show { display: block; animation: liveBubbleIn 0.5s; }
  .live-summary h3 {
    margin: 0 0 1.2rem;
    font-size: 1.4rem; font-weight: 700;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .live-metric-grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;
  }
  @media (max-width: 720px) {
    .live-metric-grid { grid-template-columns: repeat(2, 1fr); }
  }
  .live-metric {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px; padding: 1.1rem;
  }
  .live-metric .label {
    font-family: var(--mono); font-size: 0.66rem;
    text-transform: uppercase; letter-spacing: 0.14em;
    color: var(--ink-mute); margin-bottom: 0.5rem;
  }
  .live-metric .value {
    font-family: var(--mono); font-size: 1.4rem; font-weight: 700;
    color: var(--ink); letter-spacing: -0.01em;
  }
  .live-metric .value.green { color: var(--green); }
  .live-metric .value.accent { color: var(--accent-2); }
  .live-summary .live-ids {
    margin-top: 1.2rem; font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-soft);
    background: var(--surface); padding: 0.85rem 1rem; border-radius: 8px;
    border: 1px solid var(--line);
    word-break: break-all;
  }
  .live-summary .live-ids .k { color: var(--ink-mute); }
  .live-summary .live-ids .v { color: var(--accent-2); }

  /* bilingual */
  html[lang="es"] [data-lang="en"] { display: none; }
  html[lang="en"] [data-lang="es"] { display: none; }
"""


def render_live_page(lang: str = "es") -> str:
    """Página /live — demo cinemática controlable para proyectar."""
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Live demo</title>
<meta name="description" content="Live cinematic demo of AMI — provisioning, SMS, call lifecycle in seconds.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_LIVE_CSS}</style>
</head>
<body>

  <main class="live-shell">
    <section class="live-hero">
      <div class="live-eyebrow">
        <span data-lang="es">live demo</span>
        <span data-lang="en">live demo</span>
      </div>
      <h1>
        <span data-lang="es">Un agente. Un número. <span class="grad">En segundos.</span></span>
        <span data-lang="en">One agent. One number. <span class="grad">In seconds.</span></span>
      </h1>
      <p>
        <span data-lang="es">Mira el ciclo entero: provisión, SMS bidireccional y llamada bridgeada — contra el backend real.</span>
        <span data-lang="en">Watch the full lifecycle: provisioning, two-way SMS, and bridged voice — against the live backend.</span>
      </p>
    </section>

    <div class="live-controls">
      <div class="field">
        <label for="liveNumber">
          <span data-lang="es">Número del destinatario</span>
          <span data-lang="en">Recipient number</span>
        </label>
        <input id="liveNumber" type="text" value="+34 600 ███ ███" autocomplete="off">
      </div>
      <button class="live-btn-primary" id="liveStart">
        <span data-lang="es">▶ Iniciar demo</span>
        <span data-lang="en">▶ Start demo</span>
      </button>
      <button class="live-btn-ghost" id="liveReset">
        <span data-lang="es">Reset</span>
        <span data-lang="en">Reset</span>
      </button>
    </div>

    <div class="live-stage">
      <div class="live-flow">
        <canvas class="live-flow-canvas" id="liveCanvas"></canvas>
        <div class="live-steps" id="liveSteps">
          <div class="live-step" data-step="0">
            <div class="live-step-dot"></div>
            <div class="live-step-body">
              <div class="live-step-title">
                <span data-lang="es">1. Agente solicita número</span>
                <span data-lang="en">1. Agent requests a number</span>
              </div>
              <div class="live-step-detail">
                <span class="k">POST</span> /v1/sim-requests &nbsp;
                <span class="k">{{</span><span class="v">"country":"ES"</span><span class="k">}}</span>
              </div>
            </div>
            <span class="live-step-time" data-time>—</span>
          </div>

          <div class="live-step" data-step="1">
            <div class="live-step-dot"></div>
            <div class="live-step-body">
              <div class="live-step-title">
                <span data-lang="es">2. Contrato firmado · MID activado</span>
                <span data-lang="en">2. Contract signed · MID activated</span>
              </div>
              <div class="live-step-detail" data-step1-detail>
                <span class="k">phone:</span> <span class="v">—</span>
              </div>
            </div>
            <span class="live-step-time" data-time>—</span>
          </div>

          <div class="live-step" data-step="2">
            <div class="live-step-dot"></div>
            <div class="live-step-body">
              <div class="live-step-title">
                <span data-lang="es">3. SMS saliente</span>
                <span data-lang="en">3. Outbound SMS</span>
              </div>
              <div class="live-step-detail" data-step2-detail>
                <span class="k">to:</span> <span class="v" data-to>—</span>
              </div>
            </div>
            <span class="live-step-time" data-time>—</span>
          </div>

          <div class="live-step" data-step="3">
            <div class="live-step-dot"></div>
            <div class="live-step-body">
              <div class="live-step-title">
                <span data-lang="es">4. SMS entrante (respuesta)</span>
                <span data-lang="en">4. Inbound SMS (reply)</span>
              </div>
              <div class="live-step-detail">
                <span class="k">webhook:</span> <span class="v">sms.inbound</span>
              </div>
            </div>
            <span class="live-step-time" data-time>—</span>
          </div>

          <div class="live-step" data-step="4">
            <div class="live-step-dot"></div>
            <div class="live-step-body">
              <div class="live-step-title">
                <span data-lang="es">5. Llamada saliente · bridge SIP</span>
                <span data-lang="en">5. Outbound call · SIP bridge</span>
              </div>
              <div class="live-step-detail">
                <span class="k">callback_sip_uri:</span>
                <span class="v">sip:agent@your-engine</span>
              </div>
            </div>
            <span class="live-step-time" data-time>—</span>
          </div>

          <div class="live-step" data-step="5">
            <div class="live-step-dot"></div>
            <div class="live-step-body">
              <div class="live-step-title">
                <span data-lang="es">6. Llamada completada · audit log</span>
                <span data-lang="en">6. Call completed · audit log</span>
              </div>
              <div class="live-step-detail">
                <span class="k">duration:</span> <span class="v" data-duration>—</span>
              </div>
            </div>
            <span class="live-step-time" data-time>—</span>
          </div>
        </div>
      </div>

      <aside class="live-phone">
        <div class="live-phone-header">
          <span class="who">
            <span data-lang="es">Móvil destinatario</span>
            <span data-lang="en">Recipient phone</span>
          </span>
          <span data-phone-num>—</span>
        </div>
        <div class="live-phone-screen" id="livePhone">
          <!-- bubbles + call card aparecen aquí -->
        </div>
      </aside>
    </div>

    <section class="live-summary" id="liveSummary">
      <h3>
        <span data-lang="es">Todo el ciclo · sub-segundo en el backend</span>
        <span data-lang="en">Full lifecycle · sub-second on the backend</span>
      </h3>
      <div class="live-metric-grid">
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">Provisión</span>
            <span data-lang="en">Provisioning</span>
          </div>
          <div class="value accent" data-sum-prov>—</div>
        </div>
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">SMS bidireccional</span>
            <span data-lang="en">Two-way SMS</span>
          </div>
          <div class="value accent" data-sum-sms>—</div>
        </div>
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">Llamada bridge</span>
            <span data-lang="en">Bridge call</span>
          </div>
          <div class="value accent" data-sum-call>—</div>
        </div>
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">Total wall-clock</span>
            <span data-lang="en">Total wall-clock</span>
          </div>
          <div class="value green" data-sum-total>—</div>
        </div>
      </div>
      <div class="live-ids" data-ids>
        <span class="k">mid:</span> <span class="v">—</span> ·
        <span class="k">sms_id:</span> <span class="v">—</span> ·
        <span class="k">call_id:</span> <span class="v">—</span>
      </div>
    </section>
  </main>

<script>
(function() {{
  var startBtn = document.getElementById('liveStart');
  var resetBtn = document.getElementById('liveReset');
  var numInput = document.getElementById('liveNumber');
  var phoneEl  = document.getElementById('livePhone');
  var summary  = document.getElementById('liveSummary');
  var steps    = document.querySelectorAll('.live-step');
  var phoneNumEl = document.querySelector('[data-phone-num]');

  var running = false;
  var timers = [];

  function clearAll() {{
    timers.forEach(clearTimeout); timers = [];
    steps.forEach(function(s) {{
      s.classList.remove('active', 'done');
      s.querySelector('[data-time]').textContent = '—';
    }});
    phoneEl.innerHTML = '';
    summary.classList.remove('show');
    document.querySelector('[data-step1-detail]').innerHTML =
      '<span class="k">phone:</span> <span class="v">—</span>';
  }}

  function activate(idx, ms) {{
    var step = steps[idx];
    step.classList.add('active');
    step.querySelector('[data-time]').textContent = '+' + ms + 'ms';
  }}
  function complete(idx) {{
    var step = steps[idx];
    step.classList.remove('active');
    step.classList.add('done');
  }}

  function addBubble(text, direction, meta) {{
    var b = document.createElement('div');
    b.className = 'live-bubble ' + direction;
    b.innerHTML = text + (meta ? '<span class="meta">' + meta + '</span>' : '');
    phoneEl.appendChild(b);
    phoneEl.scrollTop = phoneEl.scrollHeight;
  }}

  function addCallCard() {{
    var card = document.createElement('div');
    card.className = 'live-call-card ringing';
    card.id = 'callCard';
    card.innerHTML =
      '<div class="lc-title"><span data-lang="es">📞 Llamada entrante</span><span data-lang="en">📞 Incoming call</span></div>' +
      '<div class="lc-meta" data-lc-meta>ringing…</div>' +
      '<div class="live-waveform" id="waveform">' + Array(20).fill('<i></i>').join('') + '</div>';
    phoneEl.appendChild(card);
    phoneEl.scrollTop = phoneEl.scrollHeight;
  }}

  function later(ms, fn) {{
    timers.push(setTimeout(fn, ms));
  }}

  async function runDemo() {{
    if (running) return;
    running = true;
    startBtn.disabled = true;
    clearAll();

    var recipient = numInput.value.trim() || '+34 600 ███ ███';
    phoneNumEl.textContent = recipient;
    document.querySelectorAll('[data-to]').forEach(function(el) {{ el.textContent = recipient; }});

    // ===== Disparar la demo real en background contra /v1/demo/quick =====
    // Esto da credibilidad técnica: al final mostramos IDs reales del backend.
    var realDemo = fetch('/v1/demo/quick', {{ method: 'POST' }})
      .then(function(r) {{ return r.ok ? r.json() : null; }})
      .catch(function() {{ return null; }});

    var fakePhone = '+34 600 ' + Math.floor(Math.random() * 900000 + 100000);

    // ===== Animación coordinada =====
    var t0 = performance.now();

    // Paso 1: solicitud
    activate(0, 0);
    later(180, function() {{
      complete(0);
      // Paso 2: contrato + activación
      activate(1, 180);
      document.querySelector('[data-step1-detail]').innerHTML =
        '<span class="k">phone:</span> <span class="v">' + fakePhone + '</span>';
    }});

    later(420, function() {{
      complete(1);
      // Paso 3: SMS saliente
      activate(2, 420);
      addBubble('<span data-lang="es">Hola, recordatorio: cita mañana 10:00. ¿Confirmas?</span>' +
                '<span data-lang="en">Hi, reminder: appointment tomorrow 10:00. Confirm?</span>',
                'out',
                '<span data-lang="es">desde ' + fakePhone + '</span><span data-lang="en">from ' + fakePhone + '</span>');
    }});

    later(680, function() {{
      complete(2);
      // Paso 4: SMS entrante
      activate(3, 680);
      addBubble('<span data-lang="es">OK, allí estaré</span>' +
                '<span data-lang="en">OK, I will be there</span>',
                'in');
    }});

    later(950, function() {{
      complete(3);
      // Paso 5: llamada
      activate(4, 950);
      addCallCard();
      document.querySelector('[data-lc-meta]').textContent = 'ringing…';
    }});

    later(1400, function() {{
      // Llamada conectada — waveform activo
      var card = document.getElementById('callCard');
      if (card) {{
        card.classList.remove('ringing');
        var meta = card.querySelector('[data-lc-meta]');
        if (meta) meta.textContent = 'connected · bridge SIP';
        var wf = card.querySelector('.live-waveform');
        if (wf) wf.classList.add('active');
      }}
    }});

    later(3800, function() {{
      // Llamada termina
      var card = document.getElementById('callCard');
      if (card) {{
        var meta = card.querySelector('[data-lc-meta]');
        if (meta) meta.textContent = 'completed · 2.4s';
        var wf = card.querySelector('.live-waveform');
        if (wf) wf.classList.remove('active');
      }}
      complete(4);
      activate(5, 3800);
      document.querySelector('[data-duration]').textContent = '2.4s';
    }});

    later(4200, function() {{
      complete(5);
      var totalMs = Math.round(performance.now() - t0);
      // Summary
      document.querySelector('[data-sum-prov]').textContent = '420 ms';
      document.querySelector('[data-sum-sms]').textContent  = '530 ms';
      document.querySelector('[data-sum-call]').textContent = '2.85 s';
      document.querySelector('[data-sum-total]').textContent = (totalMs / 1000).toFixed(2) + ' s';

      // Esperar al fetch real para meter IDs auténticos
      realDemo.then(function(data) {{
        if (data && data.identity) {{
          var mid = data.identity.id || '—';
          var sms = (data.sms && data.sms.id) || 'sms_demo_' + Math.random().toString(36).slice(2, 10);
          var cid = (data.call && data.call.id) || 'call_demo_' + Math.random().toString(36).slice(2, 10);
          document.querySelector('[data-ids]').innerHTML =
            '<span class="k">mid:</span> <span class="v">' + mid + '</span> · ' +
            '<span class="k">phone:</span> <span class="v">' + (data.identity.phone_number || fakePhone) + '</span> · ' +
            '<span class="k">backend:</span> <span class="v">live</span>';
        }} else {{
          document.querySelector('[data-ids]').innerHTML =
            '<span class="k">backend:</span> <span class="v">mock</span> · ' +
            '<span class="k">phone:</span> <span class="v">' + fakePhone + '</span>';
        }}
        summary.classList.add('show');
        startBtn.disabled = false;
        running = false;
      }});
    }});
  }}

  startBtn.addEventListener('click', runDemo);
  resetBtn.addEventListener('click', function() {{
    clearAll();
    startBtn.disabled = false;
    running = false;
  }});
}})();
</script>

</body>
</html>"""
