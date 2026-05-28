"""Página `/internal/brief-co` — briefs privados para Daniel.

NUNCA debe servirse sin auth admin. Contiene estrategia de negociación,
banderas rojas, cierres verdes, respuestas a preguntas críticas — material
que el partner NO debe ver.

Combina dos briefs en una sola página con navegación interna:
  · Brief pitch & plan PoC (estructura conversación, qué pedir).
  · Brief Troncal SIP (estructura min a min, pitfalls, frase de cierre).

Auth: la ruta `/internal/brief-co` usa check_kyc_admin_auth (Bearer,
cookie sesión panel KYC, o ?key=AMI_ADMIN_KEY). Si fall, 401.
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
      radial-gradient(ellipse 60% 40% at 85% 15%, rgba(255,107,138,0.06), transparent 70%),
      radial-gradient(ellipse 70% 50% at 15% 70%, rgba(139,108,255,0.10), transparent 70%),
      var(--bg);
    background-attachment: fixed; min-height: 100vh;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 2.5rem 1.5rem; }

  .danger-band {
    background: rgba(255,107,138,0.10);
    border: 1px solid rgba(255,107,138,0.35);
    border-radius: 10px;
    padding: 0.9rem 1.2rem;
    color: var(--red);
    font-family: var(--mono);
    font-size: 0.82rem;
    margin-bottom: 2rem;
    display: flex; justify-content: space-between; align-items: center;
    gap: 1rem; flex-wrap: wrap;
  }
  .danger-band a { color: var(--red); text-decoration: underline; }

  .doc-head { padding-bottom: 1.5rem; border-bottom: 1px solid var(--line); margin-bottom: 2rem; }
  .doc-head .eyebrow { font-family: var(--mono); font-size: 0.7rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em; margin-bottom: 0.6rem; }
  .doc-head h1 { font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 0.6rem; line-height: 1.15; }
  .doc-head .meta { display: flex; gap: 2rem; flex-wrap: wrap; font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute); }
  .doc-head .meta b { color: var(--ink); font-weight: 500; }

  nav.toc { background: var(--bg-soft); border: 1px solid var(--line); border-radius: 10px; padding: 1rem 1.4rem; margin-bottom: 2rem; }
  nav.toc h4 { margin: 0 0 0.6rem; font-size: 0.72rem; color: var(--ink-mute); font-family: var(--mono); text-transform: uppercase; letter-spacing: 0.14em; }
  nav.toc ul { margin: 0; padding-left: 1rem; line-height: 1.7; }
  nav.toc a { color: var(--accent-2); text-decoration: none; }
  nav.toc a:hover { color: #b9e6ff; }

  section { padding: 2rem 0; border-bottom: 1px solid var(--line); }
  section:last-of-type { border-bottom: 0; }
  h2 { font-size: 1.4rem; font-weight: 600; letter-spacing: -0.01em; margin: 0 0 1rem; }
  h2 .num { color: var(--ink-mute); font-family: var(--mono); font-size: 0.9rem; margin-right: 0.6rem; font-weight: 600; }
  h3 { font-size: 1.05rem; font-weight: 600; margin: 1.4rem 0 0.5rem; letter-spacing: -0.01em; color: var(--accent); }

  .brief { background: linear-gradient(180deg, rgba(139,108,255,0.06), rgba(93,209,255,0.04)); border: 1px solid rgba(139,108,255,0.25); border-radius: 12px; padding: 1.4rem 1.6rem; }
  .brief h3 { margin-top: 0; }
  .brief ol, .brief ul { color: var(--ink); line-height: 1.7; padding-left: 1.2rem; }
  .brief ol code, .brief ul code { font-family: var(--mono); background: rgba(0,0,0,0.3); padding: 0.1rem 0.4rem; border-radius: 4px; color: var(--accent-2); font-size: 0.86em; }
  .brief p { color: var(--ink); line-height: 1.6; }
  .brief strong { color: var(--ink); }

  p { color: var(--ink-soft); line-height: 1.6; font-size: 0.95rem; }
"""


def render_internal_brief_page() -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>AMI · Brief privado Daniel · NO compartir</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">

<div class="danger-band">
  <span>⚠ DOCUMENTO INTERNO — estrategia de negociación. NO compartir pantalla.</span>
  <span>Páginas públicas: <a href="/poc-co">/poc-co</a> · <a href="/poc-co/sip">/poc-co/sip</a></span>
</div>

<div class="doc-head">
  <div class="eyebrow">brief privado · solo Daniel</div>
  <h1>Brief de la reunión PoC Colombia</h1>
  <div class="meta">
    <span><b>Sesión:</b> 2026-05-28 · 16:30 — Revisión Troncal SIP</span>
    <span><b>Asistentes:</b> Julián · Javier Cruz (arq. IA) · Daniel</span>
  </div>
</div>

<nav class="toc">
  <h4>Contenido</h4>
  <ul>
    <li><a href="#pitch">Brief pitch & plan PoC</a> — pitch 30s, respuestas críticas, qué pedir a Javier</li>
    <li><a href="#sip">Brief Troncal SIP</a> — estructura min a min, banderas rojas, frase de cierre</li>
  </ul>
</nav>

<!-- BRIEF PITCH & PoC -->
<section id="pitch">
  <h2><span class="num">A</span>Brief pitch & plan PoC</h2>

  <div class="brief">
    <h3>Línea de pitch en 30 segundos</h3>
    <p>"AMI es el protocolo abierto que permite que un agente AI obtenga
    su propio número móvil real y opere SMS y voz de forma autónoma. Toda la pieza de
    contratación es real — KYC, firma, alta en sistemas del operador. Lo que sustituimos en cada
    país es solo el partner telco. En Colombia queremos hacer una PoC contigo en tres semanas
    para validar SMS bidireccional, voz bidireccional y un caso de uso típico: agente AI
    recibiendo OTP."</p>

    <h3>Preguntas críticas y respuestas listas</h3>
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

    <h3>Qué pedir TÚ a Javier Cruz</h3>
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

<!-- BRIEF TRONCAL SIP -->
<section id="sip">
  <h2><span class="num">B</span>Brief Troncal SIP</h2>

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

    <h3>Banderas rojas (cosas que pueden retrasarte)</h3>
    <ul>
      <li>Si te dicen "necesitamos un PoP en Colombia" — significa que su SBC no admite peering
      internacional. Sale más caro (VPS en CO) y tarda más (proveedor local). Pregunta si pueden
      hacer una excepción durante PoC.</li>
      <li>Si exigen TLS + SRTP + mutual auth desde día 1 — añade 3-5 días de setup. Empuja a
      "lo activamos en fase 2 producción, PoC va en UDP plano".</li>
      <li>Si el código SIP/RTP de su SBC es "propietario" (no estándar Asterisk-friendly) —
      pedir captura PCAP de un ejemplo para entender qué dialect hablan.</li>
    </ul>

    <h3>Cierres "verdes"</h3>
    <ul>
      <li>Si responde TODO sin pega: <strong>haz que se comprometa a entregar las creds y la IP
      del SBC al final de la sesión</strong> (no "te lo mando esta semana").</li>
      <li>Si te dicen "podemos hacerlo desde ya": cierra el slot del <strong>viernes 30/05
      para T01-T03</strong>.</li>
      <li>Si el rango de numeración es generoso (50+ números): plantea expandir scope PoC con
      múltiples agentes desde el inicio.</li>
    </ul>

    <h3>Frase de cierre técnica</h3>
    <p>"Cuando tengamos los 13 parámetros del documento cerrados, el setup del trunk son
    ~2 horas de configuración por nuestro lado. El bottleneck son las creds y el número PoC
    — si nos los pasáis hoy o mañana, T01 podemos hacerlo el viernes."</p>
  </div>
</section>

</div>
</body>
</html>"""
