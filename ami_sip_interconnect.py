"""Página `/poc-co/sip` — SIP Interconnect Specification.

Documento técnico que va al detalle del enlace SIP entre nuestro Asterisk
y el SBC del partner CO. Reunión "Revisión Troncal SIP" 2026-05-28 16:30.

Audiencia: equipo NOC/voz del partner (Julián, Javier Cruz, ingenieros SIP).
Tono: spec sheet de interconexión, no marketing.

Estructura:
  1. Topología + IPs/FQDNs
  2. Parámetros del trunk (tabla 2 columnas)
  3. Numeración E.164 (formato outbound/inbound)
  4. Codecs (orden + ptime)
  5. SRTP / TLS
  6. RTP (rango + timeouts + keepalive)
  7. NAT helpers
  8. SIP headers (From, PAI, Diversion)
  9. Routing entrantes/salientes
 10. Error codes esperados
 11. DTMF
 12. Capacity
 13. Test plan PoC (6 tests concretos)
 14. Preguntas para Daniel hacer al partner
 15. Brief privado para Daniel
"""
from __future__ import annotations


_CSS = """
  :root {
    --bg: #06060a; --bg-soft: #0c0c14; --surface: #14141d; --line: #1f1f2c;
    --line-strong: #2a2a3a; --ink: #ededf2; --ink-soft: #8888a0; --ink-mute: #5a5a70;
    --accent: #8b6cff; --accent-2: #5dd1ff; --green: #4ade80; --amber: #fbbf24; --red: #ff6b8a;
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
    background-attachment: fixed; min-height: 100vh;
  }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { color: #b9e6ff; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 2.5rem 1.5rem; }

  .doc-head { padding-bottom: 1.5rem; border-bottom: 1px solid var(--line); margin-bottom: 2rem; }
  .doc-head .eyebrow { font-family: var(--mono); font-size: 0.7rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em; margin-bottom: 0.6rem; }
  .doc-head h1 { font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 0.6rem; line-height: 1.15; }
  .doc-head .meta { display: flex; gap: 2rem; flex-wrap: wrap; font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute); }
  .doc-head .meta b { color: var(--ink); font-weight: 500; }

  section { padding: 1.6rem 0; border-bottom: 1px solid var(--line); }
  section:last-of-type { border-bottom: 0; }
  h2 { font-size: 1.2rem; font-weight: 600; letter-spacing: -0.01em; margin: 0 0 1rem; }
  h2 .num { color: var(--ink-mute); font-family: var(--mono); font-size: 0.9rem; margin-right: 0.6rem; font-weight: 600; }
  p { color: var(--ink-soft); line-height: 1.6; font-size: 0.95rem; }
  p code { font-family: var(--mono); background: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.86em; color: var(--accent-2); }
  ul { color: var(--ink-soft); line-height: 1.7; font-size: 0.95rem; padding-left: 1.2rem; }
  ul code { font-family: var(--mono); background: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.86em; color: var(--accent-2); }
  ul ul { margin-top: 0.3rem; }

  /* Spec table */
  table.spec { width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.9rem; }
  table.spec th, table.spec td { padding: 0.65rem 0.9rem; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
  table.spec thead th { color: var(--ink-mute); font-family: var(--mono); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.12em; font-weight: 500; background: var(--bg-soft); }
  table.spec td:first-child { color: var(--ink); font-weight: 500; width: 28%; }
  table.spec td:nth-child(2), table.spec td:nth-child(3) { color: var(--ink-soft); }
  table.spec code { font-family: var(--mono); background: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.86em; color: var(--accent-2); }
  table.spec .pill { font-family: var(--mono); font-size: 0.68rem; padding: 0.15rem 0.5rem; border-radius: 99px; text-transform: uppercase; letter-spacing: 0.1em; }
  table.spec .pill.req { background: rgba(74,222,128,0.10); color: var(--green); }
  table.spec .pill.ask { background: rgba(251,191,36,0.10); color: var(--amber); }
  table.spec .pill.opt { background: rgba(136,136,160,0.10); color: var(--ink-mute); }

  /* Diagram */
  .diagram { font-family: var(--mono); font-size: 0.78rem; line-height: 1.35; background: var(--bg-soft); border: 1px solid var(--line); border-radius: 12px; padding: 1.2rem; overflow-x: auto; color: var(--ink-soft); white-space: pre; }
  .diagram b { color: var(--accent-2); font-weight: 600; }
  .diagram em { color: var(--accent); font-style: normal; font-weight: 600; }

  /* Brief */
  .brief { background: linear-gradient(180deg, rgba(139,108,255,0.06), rgba(93,209,255,0.04)); border: 1px solid rgba(139,108,255,0.25); border-radius: 12px; padding: 1.4rem 1.6rem; }
  .brief h3 { margin-top: 0; color: var(--accent); }
  .brief ol { color: var(--ink); line-height: 1.7; padding-left: 1.2rem; }
  .brief ol code { font-family: var(--mono); background: rgba(0,0,0,0.3); padding: 0.1rem 0.4rem; border-radius: 4px; color: var(--accent-2); font-size: 0.86em; }

  /* Pre code block */
  pre.snippet { font-family: var(--mono); font-size: 0.78rem; background: #000; color: #cfd2dc; border: 1px solid var(--line-strong); border-radius: 8px; padding: 1rem; overflow-x: auto; line-height: 1.5; }
  pre.snippet .c { color: var(--ink-mute); }
  pre.snippet .k { color: var(--accent); }
  pre.snippet .s { color: var(--accent-2); }

  .nav-back { font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute); margin-bottom: 1rem; display: inline-block; }
  .nav-back:before { content: '← '; }

  @media print {
    body { background: white !important; color: black !important; }
    section, .doc-head, .brief, table.spec th, .diagram, pre.snippet {
      background: white !important; color: black !important; border-color: #ddd !important;
    }
    a { color: black !important; }
    .nav-back, .brief { display: none !important; }
  }
"""


def render_sip_interconnect_page() -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · SIP Interconnect Spec · PoC Colombia</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">

<a class="nav-back" href="/poc-co">PoC Colombia</a>

<div class="doc-head">
  <div class="eyebrow">documento de interconexión · revisión troncal SIP</div>
  <h1>SIP Trunk Interconnect Specification</h1>
  <div class="meta">
    <span><b>Versión:</b> v1 draft</span>
    <span><b>Audiencia:</b> NOC / Voice Eng partner CO</span>
    <span><b>Fecha:</b> 2026-05-28</span>
    <span><b>Sesión:</b> 16:30 — Julián, Javier Cruz, Daniel</span>
  </div>
</div>

<!-- TOPOLOGÍA -->
<section>
  <h2><span class="num">01</span>Topología de la interconexión</h2>
  <p>Una sesión SIP/RTP entre dos elementos: nuestro Asterisk PJSIP (lado AMI, VPS público
  con IP fija) y el SBC del partner (lado CO). El RTP siempre fluye por Asterisk (no usamos
  direct media) para preservar el bridge SIP↔SIP hacia el cliente AMI.</p>

  <div class="diagram">
     <em>Cliente AMI (Agente AI)</em>
                  │  SIP/RTP — callback_sip_uri dinámico
                  ▼
     <b>Asterisk PJSIP</b>  (AMI side · VPS · IP fija a confirmar)
                  │  Trunk SIP <em>trunk_partner</em>
                  │  SIP signaling: UDP 5060 / TCP / TLS 5061  (a decidir)
                  │  RTP media:    UDP 10000-10100             (a confirmar)
                  ▼
     <b>SBC del partner CO</b>  (host/IP, puerto, transport · a confirmar)
                  │
                  ▼
        Red móvil del operador  ·  PSTN  ·  numeración +57 3xx
  </div>
</section>

<!-- PARÁMETROS -->
<section>
  <h2><span class="num">02</span>Parámetros del trunk</h2>
  <p>Tabla de configuración del trunk. Lo marcado como <span class="pill ask">a confirmar</span>
  son los puntos a cerrar en la reunión.</p>

  <table class="spec">
    <thead><tr><th>Parámetro</th><th>AMI side (nuestro)</th><th>Partner CO (vuestro)</th><th></th></tr></thead>
    <tbody>
      <tr><td>IP / FQDN</td><td><code>&lt;IP fija VPS&gt;</code> (entregable post-setup)</td><td><code>sbc.partner.co</code></td><td><span class="pill ask">confirmar</span></td></tr>
      <tr><td>Puerto SIP</td><td><code>5060</code> (UDP) · <code>5061</code> (TLS)</td><td><code>5060 / 5061</code></td><td><span class="pill ask">confirmar</span></td></tr>
      <tr><td>Transport</td><td>UDP por defecto; TCP/TLS si lo exigís</td><td>?</td><td><span class="pill ask">confirmar</span></td></tr>
      <tr><td>Auth mode</td><td>Soporta REGISTER o IP-based</td><td>?</td><td><span class="pill ask">confirmar</span></td></tr>
      <tr><td>Credenciales (si REGISTER)</td><td>username + password en pjsip.conf</td><td><code>SIP_TRUNK_USERNAME</code> + <code>SIP_TRUNK_PASSWORD</code></td><td><span class="pill ask">vuestro side</span></td></tr>
      <tr><td>OPTIONS keepalive</td><td><code>qualify_frequency = 60s</code></td><td>?</td><td><span class="pill opt">opcional</span></td></tr>
      <tr><td>Failover SBC</td><td>Soportamos múltiples AOR contacts</td><td>?</td><td><span class="pill opt">si aplica</span></td></tr>
    </tbody>
  </table>
</section>

<!-- NUMERACIÓN -->
<section>
  <h2><span class="num">03</span>Numeración E.164 · formato esperado</h2>
  <p>Necesitamos confirmar el formato exacto en From/To, ya que cada operador tiene su norma.</p>

  <table class="spec">
    <thead><tr><th>Caso</th><th>Lo que enviamos</th><th>Lo que esperamos recibir</th></tr></thead>
    <tbody>
      <tr><td>Saliente (móvil CO)</td><td><code>+573001234567</code> en To/RURI</td><td>—</td></tr>
      <tr><td>Saliente (internacional)</td><td><code>+34600111222</code> en To/RURI</td><td>—</td></tr>
      <tr><td>Entrante al MID</td><td>—</td><td><code>+573001234567</code> en To/RURI</td></tr>
      <tr><td>Caller-ID (From)</td><td>número del MID (asignado por vosotros)</td><td>MSISDN del llamante</td></tr>
    </tbody>
  </table>
  <p style="margin-top:1rem"><strong>A confirmar con vosotros:</strong> ¿formato con <code>+</code>?
  ¿prefijo nacional <code>0057</code>? ¿solo dígitos <code>573...</code>? Asterisk normaliza en
  el dialplan tras conocer la regla.</p>
</section>

<!-- CODECS -->
<section>
  <h2><span class="num">04</span>Codecs</h2>
  <p>Ofrecemos en SDP, en orden de preferencia:</p>
  <ul>
    <li><code>alaw</code> · G.711 A-law · 64 kbps · default Europa, soportado universal</li>
    <li><code>ulaw</code> · G.711 μ-law · 64 kbps · default Norteamérica/LATAM</li>
    <li><code>g722</code> · HD Voice · 16 kHz · 64 kbps · si ambos extremos lo aceptan</li>
    <li><code>opus</code> · adaptive 8-48kHz · 6-510 kbps · raro en PSTN, ideal cliente-cliente</li>
  </ul>
  <p><strong>G.729:</strong> deshabilitado por defecto. Requiere licencia comercial (~25 EUR/canal,
  Sangoma). Si vuestro SBC lo exige, lo activamos previa decisión.</p>
  <p><strong>ptime:</strong> 20ms (default RFC 3550). Confirmar si requerís
  <code>use_ptime=yes</code> para forzar el ptime negociado.</p>

  <pre class="snippet"><span class="c">; pjsip.conf — trunk_partner</span>
<span class="k">disallow</span> = all
<span class="k">allow</span> = alaw,ulaw,g722,opus    <span class="c">; G.729 comentado</span>
<span class="k">direct_media</span> = no             <span class="c">; RTP pasa por Asterisk</span>
<span class="k">dtmf_mode</span> = rfc4733
<span class="k">ice_support</span> = no             <span class="c">; server-server, no ICE</span></pre>
</section>

<!-- SRTP/TLS -->
<section>
  <h2><span class="num">05</span>SRTP y SIP-TLS</h2>
  <p>Listos para activar pero <em>deshabilitados por defecto</em>. Si vuestro SBC exige cifrado:</p>
  <ul>
    <li><strong>SRTP-SDES:</strong> activamos <code>media_encryption = sdes</code> en pjsip.conf.
    Compatible con <code>media_encryption_optimistic = yes</code> para fallback gracioso a RTP en
    claro si el SBC no responde con SRTP.</li>
    <li><strong>SIP-TLS:</strong> activamos <code>transport = transport-tls</code> en
    el endpoint. Requiere cert (Let's Encrypt en el VPS) y que vuestro SBC valide nuestra IP/FQDN.</li>
  </ul>
  <p><span class="pill ask">confirmar</span> ¿Exigís SRTP? ¿TLS? Si sí, ¿qué cipher suite mínimo?
  ¿mutual TLS o solo server cert?</p>
</section>

<!-- RTP -->
<section>
  <h2><span class="num">06</span>RTP · rango, timeouts, keepalive</h2>
  <p>Configuración actual en <code>rtp.conf</code>:</p>
  <table class="spec">
    <thead><tr><th>Setting</th><th>Valor</th><th>Por qué</th></tr></thead>
    <tbody>
      <tr><td><code>rtpstart</code> / <code>rtpend</code></td><td><code>10000 / 10100</code></td><td>101 puertos UDP simétricos (51 calls concurrentes/instance). Ajustamos al alza si CPS lo requiere.</td></tr>
      <tr><td><code>rtpkeepalive</code></td><td><code>15</code> s</td><td>Manda paquetes RTP "silence" cada 15s para mantener pinhole NAT abierto.</td></tr>
      <tr><td><code>rtptimeout</code></td><td><code>60</code> s</td><td>Si no llega RTP en 60s → hangup automático. Protección contra "calls fantasma".</td></tr>
      <tr><td><code>rtpholdtimeout</code></td><td><code>300</code> s</td><td>Para llamadas en HOLD que no reciben RTP. 5 min.</td></tr>
    </tbody>
  </table>
  <p><span class="pill ask">confirmar</span> ¿Qué rango RTP nos vais a ver desde vuestro SBC?
  Si hay firewall intermedio, necesitamos que abráis el rango 10000-10100/UDP desde nuestra IP fija.</p>
</section>

<!-- NAT -->
<section>
  <h2><span class="num">07</span>NAT helpers</h2>
  <p>Aplicamos los 3 helpers estándar — vuestro SBC seguramente no está NAT-ado pero los nuestros
  pueden estarlo (depende del VPS).</p>
  <pre class="snippet"><span class="k">rtp_symmetric</span> = yes      <span class="c">; manda RTP de vuelta al mismo puerto que recibió</span>
<span class="k">force_rport</span> = yes        <span class="c">; usa el puerto desde el que llegó el SIP, no el anunciado</span>
<span class="k">rewrite_contact</span> = yes    <span class="c">; reescribe Contact con la IP real del peer</span></pre>
  <p>Si la VPS donde corre Asterisk está detrás de NAT (caso normal en Hetzner/Digital Ocean), también
  añadimos <code>external_media_address</code> y <code>external_signaling_address</code> apuntando a
  la IP pública.</p>
</section>

<!-- SIP HEADERS -->
<section>
  <h2><span class="num">08</span>SIP headers críticos</h2>
  <table class="spec">
    <thead><tr><th>Header</th><th>Outbound (nosotros → vosotros)</th><th>Inbound (vosotros → nosotros)</th></tr></thead>
    <tbody>
      <tr><td><code>From</code></td><td>número del MID que origina la llamada</td><td>MSISDN del llamante (caller-ID)</td></tr>
      <tr><td><code>To</code> / <code>R-URI</code></td><td>destino E.164</td><td>número del MID destino</td></tr>
      <tr><td><code>Contact</code></td><td><code>sip:USER@ami-ip:5060</code></td><td><code>sip:user@sbc-ip:5060</code></td></tr>
      <tr><td><code>P-Asserted-Identity</code> (PAI)</td><td>opcional — si lo exigís para billing</td><td>opcional</td></tr>
      <tr><td><code>Diversion</code></td><td>solo en call-forward</td><td>solo en call-forward</td></tr>
      <tr><td><code>User-Agent</code></td><td><code>Asterisk PBX/20.5</code></td><td>—</td></tr>
    </tbody>
  </table>
  <p><span class="pill ask">confirmar</span> ¿Requerís header PAI con el caller-ID real?
  ¿Algún header propietario para identificar el trunk en vuestro SBC?</p>
</section>

<!-- ROUTING -->
<section>
  <h2><span class="num">09</span>Routing entrantes / salientes</h2>

  <h3 style="margin-bottom:0.5rem;">Salientes (AMI → SBC)</h3>
  <pre class="snippet"><span class="c">; extensions.conf — bridge-by-API saliente</span>
<span class="k">exten</span> =&gt; bridge,1,Set(STATUS_URL=${{AMI_API}}/v1/_telco/calls/${{AMI_CALL_ID}}/status)
 same =&gt; n,System(curl ...&quot;status&quot;:&quot;ringing&quot;... )
 same =&gt; n,Dial(PJSIP/${{CALLBACK_SIP_URI}}/client_outbound,60,g)
 same =&gt; n,System(curl ...&quot;status&quot;:&quot;completed&quot;... )</pre>

  <h3 style="margin-bottom:0.5rem;">Entrantes (SBC → AMI)</h3>
  <pre class="snippet"><span class="c">; extensions.conf — bridge-by-API entrante</span>
<span class="k">exten</span> =&gt; _X.,1,Set(LOOKUP=${{SHELL(ami_inbound.sh ...)}})
 same =&gt; n,Set(FORWARD=${{CUT(FWD_FIELD,=,2)}})    <span class="c">; sip:agent@cliente</span>
 same =&gt; n,GotoIf($[&quot;${{FORWARD}}&quot; = &quot;&quot;]?reject:forward)
 same =&gt; n(forward),Dial(PJSIP/${{FORWARD}}/client_outbound,60,g)</pre>

  <p>Cuando un INVITE llega a vuestro SBC para un MID, esperamos que lo forward-eéis a nuestra IP
  fija con <code>To: +573001234567</code>. Asterisk lo recibe, llama a AMI para obtener el SIP del
  agente, y hace Dial.</p>
</section>

<!-- ERRORES -->
<section>
  <h2><span class="num">10</span>Error codes (mapeo SIP → AMI state)</h2>
  <table class="spec">
    <thead><tr><th>Código SIP</th><th>Significado</th><th>Estado AMI Call</th></tr></thead>
    <tbody>
      <tr><td><code>200 OK</code></td><td>Answered</td><td><code>in_progress</code></td></tr>
      <tr><td><code>180 / 183</code></td><td>Ringing</td><td><code>ringing</code></td></tr>
      <tr><td><code>486</code></td><td>Busy here</td><td><code>failed (busy)</code></td></tr>
      <tr><td><code>480</code></td><td>Temporarily unavailable</td><td><code>failed (unavailable)</code></td></tr>
      <tr><td><code>487</code></td><td>Request terminated (CANCEL)</td><td><code>failed (cancelled)</code></td></tr>
      <tr><td><code>488</code></td><td>Not acceptable here (codec mismatch)</td><td><code>failed (codec)</code></td></tr>
      <tr><td><code>503</code></td><td>Service unavailable (SBC down)</td><td><code>failed (sbc_unavailable)</code></td></tr>
      <tr><td><code>603</code></td><td>Decline</td><td><code>failed (declined)</code></td></tr>
    </tbody>
  </table>
</section>

<!-- DTMF -->
<section>
  <h2><span class="num">11</span>DTMF</h2>
  <p>Por defecto: <code>rfc4733</code> (RTP events, RFC 4733/2833). Soportamos también
  <code>inband</code> (in-audio) y <code>info</code> (SIP INFO method).</p>
  <p><span class="pill ask">confirmar</span> ¿Qué modo usa vuestro SBC? Si transit-only,
  rfc4733 es la elección segura.</p>
</section>

<!-- CAPACITY -->
<section>
  <h2><span class="num">12</span>Capacity expected (PoC)</h2>
  <table class="spec">
    <thead><tr><th>Métrica</th><th>PoC (semanas 1-3)</th><th>Producción v1</th></tr></thead>
    <tbody>
      <tr><td>Calls concurrentes</td><td>1-5 (testing)</td><td>50-200</td></tr>
      <tr><td>CPS (calls per second)</td><td>1</td><td>2-5</td></tr>
      <tr><td>BHCA</td><td>20-50</td><td>1000-5000</td></tr>
      <tr><td>SMS/min outbound</td><td>10</td><td>500-1000</td></tr>
      <tr><td>SMS/min inbound (OTP)</td><td>5</td><td>200-500</td></tr>
    </tbody>
  </table>
  <p><span class="pill ask">confirmar</span> ¿Hay límite de CPS / concurrent en vuestro lado?
  ¿Hay diferenciación CPS por destino (móvil vs fijo)?</p>
</section>

<!-- TEST PLAN -->
<section>
  <h2><span class="num">13</span>Test plan de la troncal (6 tests)</h2>
  <p>Plan secuencial. Cada test desbloquea el siguiente. Total estimado: 2-3 horas con NOC del partner.</p>

  <table class="spec">
    <thead><tr><th>#</th><th>Test</th><th>Esperado</th></tr></thead>
    <tbody>
      <tr><td><code>T01</code></td><td>OPTIONS ping en ambos sentidos</td><td>200 OK · trunk reachable</td></tr>
      <tr><td><code>T02</code></td><td>REGISTER (si auth=register) o IP-recognition</td><td>200 OK · Asterisk muestra <code>Registered</code></td></tr>
      <tr><td><code>T03</code></td><td>INVITE saliente a un test number (vuestro lab)</td><td>200 OK → audio bidireccional 30s → BYE limpio</td></tr>
      <tr><td><code>T04</code></td><td>INVITE entrante a +57 3xx asignado al MID PoC</td><td>SBC forward a nuestra IP · Asterisk hace Dial al agente · audio bidireccional</td></tr>
      <tr><td><code>T05</code></td><td>DTMF: digit 5 enviado mid-call</td><td>Lo recibimos correctamente en RTP-events</td></tr>
      <tr><td><code>T06</code></td><td>SMS MO al MID (OTP típico)</td><td>SBC/SMSC → Kannel webhook → AMI webhook firmado al cliente &lt;500ms</td></tr>
    </tbody>
  </table>
</section>

<!-- PREGUNTAS PARA DANIEL -->
<section>
  <h2><span class="num">14</span>Preguntas concretas para esta sesión</h2>
  <p>Conviene cerrarlas en la reunión para no bloquear la fase 1 de la PoC.</p>
  <ol style="color:var(--ink-soft);line-height:1.8;padding-left:1.4rem;">
    <li><strong>SBC host/FQDN + puerto + transport</strong> — UDP, TCP o TLS?</li>
    <li><strong>Auth mode</strong> — REGISTER (con user+pass) o IP-based (whitelisting nuestra IP fija)?</li>
    <li><strong>Codec</strong> — confirmar alaw como default. ¿Exigís G.729 (con licencia)?</li>
    <li><strong>SRTP / SIP-TLS</strong> — obligatorio o opcional? Si obligatorio, ¿qué cipher mínimo?</li>
    <li><strong>Formato E.164</strong> en From/To — <code>+57...</code>, <code>57...</code>, o <code>0057...</code>?</li>
    <li><strong>PAI / Diversion</strong> — ¿headers requeridos por vuestro billing?</li>
    <li><strong>Numeración PoC</strong> — ¿qué rango +57 3xx tenéis disponible y a qué coste por número/mes?</li>
    <li><strong>DTMF mode</strong> — rfc4733 OK?</li>
    <li><strong>Capacity</strong> — límite CPS y concurrent en vuestro lado durante PoC?</li>
    <li><strong>OPTIONS / qualify</strong> — ¿hacéis qualify hacia nosotros? Si sí, cada cuántos segundos?</li>
    <li><strong>Inbound routing</strong> — ¿forward INVITE a nuestra IP fija o tenemos que configurar algo en vuestro portal?</li>
    <li><strong>SMS</strong> — ¿SMSC propio (SMPP directo) o relay? Host, puerto, system_id, password.</li>
    <li><strong>Plazos</strong> — ¿cuándo podemos arrancar T01? ¿Quién es la persona de contacto técnico para el día a día de la PoC?</li>
  </ol>
</section>

<!-- BRIEF DANIEL -->
<section>
  <h2><span class="num">15</span>Brief privado para Daniel</h2>
  <div class="brief">
    <h3>Estructura sugerida de la conversación (16:30)</h3>
    <ol>
      <li><strong>Min 0-5 · contexto</strong> — Resumen 2 frases de qué es AMI y qué hace este trunk:
      "El trunk es la pieza que conecta nuestro Asterisk con vuestro SBC. Por ahí pasan las llamadas
      del agente AI al PSTN y de vuelta. Hoy queremos cerrar los 13 parámetros del documento."</li>

      <li><strong>Min 5-25 · sección 02 (Parámetros)</strong> — Repasar la tabla punto por punto.
      Si Javier insiste en REGISTER, OK. Si prefiere IP-based, también — necesitamos saber la IP
      fija del VPS ANTES de fase 1.</li>

      <li><strong>Min 25-40 · sección 04 (Codecs) + 05 (SRTP)</strong> — Defender <code>alaw</code>
      como primario. Si exigen G.729, hablar de licencia (~25€/canal) o transcoding. SRTP: solo si
      lo exigen.</li>

      <li><strong>Min 40-50 · sección 03 (Numeración) + 08 (Headers)</strong> — Cerrar formato
      E.164 exacto. Es la fuente más común de bugs en interconexión.</li>

      <li><strong>Min 50-60 · sección 13 (Test plan)</strong> — Acordar fecha para T01. Si quieren
      mostrarse rápidos, propón <strong>el viernes 30/05</strong> para empezar tests.</li>
    </ol>

    <h3 style="margin-top:1.2rem;">Banderas rojas (cosas que pueden retrasarte)</h3>
    <ul style="color:var(--ink);">
      <li>Si te dicen "necesitamos un PoP en Colombia" — significa que su SBC no admite peering
      internacional. Sale más caro (VPS en CO) y tarda más (proveedor local). Pregunta si pueden
      hacer una excepción durante PoC.</li>
      <li>Si exigen TLS + SRTP + mutual auth desde día 1 — añade 3-5 días de setup. Empuja a
      "lo activamos en fase 2 producción, PoC va en UDP plano".</li>
      <li>Si el código SIP/RTP de su SBC es "propietario" (no estándar Asterisk-friendly) —
      pedir captura PCAP de un ejemplo para entender qué dialect hablan.</li>
    </ul>

    <h3 style="margin-top:1.2rem;">Cierres "verdes"</h3>
    <ul style="color:var(--ink);">
      <li>Si responde TODO sin pega: <strong>haz que se comprometa a entregar las creds y la IP
      del SBC al final de la sesión</strong> (no "te lo mando esta semana").</li>
      <li>Si te dicen "podemos hacerlo desde ya": cierra el slot del <strong>viernes 30/05
      para T01-T03</strong>.</li>
      <li>Si el rango de numeración es generoso (50+ números): plantea expandir scope PoC con
      múltiples agentes desde el inicio.</li>
    </ul>

    <h3 style="margin-top:1.2rem;">Frase de cierre técnica</h3>
    <p style="color:var(--ink);">"Cuando tengamos los 13 parámetros del documento cerrados, el
    setup del trunk son ~2 horas de configuración por nuestro lado. El bottleneck son las creds
    y el número PoC — si nos los pasáis hoy o mañana, T01 podemos hacerlo el viernes."</p>
  </div>
</section>

</div>
</body>
</html>"""
