"""Página `/internal/vps-co` — runbook para provisionar el VPS con
Asterisk + Kannel y entregar la IP fija al partner CO.

PRIVADO: requiere admin auth. No exponer públicamente (revela infra
interna + valores de env).

Pensado para Daniel en mitad de la reunión: pasos numerados, comandos
copy-paste, decisiones tomadas, troubleshooting al pie.
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
    background: radial-gradient(ellipse 60% 40% at 85% 15%, rgba(93,209,255,0.06), transparent 70%),
                radial-gradient(ellipse 70% 50% at 15% 70%, rgba(139,108,255,0.08), transparent 70%), var(--bg);
    background-attachment: fixed; min-height: 100vh;
  }
  .wrap { max-width: 920px; margin: 0 auto; padding: 2rem 1.5rem; }

  .danger-band {
    background: rgba(255,107,138,0.10); border: 1px solid rgba(255,107,138,0.35);
    border-radius: 10px; padding: 0.8rem 1.2rem; color: var(--red);
    font-family: var(--mono); font-size: 0.82rem; margin-bottom: 1.5rem;
  }

  .doc-head { padding-bottom: 1.2rem; border-bottom: 1px solid var(--line); margin-bottom: 1.6rem; }
  .doc-head .eyebrow { font-family: var(--mono); font-size: 0.7rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em; margin-bottom: 0.5rem; }
  .doc-head h1 { font-size: 1.8rem; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 0.4rem; line-height: 1.15; }
  .doc-head .meta { font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute); }
  .doc-head .meta b { color: var(--ink); font-weight: 500; }

  .summary-card {
    background: var(--bg-soft); border: 1px solid var(--line); border-radius: 10px;
    padding: 1rem 1.2rem; margin-bottom: 1.6rem; display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem;
  }
  .summary-card .item { }
  .summary-card .item .k { font-family: var(--mono); font-size: 0.68rem; color: var(--ink-mute); text-transform: uppercase; letter-spacing: 0.12em; margin-bottom: 0.3rem; }
  .summary-card .item .v { color: var(--ink); font-weight: 600; font-size: 0.95rem; }
  .summary-card .item .v code { font-family: var(--mono); color: var(--accent-2); }

  .step {
    background: var(--bg-soft); border: 1px solid var(--line); border-radius: 12px;
    padding: 1.2rem 1.4rem; margin-bottom: 1rem;
  }
  .step h3 { margin: 0 0 0.5rem; font-size: 1.05rem; font-weight: 600; display: flex; align-items: center; gap: 0.6rem; }
  .step h3 .badge {
    background: linear-gradient(180deg, #9d80ff, #7a5cff); color: white;
    width: 1.9rem; height: 1.9rem; border-radius: 6px;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 0.9rem; flex-shrink: 0;
  }
  .step .why { color: var(--ink-soft); font-size: 0.9rem; line-height: 1.55; margin: 0.3rem 0 0.8rem; }
  .step p { color: var(--ink); margin: 0.5rem 0; line-height: 1.55; font-size: 0.93rem; }
  .step ul { color: var(--ink-soft); padding-left: 1.2rem; line-height: 1.65; margin: 0.4rem 0; font-size: 0.9rem; }
  .step strong { color: var(--ink); }

  pre.cmd {
    font-family: var(--mono); font-size: 0.8rem;
    background: #000; color: #cfd2dc; border: 1px solid var(--line-strong);
    border-radius: 8px; padding: 0.9rem 1rem; margin: 0.6rem 0;
    overflow-x: auto; line-height: 1.5; position: relative;
  }
  pre.cmd .c { color: var(--ink-mute); }
  pre.cmd .k { color: var(--accent); }
  pre.cmd .s { color: var(--accent-2); }
  pre.cmd .v { color: var(--green); }
  .copy-btn {
    position: absolute; top: 0.4rem; right: 0.5rem;
    background: var(--surface); color: var(--ink-soft); border: 1px solid var(--line-strong);
    border-radius: 6px; padding: 0.25rem 0.6rem; font-family: var(--mono); font-size: 0.7rem;
    cursor: pointer; text-transform: uppercase; letter-spacing: 0.1em;
  }
  .copy-btn:hover { background: var(--surface-2); color: var(--ink); border-color: var(--accent); }
  .copy-btn.copied { color: var(--green); border-color: var(--green); }

  .anchor {
    background: linear-gradient(180deg, rgba(139,108,255,0.08), rgba(93,209,255,0.04));
    border: 1px solid rgba(139,108,255,0.25); border-radius: 12px;
    padding: 1.2rem 1.4rem; margin-bottom: 1.6rem;
  }
  .anchor h2 { margin: 0 0 0.5rem; color: var(--accent); font-size: 1.1rem; }
  .anchor p { color: var(--ink); margin: 0; line-height: 1.55; font-size: 0.95rem; }

  details.trouble {
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 10px; padding: 0.9rem 1.2rem; margin-bottom: 0.6rem;
  }
  details.trouble summary { cursor: pointer; font-weight: 600; color: var(--ink); list-style: none; }
  details.trouble summary::-webkit-details-marker { display: none; }
  details.trouble summary:before { content: '+ '; color: var(--accent); font-family: var(--mono); font-weight: 700; margin-right: 0.4rem; }
  details.trouble[open] summary:before { content: '− '; }
  details.trouble .body { margin-top: 0.7rem; color: var(--ink-soft); font-size: 0.92rem; line-height: 1.6; }

  h2 { font-size: 1.3rem; font-weight: 600; margin: 2rem 0 0.8rem; letter-spacing: -0.01em; }

  .nav { font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute); margin-bottom: 1.2rem; }
  .nav a { color: var(--accent-2); text-decoration: none; margin-right: 1rem; }
  .nav a:before { content: '← '; }
"""

_JS = """
document.querySelectorAll('pre.cmd').forEach(function(pre){
  var btn = document.createElement('button');
  btn.className = 'copy-btn';
  btn.textContent = 'copiar';
  btn.onclick = function() {
    var txt = pre.cloneNode(true);
    txt.querySelectorAll('.copy-btn').forEach(function(b){ b.remove(); });
    navigator.clipboard.writeText(txt.textContent.trim()).then(function(){
      btn.textContent = 'copiado ✓'; btn.classList.add('copied');
      setTimeout(function(){ btn.textContent = 'copiar'; btn.classList.remove('copied'); }, 1500);
    });
  };
  pre.appendChild(btn);
});
"""


def render_vps_runbook_page() -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>AMI · VPS Runbook PoC Colombia</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">

<div class="nav">
  <a href="/internal/brief-co">brief de la reunión</a>
  <a href="/poc-co/sip">SIP spec</a>
</div>

<div class="danger-band">⚠ DOCUMENTO INTERNO — runbook operacional. NO compartir pantalla.</div>

<div class="doc-head">
  <div class="eyebrow">runbook · provisionar VPS para PoC Colombia</div>
  <h1>Levantar VPS con IP fija para entregar al partner CO</h1>
  <div class="meta">Objetivo: tener IP /32 confirmada para enviar a Javier Cruz <b>esta tarde / mañana</b>.</div>
</div>

<!-- DECISIONES TOMADAS -->
<div class="anchor">
  <h2>Decisiones tomadas (no las cambies salvo razón)</h2>
  <p>
    Proveedor: <strong>Hetzner Cloud</strong> · Plan: <strong>CX22</strong> (2 vCPU · 4GB RAM · 40GB SSD · 20TB tráfico · IPv4 incluida)
    · Coste: <strong>~4,51€/mes</strong> · Region: <strong>Falkenstein (DE)</strong> · OS: <strong>Ubuntu 24.04 LTS</strong>.
  </p>
  <p style="margin-top:0.6rem;">
    <em>¿Por qué Hetzner CX22?</em> IPv4 dedicada incluida (no addon de pago como en Render/Fly),
    snapshot/backup gratis, panel limpio, soporta UDP libremente (necesario RTP/SIP),
    latencia ~140-180ms a Colombia (suficiente para PSTN G.711). Si el partner pide PoP americano,
    cambia a Hetzner Cloud Ashburn (US) — misma instrucción, distinta region.
  </p>
</div>

<div class="summary-card">
  <div class="item"><div class="k">Coste estimado</div><div class="v">4,51 €/mes</div></div>
  <div class="item"><div class="k">Tiempo total setup</div><div class="v">~45-60 min</div></div>
  <div class="item"><div class="k">Lo que entregas</div><div class="v">1 IP <code>/32</code></div></div>
  <div class="item"><div class="k">Servicios que corren</div><div class="v">Kannel · Asterisk · ami_api</div></div>
</div>

<h2>Pasos</h2>

<!-- STEP 1 -->
<div class="step">
  <h3><span class="badge">1</span>Crear cuenta Hetzner Cloud (si no la tienes)</h3>
  <p class="why">Hetzner pide verificación KYC ligera (foto del DNI). Suele tardar 5-15 min en horario laboral. Si ya tienes cuenta, salta al paso 2.</p>
  <ul>
    <li>Web: <a href="https://accounts.hetzner.com/signUp" target="_blank">accounts.hetzner.com/signUp</a></li>
    <li>Sube DNI por foto. Aprueban en minutos en horario CET.</li>
    <li>Confirma email. Mete tarjeta o transferencia inicial (mínimo 10€).</li>
  </ul>
</div>

<!-- STEP 2 -->
<div class="step">
  <h3><span class="badge">2</span>Crear el server desde el dashboard</h3>
  <p class="why">Una sola pantalla. Decisiones ya tomadas — copias los valores.</p>
  <ul>
    <li>Console: <a href="https://console.hetzner.cloud" target="_blank">console.hetzner.cloud</a> → nuevo proyecto <code>ami-co-poc</code></li>
    <li>"Add Server":
      <ul>
        <li><strong>Location:</strong> Falkenstein (DE)</li>
        <li><strong>Image:</strong> Ubuntu 24.04 LTS</li>
        <li><strong>Type:</strong> Shared vCPU → <strong>CX22</strong></li>
        <li><strong>Networking:</strong> IPv4 ✓ &nbsp; IPv6 ✓</li>
        <li><strong>SSH Keys:</strong> sube tu llave pública (<code>~/.ssh/id_ed25519.pub</code>). Si no la tienes, créala en local con <code>ssh-keygen -t ed25519</code>.</li>
        <li><strong>Name:</strong> <code>ami-co-poc-01</code></li>
      </ul>
    </li>
    <li><strong>Anota la IPv4 que asigne</strong>. Esa es la IP que vas a entregar al partner CO.</li>
  </ul>
</div>

<!-- STEP 3 -->
<div class="step">
  <h3><span class="badge">3</span>SSH al server + bootstrap</h3>
  <p class="why">Conectarte y dejar docker + git instalados de un golpe. Sustituye <code>&lt;VPS_IP&gt;</code> por la IP que te dio Hetzner.</p>
<pre class="cmd"><span class="c"># En tu MacBook</span>
ssh root@&lt;VPS_IP&gt;

<span class="c"># Una vez dentro del server: docker, git, ufw, herramientas</span>
apt update &amp;&amp; apt upgrade -y
apt install -y docker.io docker-compose-v2 git ufw curl jq tcpdump
systemctl enable --now docker</pre>
</div>

<!-- STEP 4 -->
<div class="step">
  <h3><span class="badge">4</span>Firewall — abrir solo lo necesario</h3>
  <p class="why">Bloqueamos todo menos SSH, SIP (5060/5061), RTP (10000-10100), y los puertos admin de Kannel.</p>
<pre class="cmd"><span class="k">ufw</span> default deny incoming
<span class="k">ufw</span> default allow outgoing
<span class="k">ufw</span> allow 22/tcp                  <span class="c"># SSH</span>
<span class="k">ufw</span> allow 5060/udp                <span class="c"># SIP UDP</span>
<span class="k">ufw</span> allow 5060/tcp                <span class="c"># SIP TCP</span>
<span class="k">ufw</span> allow 5061/tcp                <span class="c"># SIP TLS</span>
<span class="k">ufw</span> allow 10000:10100/udp        <span class="c"># RTP</span>
<span class="k">ufw</span> allow 13000/tcp               <span class="c"># Kannel admin (limitar a tu IP luego)</span>
<span class="k">ufw</span> allow 13013/tcp               <span class="c"># Kannel sendsms HTTP</span>
<span class="k">ufw</span> allow 8088/tcp                <span class="c"># Asterisk ARI</span>
<span class="k">ufw</span> --force enable
<span class="k">ufw</span> status</pre>
</div>

<!-- STEP 5 -->
<div class="step">
  <h3><span class="badge">5</span>Clonar AMI + entrar a la carpeta infra</h3>
<pre class="cmd">cd /opt
git clone https://github.com/Gamino17/AMI.git ami
cd ami/infra
ls -la</pre>
  <p class="why">Verás <code>docker-compose.yml</code>, <code>asterisk/</code>, <code>kannel/</code>, <code>.env.example</code> (si existe). Si no hay <code>.env.example</code>, lo creamos en el siguiente paso.</p>
</div>

<!-- STEP 6 -->
<div class="step">
  <h3><span class="badge">6</span>Crear el archivo <code>.env</code> con la config del PoC</h3>
  <p class="why">Estos son los valores. Las creds del partner CO se rellenan tras la reunión — déjalos vacíos por ahora si aún no los tienes, el modo mock arranca igual.</p>
<pre class="cmd">cat &gt; .env &lt;&lt;'EOF'
<span class="c"># ====== AMI API (apunta al servicio Render) ======</span>
<span class="k">AMI_PUBLIC_URL</span>=https://protocolami.com
<span class="k">AMI_API_KEY</span>=&lt;poner aquí AMI_API_KEY que tienes en Render&gt;
<span class="k">AMI_TELCO_MODE</span>=live
<span class="k">AMI_TELCO_INBOUND_KEY</span>=&lt;poner aquí AMI_TELCO_INBOUND_KEY de Render&gt;

<span class="c"># ====== IP pública del propio VPS (esta máquina) ======</span>
<span class="k">EXTERNAL_IP</span>=&lt;tu IP Hetzner&gt;

<span class="c"># ====== Kannel · creds que te pase Javier Cruz ======</span>
<span class="k">SMPP_HOST</span>=&lt;smsc.partner.co&gt;
<span class="k">SMPP_PORT</span>=2775
<span class="k">SMPP_SYSTEM_ID</span>=&lt;system_id&gt;
<span class="k">SMPP_PASSWORD</span>=&lt;password&gt;
<span class="k">SMPP_SYSTEM_TYPE</span>=
<span class="k">SMPP_SOURCE_TON</span>=1
<span class="k">SMPP_SOURCE_NPI</span>=1
<span class="k">KANNEL_SENDSMS_USER</span>=ami
<span class="k">KANNEL_SENDSMS_PASSWORD</span>=$(openssl rand -hex 24)

<span class="c"># ====== Asterisk · trunk SIP del partner CO ======</span>
<span class="k">SIP_TRUNK_HOST</span>=&lt;sbc.partner.co&gt;
<span class="k">SIP_TRUNK_PORT</span>=5060
<span class="k">SIP_TRUNK_USERNAME</span>=&lt;sip_user&gt;
<span class="k">SIP_TRUNK_PASSWORD</span>=&lt;sip_password&gt;

<span class="c"># ====== ARI (Asterisk REST Interface) ======</span>
<span class="k">ARI_USERNAME</span>=ami
<span class="k">ARI_PASSWORD</span>=$(openssl rand -hex 24)
EOF
chmod 600 .env</pre>
  <p><strong>Acción manual</strong>: rellena <code>EXTERNAL_IP</code> con la IP que te dio Hetzner. El resto lo rellenas cuando Javier te pase las creds.</p>
</div>

<!-- STEP 7 -->
<div class="step">
  <h3><span class="badge">7</span>Levantar el stack</h3>
<pre class="cmd">cd /opt/ami/infra
docker compose up -d
docker compose ps
docker compose logs -f --tail=50</pre>
  <p>Debes ver 3 servicios <code>running</code>:
    <code>ami_api</code>, <code>kannel</code>, <code>asterisk</code>.
    Si alguno entra en <em>restarting</em>, va al troubleshooting al pie.</p>
</div>

<!-- STEP 8 -->
<div class="step">
  <h3><span class="badge">8</span>Smoke test interno (antes de entregar la IP)</h3>
  <p class="why">Verifica que los servicios respondan localmente. Si esto pasa, la IP es confiable para entregar.</p>
<pre class="cmd"><span class="c"># 1) AMI API responde</span>
curl -s http://127.0.0.1:8000/v1/health | jq

<span class="c"># 2) Kannel admin responde</span>
curl -s http://127.0.0.1:13000/status

<span class="c"># 3) Asterisk levantó y vio el trunk</span>
docker compose exec asterisk asterisk -rx 'pjsip show endpoints'
docker compose exec asterisk asterisk -rx 'pjsip show registrations'

<span class="c"># 4) Desde fuera, SIP OPTIONS al puerto público (responde 200/401)</span>
nc -uv &lt;VPS_IP&gt; 5060</pre>
</div>

<!-- STEP 9 -->
<div class="step">
  <h3><span class="badge">9</span>Confirmar la IP pública y entregarla</h3>
<pre class="cmd">curl -s ifconfig.me
<span class="c"># Debe coincidir con la IP que Hetzner te asignó.</span></pre>

  <p><strong>Mensaje listo para enviar a Javier Cruz</strong> (WhatsApp o email):</p>
<pre class="cmd">Hola Javier,

Confirmo IP de nuestro Asterisk para que la whitelisteéis en vuestro SBC:

  IP:        &lt;VPS_IP&gt;/32
  Provider:  Hetzner Cloud (Falkenstein, DE)
  SIP port:  5060 UDP/TCP, 5061 TLS
  RTP range: 10000-10100 UDP
  Hostname:  ami-co-poc-01.parallax-iei.com (DNS por configurar)

Confirmadme cuando esté hecho y cierro T01 (OPTIONS ping)
+ T02 (REGISTER si aplica) de la sesión.

Daniel</pre>
</div>

<!-- STEP 10 -->
<div class="step">
  <h3><span class="badge">10</span>Hardening básico antes de cerrar</h3>
<pre class="cmd"><span class="c"># Desactiva password SSH (solo key)</span>
sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh

<span class="c"># Restringe Kannel admin a tu IP (no a 0.0.0.0)</span>
<span class="c"># Editar /opt/ami/infra/kannel/kannel.conf si quieres limitar admin-allowed-ip</span>

<span class="c"># Persistencia logs Asterisk (rotación)</span>
mkdir -p /opt/ami/infra/logs
chmod 755 /opt/ami/infra/logs</pre>
</div>

<h2>Troubleshooting</h2>

<details class="trouble">
  <summary>Asterisk no levanta · "Unable to load module res_pjsip"</summary>
  <div class="body">Normalmente es por una macro <code>${{EXTERNAL_IP}}</code> sin valor en
  pjsip.conf. Verifica que en <code>.env</code> tienes <code>EXTERNAL_IP=&lt;IP&gt;</code>
  rellena con tu IP Hetzner y reinicia: <code>docker compose restart asterisk</code>.</div>
</details>

<details class="trouble">
  <summary>Asterisk levanta pero "pjsip show registrations" muestra Rejected/Unreachable</summary>
  <div class="body">Hay 3 causas típicas: (1) la IP del SBC del partner no es alcanzable
  desde Hetzner — pídeles que confirmen que ven nuestra IP en sus logs.
  (2) Credenciales mal — verifica <code>SIP_TRUNK_USERNAME/PASSWORD</code> contra lo
  que te pasó Javier. (3) Auth mode IP-based en su SBC y nuestra IP aún no whitelisteada.
  <code>tcpdump -i any port 5060</code> en el VPS te muestra los REGISTER salientes y
  las respuestas (401 = mal pass, sin respuesta = no llegamos a ellos).</div>
</details>

<details class="trouble">
  <summary>Kannel arranca pero no conecta SMPP</summary>
  <div class="body">Revisa <code>docker compose logs kannel</code>. Errores típicos:
  <code>bind transmitter failed</code> = system_id/password incorrectos;
  <code>connection refused</code> = host/puerto mal o firewall del partner no nos
  acepta; <code>bind type not supported</code> = pide <em>transceiver</em> en lugar de
  transmitter — cambiar en <code>kannel.conf</code>.</div>
</details>

<details class="trouble">
  <summary>Saliente SIP funciona, entrante no</summary>
  <div class="body">El SBC del partner tiene que reenviar el INVITE entrante a TU IP fija
  con el formato E.164 acordado. Si no llega ningún paquete UDP a 5060 (verifica con
  <code>tcpdump -i any -n udp port 5060</code>), el partner no está routing a nosotros
  — pídeles que confirmen la regla de inbound en su SBC.</div>
</details>

<details class="trouble">
  <summary>Audio una vía / sin audio</summary>
  <div class="body">99% de las veces es RTP bloqueado o NAT mal. Verifica
  <code>ufw status</code> incluye <code>10000:10100/udp ALLOW</code>. En Asterisk
  asegúrate de que <code>direct_media=no</code> (ya está en repo).
  <code>tcpdump -i any -n udp portrange 10000-10100</code> te muestra si llega
  RTP por ambas direcciones.</div>
</details>

<details class="trouble">
  <summary>Necesito cambiar a PoP americano para latencia</summary>
  <div class="body">Mismo runbook, cambia en el paso 2 la location de Falkenstein a
  Ashburn (US). El resto idéntico. Latencia esperada a Colombia: ~65-80ms.</div>
</details>

</div>

<script>{_JS}</script>
</body>
</html>"""
