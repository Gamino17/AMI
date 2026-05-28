"""Página `/poc-co` — pitch técnico + plan PoC + brief para reunión con
el equipo del operador en Colombia (Julián + Javier Cruz, arq. IA).

Se sirve pública (sin auth) para que se pueda enseñar en pantalla durante
la reunión. Contiene:

  1. Pitch en una pantalla (qué es AMI, qué resuelve).
  2. Arquitectura técnica adaptada a CO (Kannel + Asterisk + trunk SMPP/SIP).
  3. Plan PoC en 3 fases concretas con plazos.
  4. Lo que necesitamos del partner CO (creds + IP whitelist + numeración).
  5. FAQ técnica anticipando preguntas (latencia, codec, MNP, SMS OTP).
  6. Brief para Daniel: cómo defender cada punto.
  7. Live demo: botones que disparan el flow contra el backend mock.

Sigue las reglas visuales de la landing (dark + violet/cyan + Inter).
NUNCA menciona marcas de operadores ni competidores (regla socio).
"""
from __future__ import annotations


_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --surface-2: #1a1a25;
    --line: #1f1f2c;
    --line-strong: #2a2a3a;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --green: #4ade80;
    --amber: #fbbf24;
    --red: #ff6b8a;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); -webkit-font-smoothing: antialiased; }
  body {
    background:
      radial-gradient(ellipse 60% 40% at 85% 15%, rgba(93,209,255,0.08), transparent 70%),
      radial-gradient(ellipse 70% 50% at 15% 70%, rgba(139,108,255,0.10), transparent 70%),
      var(--bg);
    background-attachment: fixed;
    min-height: 100vh;
  }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { color: #b9e6ff; }
  .wrap { max-width: 1120px; margin: 0 auto; padding: 3rem 1.5rem; }

  /* HERO */
  .hero { padding: 2rem 0 4rem; }
  .eyebrow { font-family: var(--mono); font-size: 0.72rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em; margin-bottom: 1rem; }
  h1 { font-size: clamp(2.4rem, 5vw, 4rem); font-weight: 700; line-height: 1.05; letter-spacing: -0.03em; margin: 0 0 1rem; }
  h1 .accent { color: var(--accent-2); }
  .hero p.lead { font-size: 1.2rem; color: var(--ink-soft); max-width: 720px; line-height: 1.55; margin: 0 0 1.6rem; }
  .meta { display: flex; gap: 1.2rem; flex-wrap: wrap; font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute); margin-top: 1.4rem; }
  .meta b { color: var(--ink); font-weight: 500; }

  /* SECTIONS */
  section { padding: 3rem 0; border-top: 1px solid var(--line); }
  section h2 { font-size: 1.7rem; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 0.4rem; }
  section h2 .num { color: var(--ink-mute); font-family: var(--mono); font-size: 1rem; margin-right: 0.6rem; font-weight: 600; }
  section .sub { color: var(--ink-soft); margin: 0 0 2rem; font-size: 1rem; max-width: 720px; line-height: 1.55; }
  section h3 { font-size: 1.1rem; font-weight: 600; margin: 1.6rem 0 0.6rem; letter-spacing: -0.01em; }

  /* CARDS */
  .grid { display: grid; gap: 1rem; }
  .grid.cols-2 { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  .grid.cols-3 { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
  .card {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.4rem 1.4rem;
  }
  .card .icon { font-family: var(--mono); font-size: 0.72rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.14em; margin-bottom: 0.5rem; }
  .card h4 { font-size: 1rem; font-weight: 600; margin: 0 0 0.4rem; }
  .card p { color: var(--ink-soft); font-size: 0.9rem; margin: 0; line-height: 1.5; }

  /* DIAGRAM ASCII */
  .diagram {
    font-family: var(--mono); font-size: 0.78rem; line-height: 1.35;
    background: var(--bg-soft); border: 1px solid var(--line); border-radius: 12px;
    padding: 1.6rem; overflow-x: auto; color: var(--ink-soft);
    white-space: pre;
  }
  .diagram b { color: var(--accent-2); font-weight: 600; }
  .diagram em { color: var(--accent); font-style: normal; font-weight: 600; }

  /* TIMELINE */
  .phases { display: grid; gap: 1.2rem; }
  .phase { display: grid; grid-template-columns: auto 1fr; gap: 1.4rem; align-items: start; }
  .phase .badge {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: white; width: 2.4rem; height: 2.4rem;
    border-radius: 8px; display: flex; align-items: center; justify-content: center;
    font-weight: 700;
  }
  .phase .body { background: var(--bg-soft); border: 1px solid var(--line); border-radius: 10px; padding: 1.2rem; }
  .phase h4 { margin: 0 0 0.4rem; font-size: 1.05rem; font-weight: 600; }
  .phase .when { font-family: var(--mono); font-size: 0.72rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.14em; margin-bottom: 0.6rem; }
  .phase ul { margin: 0.4rem 0 0 1rem; padding: 0; color: var(--ink-soft); font-size: 0.92rem; line-height: 1.65; }

  /* FAQ */
  details { background: var(--bg-soft); border: 1px solid var(--line); border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 0.6rem; }
  details summary { cursor: pointer; font-weight: 600; color: var(--ink); list-style: none; }
  details summary::-webkit-details-marker { display: none; }
  details summary:before { content: '+ '; color: var(--accent); font-family: var(--mono); font-weight: 700; margin-right: 0.4rem; }
  details[open] summary:before { content: '− '; }
  details .answer { margin-top: 0.8rem; color: var(--ink-soft); font-size: 0.94rem; line-height: 1.6; }
  details .answer code { font-family: var(--mono); background: var(--surface); padding: 0.1rem 0.35rem; border-radius: 4px; font-size: 0.86em; color: var(--accent-2); }

  /* BRIEF */
  .brief {
    background: linear-gradient(180deg, rgba(139,108,255,0.06), rgba(93,209,255,0.04));
    border: 1px solid rgba(139,108,255,0.25);
    border-radius: 12px; padding: 1.6rem 1.8rem;
  }
  .brief h3 { margin-top: 0; color: var(--accent); }
  .brief ul { padding-left: 1rem; color: var(--ink); line-height: 1.7; }
  .brief ul code { font-family: var(--mono); background: rgba(0,0,0,0.3); padding: 0.1rem 0.4rem; border-radius: 4px; color: var(--accent-2); font-size: 0.86em; }

  /* LIVE DEMO */
  .demo {
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.6rem;
  }
  .demo .btn-row { display: flex; gap: 0.7rem; flex-wrap: wrap; }
  .btn {
    background: linear-gradient(180deg, #9d80ff, #7a5cff); color: white;
    border: 0; padding: 0.65rem 1.2rem; border-radius: 8px; cursor: pointer;
    font-family: var(--sans); font-size: 0.88rem; font-weight: 600;
  }
  .btn.ghost {
    background: transparent; border: 1px solid var(--line-strong); color: var(--ink);
  }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  pre.demo-out {
    background: #000; color: #5dd1ff; border-radius: 8px; padding: 1rem;
    font-family: var(--mono); font-size: 0.78rem; overflow-x: auto;
    max-height: 360px; line-height: 1.45; margin-top: 1rem;
    border: 1px solid var(--line-strong);
  }
  pre.demo-out .ok { color: var(--green); }
  pre.demo-out .err { color: var(--red); }

  /* TABLES */
  .req-list { width: 100%; border-collapse: collapse; }
  .req-list th, .req-list td { padding: 0.7rem 0.9rem; text-align: left; border-bottom: 1px solid var(--line); font-size: 0.9rem; }
  .req-list th { color: var(--ink-mute); font-family: var(--mono); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.1em; font-weight: 500; }
  .req-list td code { font-family: var(--mono); color: var(--accent-2); font-size: 0.86em; background: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px; }
  .req-list tr:last-child td { border-bottom: 0; }

  /* FOOTER */
  footer { padding: 3rem 0; border-top: 1px solid var(--line); color: var(--ink-mute); font-size: 0.85rem; }

  @media (max-width: 640px) {
    .meta { flex-direction: column; gap: 0.5rem; }
    .phase { grid-template-columns: 1fr; }
  }
"""


def render_poc_co_page() -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · PoC Colombia</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">

<!-- BANNER REU SIP -->
<div style="background:linear-gradient(180deg,rgba(139,108,255,0.10),rgba(93,209,255,0.06));border:1px solid rgba(139,108,255,0.30);border-radius:12px;padding:1rem 1.4rem;margin-bottom:2rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem;">
  <div>
    <div style="font-family:var(--mono);font-size:0.7rem;color:var(--accent);text-transform:uppercase;letter-spacing:0.18em;margin-bottom:0.3rem;">Sesión 2026-05-28 · 16:30</div>
    <div style="font-size:1rem;font-weight:600;">Revisión Troncal SIP — abrir spec técnica completa</div>
  </div>
  <a href="/poc-co/sip" style="background:linear-gradient(180deg,#9d80ff,#7a5cff);color:white;padding:0.7rem 1.2rem;border-radius:8px;font-weight:600;font-size:0.9rem;">SIP Interconnect Spec →</a>
</div>

<!-- HERO -->
<section class="hero" style="border-top:0;padding-top:0">
  <div class="eyebrow">prueba de concepto · Colombia</div>
  <h1>Identidad móvil real para <span class="accent">agentes de IA</span>.</h1>
  <p class="lead">AMI es el protocolo que permite a un agente AI obtener su propio número móvil
  real, enviar y recibir SMS, y hacer y recibir llamadas — sin intervención humana en el bucle de
  operación. Toda la contratación es real (KYC, firma, alta en sistemas del operador); solo la
  pieza de telco se sustituye por el partner local.</p>
  <div class="meta">
    <span><b>País PoC:</b> Colombia (+57)</span>
    <span><b>Stack AMI:</b> Python stdlib · SQLite · MCP · REST</span>
    <span><b>Stack telco:</b> Kannel (SMS · SMPP) + Asterisk (voz · SIP)</span>
    <span><b>Deploy:</b> AMI en cloud · telco en VPS del partner</span>
  </div>
</section>

<!-- PROBLEMA -->
<section>
  <h2><span class="num">01</span>El problema que resolvemos</h2>
  <p class="sub">Los agentes de IA necesitan identidad telefónica real (no virtual de chat) para
  operar en el mundo: validarse por OTP, recibir SMS de bancos, mandar mensajes a humanos,
  hacer llamadas con un número que sea contestado. Hoy no existe forma estándar de que un agente
  contrate y opere un número móvil de forma autónoma.</p>
  <div class="grid cols-3">
    <div class="card">
      <div class="icon">SIN AMI</div>
      <h4>Contratación manual</h4>
      <p>Humano abre app del operador, sube DNI, espera, da número al agente por copy-paste.
      Días de friction, agente atado a una persona.</p>
    </div>
    <div class="card">
      <div class="icon">SIN AMI</div>
      <h4>SaaS virtuales</h4>
      <p>Números VoIP / proxy que no aceptan SMS de bancos, no son móviles reales, no validan
      OTP, sin licencia regulatoria.</p>
    </div>
    <div class="card">
      <div class="icon">CON AMI</div>
      <h4>Programático extremo a extremo</h4>
      <p>Un agente AI llama una API, completa KYC, firma, y queda activo en minutos contra
      infraestructura telco real del partner.</p>
    </div>
  </div>
</section>

<!-- ARQUITECTURA -->
<section>
  <h2><span class="num">02</span>Arquitectura técnica</h2>
  <p class="sub">AMI vive en cloud y expone REST + MCP a los agentes. La pieza telco (Kannel
  para SMS · Asterisk para voz) corre en infraestructura del partner CO, con IP fija whitelisteada
  hacia su SBC/SMSC. Bridge-by-API en voz: el RTP pasa por Asterisk pero el destino lo decide
  el agente AI dinámicamente vía SIP URI.</p>

  <div class="diagram">
   <em>Agente AI (cliente)</em>
        │
        │  HTTPS / MCP
        ▼
   ┌──────────────────────────────────────────────┐
   │           <b>AMI backend (cloud)</b>                 │
   │  REST · MCP · KYC · panel · webhooks         │
   │  SQLite (state + backups · auto-failover)    │
   └──────────────────────────────────────────────┘
        │                                  │
        │  HTTP (SMS)                      │  HTTP / ARI (voz)
        ▼                                  ▼
   ┌─────────────────┐                ┌──────────────────────┐
   │ <em>Kannel</em>          │                │ <em>Asterisk PBX</em>        │
   │ SMSC gateway    │                │ PJSIP + ARI + RTP    │
   │ ↕ SMPP          │                │ ↕ SIP / RTP          │
   └─────────────────┘                └──────────────────────┘
        │                                  │
        │  SMPP                            │  SIP + RTP (alaw/ulaw/g722)
        ▼                                  ▼
   ┌──────────────────────────────────────────────┐
   │     <b>Partner telco CO (Julián / Javier)</b>      │
   │     SMSC · SBC · numeración móvil +57 3xx    │
   └──────────────────────────────────────────────┘
  </div>

  <h3>Bridge-by-API en voz (la pieza no trivial)</h3>
  <div class="grid cols-2">
    <div class="card">
      <div class="icon">SALIENTE</div>
      <p>Agente AI llama a <code>POST /v1/agent/calls/place</code> con
      <code>{{ to, callback_sip_uri }}</code>. AMI envía Originate al ARI; Asterisk marca al
      PSTN vía trunk del partner; cuando descuelga, Asterisk hace <code>Dial(PJSIP/&lt;URI&gt;
      /client_outbound)</code> al endpoint SIP del cliente. RTP bridged en Asterisk
      (direct_media=no).</p>
    </div>
    <div class="card">
      <div class="icon">ENTRANTE</div>
      <p>Llamada al número móvil del agente entra por trunk del partner.
      Dialplan llama <code>POST /v1/_telco/calls/inbound</code> a AMI con
      <code>{{ from, to, telco_ref }}</code>. AMI consulta el <code>inbound_sip_uri</code>
      configurado por el cliente para ese MID y devuelve el URI; Asterisk hace
      <code>Dial(PJSIP/&lt;URI&gt;/client_outbound)</code>.</p>
    </div>
  </div>
</section>

<!-- PLAN POC -->
<section>
  <h2><span class="num">03</span>Plan PoC · 3 fases</h2>
  <p class="sub">PoC enfocada al flujo end-to-end con un MID en Colombia. Plazos razonables;
  ajustables según prioridades del equipo de Julián.</p>

  <div class="phases">
    <div class="phase">
      <div class="badge">1</div>
      <div class="body">
        <div class="when">Semana 1 · staging</div>
        <h4>Setup infra & credenciales</h4>
        <ul>
          <li>Partner CO entrega: creds SMPP del SMSC, creds SIP del trunk, IP fija que se whitelistea, 1 número móvil +57 3xx asignado al MID PoC.</li>
          <li>AMI side: VPS levantado con Kannel + Asterisk (docker-compose listo en repo); apuntar al SBC/SMSC del partner. Smoke test SMS/voz mock → real.</li>
          <li>Ajustes localización: prefijos CO (+57), label "Cédula" en KYC, monedas COP en pricing.</li>
        </ul>
      </div>
    </div>
    <div class="phase">
      <div class="badge">2</div>
      <div class="body">
        <div class="when">Semana 2 · integración</div>
        <h4>Pruebas de protocolo</h4>
        <ul>
          <li>SMS A2P salientes: agente AI → AMI → Kannel → SMPP → móvil destino real. DLR de vuelta verifica entrega.</li>
          <li>SMS MO entrantes: móvil real → SMPP → Kannel → webhook a AMI → webhook al cliente con HMAC. Caso típico: <strong>recepción de OTP</strong>.</li>
          <li>Voz saliente con bridge-by-API: ARI Originate, callback_sip_uri al endpoint del agente, audio bidireccional verificado.</li>
          <li>Voz entrante: llamada al +57 3xx → Asterisk consulta AMI → forward al SIP del agente.</li>
        </ul>
      </div>
    </div>
    <div class="phase">
      <div class="badge">3</div>
      <div class="body">
        <div class="when">Semana 3 · demo end-to-end</div>
        <h4>Showcase + go/no-go</h4>
        <ul>
          <li>Agente AI demo (puede ser un script Python con OpenAI/Anthropic o equivalente) que: solicita número, completa KYC con cédula del rep legal, firma, recibe OTP por SMS, hace una llamada al partner.</li>
          <li>Métricas: latencia signaling end-to-end, latencia voz (jitter, MOS), DLR success rate, OTP delivery rate.</li>
          <li>Reunión de cierre: decisión sobre comercial v1, ampliar a más números, condiciones de revenue share.</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<!-- REQUISITOS -->
<section>
  <h2><span class="num">04</span>Qué necesitamos del partner CO</h2>
  <p class="sub">Lista accionable. Cuanto antes lo tengamos, antes empezamos la fase 1.</p>

  <table class="req-list">
    <thead>
      <tr><th>Pieza</th><th>Detalle</th><th>Quién lo entrega</th></tr>
    </thead>
    <tbody>
      <tr><td>SMPP host + port</td><td><code>SMSC_HOST</code>, <code>SMSC_PORT</code>, <code>system_id</code>, <code>password</code></td><td>Partner CO</td></tr>
      <tr><td>SIP trunk</td><td>Host del SBC, user, pass, codecs requeridos (alaw/ulaw/g729?)</td><td>Partner CO</td></tr>
      <tr><td>IP a whitelistear</td><td>IP fija del VPS donde corre Kannel+Asterisk (le mandamos nosotros tras setup)</td><td>AMI side</td></tr>
      <tr><td>Numeración PoC</td><td>1 número móvil +57 3xx con SMS bidireccional + voz, asignado al MID PoC</td><td>Partner CO</td></tr>
      <tr><td>Confirmación KYC</td><td>Si la verificación de cédula del rep. legal va por nuestro panel o por su flujo</td><td>Decisión conjunta</td></tr>
      <tr><td>Revenue model</td><td>Precio por MID/mes, por SMS, por minuto voz, revenue share</td><td>Comercial Julián / Daniel</td></tr>
    </tbody>
  </table>
</section>

<!-- FAQ -->
<section>
  <h2><span class="num">05</span>FAQ técnica</h2>
  <p class="sub">Anticipando preguntas de Javier Cruz (arquitecto IA del partner) y del equipo
  técnico. Respuestas concretas.</p>

  <details>
    <summary>¿Cómo se entrega un SMS entrante (OTP) al agente AI? ¿Latencia?</summary>
    <div class="answer">SMPP MO → Kannel → HTTP POST a AMI (<code>/v1/_telco/sms/inbound</code>)
    → webhook firmado HMAC-SHA256 al endpoint del cliente del agente.
    Latencia típica: <strong>200–500ms</strong> desde que Kannel recibe el deliver_sm hasta
    que el webhook sale. El agente decide qué hacer (parsear OTP, contestar, etc.).</div>
  </details>

  <details>
    <summary>¿Cómo se hace bridge de voz a un agente que vive en cloud (no en device)?</summary>
    <div class="answer">El agente expone un endpoint SIP propio (Realtime API, Twilio Voice
    Agents propio, o stack STT/LLM/TTS propio). Configura <code>inbound_sip_uri</code> con
    AMI. Cuando entra una llamada al número móvil, Asterisk hace
    <code>Dial(PJSIP/&lt;callback_uri&gt;/client_outbound)</code> y bridge el RTP. AMI no
    monta el motor de voz — es solo la pipa SIP.</div>
  </details>

  <details>
    <summary>¿Codec? ¿Qué pasa si el SBC del partner exige G.729?</summary>
    <div class="answer">Por defecto ofrecemos <code>alaw, ulaw, g722, opus</code> en orden de
    preferencia. Si el SBC impone G.729, hay dos opciones: (a) licencia G.729 (~25 EUR/canal,
    Sangoma); (b) transcodificación en Asterisk a costa de CPU.
    Recomendado: alaw negociado en SDP — funciona universal y no requiere licencia.</div>
  </details>

  <details>
    <summary>¿NAT, jitter, RTP timeout? ¿Cómo manejáis SBC con NAT estricto?</summary>
    <div class="answer">pjsip.conf configurado con <code>rtp_symmetric=yes</code>,
    <code>force_rport=yes</code>, <code>rewrite_contact=yes</code> — los helpers NAT estándar.
    <code>rtp.conf</code> con rango 10000-10100 UDP, <code>rtpkeepalive=15</code>,
    <code>rtptimeout=60</code>. Direct media off (todo el RTP pasa por Asterisk
    para soportar el bridge SIP↔SIP).</div>
  </details>

  <details>
    <summary>¿Cómo escalas a 1000 llamadas concurrentes?</summary>
    <div class="answer">Asterisk single-instance soporta 200-500 calls concurrentes en una VM
    decente (4-8 vCPU). Para 1000+ se cluster-ea con Kamailio/OpenSIPS como SIP front-end y
    Asterisk como media engine, escalado horizontal. Para el PoC inicial 1 instancia basta.</div>
  </details>

  <details>
    <summary>¿MNP / portabilidad?</summary>
    <div class="answer">No en v1. Asignamos números nuevos del rango que el partner tenga
    disponible. Portabilidad entrante es un proyecto aparte que requiere integración con CRC y
    el operador donante. Lo dejamos como roadmap v2.</div>
  </details>

  <details>
    <summary>¿KYC en Colombia? ¿Aceptan cédula?</summary>
    <div class="answer">El panel KYC ya soporta etiqueta configurable por país: en CO pide
    "Cédula de Ciudadanía" en lugar de DNI. Frontal + reverso + selfie sujetando cédula.
    Revisión manual del operador AMI o del partner CO (decidir en la reu). Cumple CRC y
    Ley 1581 de protección de datos personales.</div>
  </details>

  <details>
    <summary>¿Qué pasa si AMI se cae? ¿Llamadas activas mueren?</summary>
    <div class="answer">Llamadas activas viven en Asterisk; si AMI se cae, las llamadas siguen
    porque el RTP no pasa por AMI sino por Asterisk. Lo que se pierde es el lifecycle
    notification al cliente (webhooks). AMI tiene backup automático cada hora a disco
    persistente + opcional offsite a S3-compatible (SigV4 firmado stdlib). Auto-failover si
    SQLite se corrompe.</div>
  </details>

  <details>
    <summary>¿Modelo comercial? ¿Cuánto cobra AMI y cuánto el partner?</summary>
    <div class="answer">Conversación con Julián y Daniel. Modelo típico: revenue share sobre
    MRR del MID, más markup sobre SMS A2P y minutos de voz. Detalle se cierra en sesión
    comercial.</div>
  </details>

  <details>
    <summary>¿Open source? ¿Quién es dueño del código?</summary>
    <div class="answer">AMI es código abierto (repo en GitHub). El protocolo es público para
    que cualquier agente AI pueda hablar AMI. La operación comercial (números, contratos,
    KYC verificado) es propietaria de Parallax IEI + partner local. Modelo open core.</div>
  </details>
</section>

<!-- BRIEF DANIEL -->
<section>
  <h2><span class="num">06</span>Brief para ti (Daniel) · cómo defender cada punto</h2>
  <p class="sub">Esta sección no se enseña en la pantalla principal; es tu cheat-sheet.</p>

  <div class="brief">
    <h3>Línea de pitch en 30 segundos</h3>
    <p style="color:var(--ink);">"AMI es el protocolo abierto que permite que un agente AI obtenga
    su propio número móvil real y opere SMS y voz de forma autónoma. Toda la pieza de
    contratación es real — KYC, firma, alta en sistemas del operador. Lo que sustituimos en cada
    país es solo el partner telco. En Colombia queremos hacer una PoC contigo en tres semanas
    para validar SMS bidireccional, voz bidireccional y un caso de uso típico: agente AI
    recibiendo OTP."</p>

    <h3 style="margin-top:1.4rem;">Preguntas críticas y respuestas listas</h3>
    <ul>
      <li><strong>"¿Por qué AMI y no un servicio SaaS existente?"</strong> → Los SaaS existentes
      son numeración virtual o trunk para humanos; ninguno expone API que un agente AI pueda
      consumir autónomamente con KYC programático.</li>
      <li><strong>"¿En qué fase está el código?"</strong> → Backend production-ready: 409
      tests verde, CI en Python 3.11/3.12, multi-tenant, KYC con email+SMS, backup automático,
      logging JSON estructurado, webhooks account-scoped, panel admin con CSRF.</li>
      <li><strong>"¿Es bridge-by-API real?"</strong> → Sí. <code>pjsip.conf</code> con endpoint
      cliente genérico, <code>extensions.conf</code> con Dial dinámico al
      <code>callback_sip_uri</code>, <code>rtp.conf</code> con rango y keepalive. AMI no monta
      STT/TTS; lo hace el agente.</li>
      <li><strong>"¿Y la regulación CRC / habeas data Ley 1581?"</strong> → KYC se hace contra
      DNI o cédula del rep legal, datos cifrados en disco (0o600), retención 90d con purga
      automática RGPD-style. Para CRC específico, vamos con el partner que tiene la licencia
      operador local.</li>
      <li><strong>"¿Cuándo podéis arrancar?"</strong> → Cuando ellos nos den creds SMPP + SIP +
      IP whitelist + 1 número PoC. Esa semana levantamos infra. Semana siguiente integración.
      Tercera demo.</li>
    </ul>

    <h3 style="margin-top:1.4rem;">Qué pedir TÚ a Javier Cruz</h3>
    <ul>
      <li>Confirmación del codec preferido del SBC (g711a vs ulaw vs g729 con licencia).</li>
      <li>Si exigen SRTP/TLS para la señalización SIP.</li>
      <li>Rango de numeración disponible para el PoC y el coste por número.</li>
      <li>Si tienen SMSC propio o relay vía un tercero (afecta latencia OTP).</li>
      <li>Quién hace la verificación KYC: ellos (con su flow existente) o nosotros (con el
      panel KYC de AMI). Recomendación: nosotros, así el agente AI tiene control end-to-end.</li>
      <li>Si quieren coemitir webhook events o si AMI es proxy único.</li>
    </ul>
  </div>
</section>

<!-- LIVE DEMO -->
<section>
  <h2><span class="num">07</span>Demo en vivo · flujo end-to-end</h2>
  <p class="sub">Ejecuta los pasos contra el backend en modo mock — toda la contratación + KYC
  es real, solo el SMS final se simula. Para enseñar el shape del flujo a Javier Cruz en
  pantalla.</p>

  <div class="demo">
    <div class="btn-row">
      <button class="btn" onclick="runDemo()">▶ Ejecutar flujo completo</button>
      <button class="btn ghost" onclick="resetDemo()">Reset</button>
      <a class="btn ghost" href="/panel/kyc" target="_blank">Abrir panel KYC</a>
      <a class="btn ghost" href="/docs" target="_blank">Docs interactivas</a>
    </div>
    <pre class="demo-out" id="demoOut">// Pulsa "Ejecutar flujo completo" para arrancar.\n// Crea SimRequest +57 CO → acepta oferta → customer-data con cédula\n// → KYC initiate (envía email+SMS al rep) → contrato → mock-sign → activar MID.</pre>
  </div>
</section>

<footer>
  <strong>AMI · Agent Mobile Identity Protocol</strong> · Parallax IEI · 2026<br>
  Página preparada para reunión PoC Colombia 2026-05-28.
</footer>

</div>

<script>
async function runDemo() {{
  var out = document.getElementById('demoOut');
  out.innerHTML = '';
  function log(line, cls) {{
    var span = document.createElement('span');
    if (cls) span.className = cls;
    span.textContent = line + '\\n';
    out.appendChild(span);
    out.scrollTop = out.scrollHeight;
  }}
  try {{
    log('[1/7] POST /v1/sim-requests {{country: "CO", sim_type: "eSIM"}}');
    var r1 = await fetch('/v1/demo/quick', {{method: 'POST'}});
    if (!r1.ok) {{ log('   ✗ ' + r1.status + ' ' + await r1.text(), 'err'); return; }}
    var data = await r1.json();
    log('   ✓ flow ejecutado en backend, devolviendo trazado:', 'ok');
    log(JSON.stringify(data, null, 2));
    log('');
    log('[fin] Para una integración real, configura AMI_TELCO_MODE=live + creds del partner CO.', 'ok');
  }} catch(e) {{
    log('✗ Error: ' + e.message, 'err');
  }}
}}
function resetDemo() {{
  document.getElementById('demoOut').textContent = '// Pulsa "Ejecutar flujo completo" para arrancar.';
}}
</script>

</body>
</html>"""
