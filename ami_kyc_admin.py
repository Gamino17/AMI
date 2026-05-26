"""Panel admin HTML para revisar verificaciones KYC pendientes.

Se sirve en GET /panel/kyc tras autenticarse. Tres mecanismos de auth:

  1. Cookie `ami-kyc-admin` (set por POST /panel/kyc/login) — preferido.
  2. Header Authorization: Bearer <AMI_ADMIN_KEY> — para curl/scripts.
  3. Query ?key=<AMI_ADMIN_KEY> — quick-access en demo.

UX: lista de KYCs por estado, click para ver imágenes en grande,
botones Verify / Reject que llaman a la API admin REST.
"""
from __future__ import annotations
import html as _html
import os
import secrets


COOKIE_NAME = "ami-kyc-admin"


def check_kyc_admin_cookie(handler, admin_key: str | None) -> bool:
    """True si la cookie de sesión coincide con el AMI_ADMIN_KEY."""
    if not admin_key:
        return False
    cookie = handler.headers.get("Cookie", "") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            try:
                return secrets.compare_digest(v, admin_key)
            except (TypeError, ValueError):
                return False
    return False


def render_login(error: str = "") -> str:
    err_html = f'<div class="err">{_html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Panel KYC · Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  body {{
    background: #06060a; color: #ededf2; font-family: "Inter", sans-serif;
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: radial-gradient(ellipse 80% 50% at 50% 0%, rgba(139,108,255,0.10), transparent 70%), #06060a;
  }}
  form {{
    width: 100%; max-width: 380px; padding: 2rem;
    background: #0c0c14; border: 1px solid #1f1f2c; border-radius: 14px;
  }}
  h1 {{ margin: 0 0 0.4rem; font-size: 1.4rem; letter-spacing: -0.02em; }}
  p.sub {{ color: #8888a0; margin: 0 0 1.6rem; font-size: 0.9rem; }}
  label {{
    display: block; font-family: "JetBrains Mono", monospace;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em;
    color: #5a5a70; margin-bottom: 0.4rem;
  }}
  input {{
    width: 100%; padding: 0.75rem; background: #14141d; color: #ededf2;
    border: 1px solid #1f1f2c; border-radius: 8px; font-family: inherit;
    font-size: 0.95rem; margin-bottom: 1.2rem;
  }}
  input:focus {{ outline: none; border-color: #8b6cff; }}
  button {{
    width: 100%; padding: 0.85rem; border: 0; cursor: pointer;
    background: linear-gradient(180deg,#9d80ff,#7a5cff); color: white;
    border-radius: 8px; font-size: 0.95rem; font-weight: 600;
  }}
  .err {{
    background: rgba(255,107,138,0.10); border: 1px solid rgba(255,107,138,0.3);
    color: #ff6b8a; padding: 0.6rem 0.8rem; border-radius: 6px; margin-bottom: 1rem;
    font-family: "JetBrains Mono", monospace; font-size: 0.82rem;
  }}
</style>
</head><body>
<form method="POST" action="/panel/kyc/login">
  <h1>Panel KYC</h1>
  <p class="sub">Acceso para revisores de identidad.</p>
  {err_html}
  <label>Tu nombre</label>
  <input type="text" name="reviewer" required autofocus placeholder="daniel">
  <label>Admin key</label>
  <input type="password" name="key" required>
  <button type="submit">Entrar</button>
</form>
</body></html>"""


def render_admin_kyc_list(kycs: list[dict]) -> str:
    """Renderiza la lista completa de KYCs con filtros por estado.

    `kycs` es la salida de `ami_kyc.kyc_summary(include_images=True)` para
    cada registro (necesitamos las imágenes para previsualización inline).
    """
    rows = []
    for k in kycs:
        status = k.get("status", "pending")
        rows.append(_render_card(k, status))
    body = "\n".join(rows) if rows else _empty_state()

    return _PAGE.replace("{{BODY}}", body).replace("{{COUNT}}", str(len(kycs)))


def _empty_state() -> str:
    return """
      <div class="empty">
        <div class="empty-icon">∅</div>
        <h3>Sin verificaciones pendientes</h3>
        <p>Cuando un agente AI dispare <code>kyc/initiate</code> aparecerá aquí.</p>
      </div>
    """


def _render_card(k: dict, status: str) -> str:
    kid = _html.escape(k.get("id", ""))
    sim = _html.escape(k.get("sim_request_id", ""))
    cust = _html.escape(k.get("customer_id", ""))
    email = _html.escape(k.get("rep_email", "—"))
    created = _html.escape((k.get("created_at") or "")[:19].replace("T", " "))
    reviewer = _html.escape(k.get("reviewer") or "—")
    reason = _html.escape(k.get("rejection_reason") or "")

    front = k.get("dni_front_b64") or ""
    back  = k.get("dni_back_b64") or ""
    selfie = k.get("selfie_b64") or ""

    imgs_html = ""
    for label, b64 in (("DNI frontal", front), ("DNI reverso", back), ("Selfie", selfie)):
        if b64:
            src = b64 if b64.startswith("data:") else ("data:image/jpeg;base64," + b64)
            imgs_html += f"""
              <figure class="img">
                <img src="{_html.escape(src)}" alt="{label}">
                <figcaption>{label}</figcaption>
              </figure>
            """
        else:
            imgs_html += f"""
              <figure class="img empty">
                <div class="img-placeholder">sin imagen</div>
                <figcaption>{label}</figcaption>
              </figure>
            """

    actions = ""
    if status == "submitted":
        actions = f"""
          <div class="actions">
            <button class="btn primary" onclick="verifyKyc('{kid}')">✓ Verificar</button>
            <button class="btn danger" onclick="rejectKyc('{kid}')">✗ Rechazar</button>
          </div>
        """
    elif status == "verified":
        actions = f'<div class="meta">Verificado por <strong>{reviewer}</strong></div>'
    elif status == "rejected":
        actions = f'<div class="meta">Rechazado por <strong>{reviewer}</strong>{(": " + reason) if reason else ""}</div>'
    else:
        actions = '<div class="meta">Esperando que el humano suba el DNI…</div>'

    pill = f'<span class="pill {status}">{status}</span>'
    return f"""
      <article class="card" data-status="{status}">
        <header>
          <div>
            <div class="kid">{kid}</div>
            <div class="meta">creado {created} · sim_request {sim}</div>
          </div>
          {pill}
        </header>
        <div class="who">
          <div><span class="k">rep:</span> {email}</div>
          <div><span class="k">customer:</span> <code>{cust}</code></div>
        </div>
        <div class="imgs">{imgs_html}</div>
        {actions}
      </article>
    """


_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Panel KYC</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #06060a; --bg-soft: #0c0c14; --surface: #14141d;
    --line: #1f1f2c; --ink: #ededf2; --ink-soft: #8888a0; --ink-mute: #5a5a70;
    --accent: #8b6cff; --green: #4ade80; --amber: #fbbf24; --red: #ff6b8a;
    --sans: "Inter", -apple-system, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); }
  .top {
    border-bottom: 1px solid var(--line); padding: 1.2rem 2rem;
    display: flex; justify-content: space-between; align-items: center;
    background: var(--bg-soft);
  }
  .top h1 { margin: 0; font-size: 1.1rem; letter-spacing: -0.01em; }
  .top h1 small { color: var(--ink-mute); font-weight: 400; font-family: var(--mono); font-size: 0.8rem; margin-left: 0.6rem; }
  .filters { display: flex; gap: 0.5rem; }
  .filter {
    font-family: var(--mono); font-size: 0.72rem; padding: 0.4rem 0.8rem;
    background: transparent; color: var(--ink-soft);
    border: 1px solid var(--line); border-radius: 99px; cursor: pointer;
    text-transform: uppercase; letter-spacing: 0.1em;
  }
  .filter.active { color: var(--accent); border-color: var(--accent); }
  main { padding: 2rem; max-width: 1200px; margin: 0 auto; display: grid; gap: 1.5rem; }
  .empty { text-align: center; padding: 4rem 1rem; color: var(--ink-soft); }
  .empty-icon { font-size: 3rem; color: var(--ink-mute); margin-bottom: 1rem; }
  .empty h3 { margin: 0 0 0.5rem; color: var(--ink); }
  .empty code { font-family: var(--mono); background: var(--surface); padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.85em; }
  .card {
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.4rem;
  }
  .card header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }
  .kid { font-family: var(--mono); font-size: 0.95rem; font-weight: 600; }
  .meta { font-family: var(--mono); font-size: 0.75rem; color: var(--ink-mute); margin-top: 0.2rem; }
  .pill {
    font-family: var(--mono); font-size: 0.68rem; padding: 0.2rem 0.7rem;
    border-radius: 99px; text-transform: uppercase; letter-spacing: 0.1em;
  }
  .pill.pending   { color: var(--ink-mute); background: rgba(136,136,160,0.10); }
  .pill.submitted { color: var(--amber);   background: rgba(251,191,36,0.10); }
  .pill.verified  { color: var(--green);   background: rgba(74,222,128,0.10); }
  .pill.rejected  { color: var(--red);     background: rgba(255,107,138,0.10); }
  .who { display: flex; gap: 2rem; font-size: 0.88rem; color: var(--ink-soft); margin-bottom: 1rem; }
  .who .k { color: var(--ink-mute); margin-right: 0.4rem; }
  .who code { font-family: var(--mono); font-size: 0.82em; color: var(--ink); }
  .imgs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 1rem; }
  .img { margin: 0; }
  .img img {
    width: 100%; aspect-ratio: 1.586/1; object-fit: cover;
    border-radius: 8px; border: 1px solid var(--line);
    cursor: zoom-in; transition: transform 0.15s;
  }
  .img img:hover { transform: scale(1.02); }
  .img-placeholder {
    width: 100%; aspect-ratio: 1.586/1; border: 1px dashed var(--line);
    border-radius: 8px; display: flex; align-items: center; justify-content: center;
    color: var(--ink-mute); font-family: var(--mono); font-size: 0.78rem;
  }
  .img figcaption { font-family: var(--mono); font-size: 0.72rem; color: var(--ink-mute); margin-top: 0.4rem; text-transform: uppercase; letter-spacing: 0.08em; }
  .actions { display: flex; gap: 0.7rem; }
  .btn {
    flex: 1; padding: 0.7rem; border: 0; border-radius: 8px; cursor: pointer;
    font-family: var(--sans); font-size: 0.9rem; font-weight: 600;
  }
  .btn.primary { background: linear-gradient(180deg, #5dd97a, #3bb55c); color: #0a0a10; }
  .btn.danger  { background: rgba(255,107,138,0.15); color: var(--red); border: 1px solid rgba(255,107,138,0.3); }
  .lightbox {
    position: fixed; inset: 0; background: rgba(0,0,0,0.92); display: none;
    align-items: center; justify-content: center; z-index: 1000; cursor: zoom-out;
  }
  .lightbox.show { display: flex; }
  .lightbox img { max-width: 92vw; max-height: 92vh; }
</style>
</head>
<body>
  <div class="top">
    <h1>Panel KYC <small>({{COUNT}} registros)</small></h1>
    <div class="filters">
      <button class="filter active" data-filter="all">Todos</button>
      <button class="filter" data-filter="submitted">Pendientes</button>
      <button class="filter" data-filter="verified">Verificados</button>
      <button class="filter" data-filter="rejected">Rechazados</button>
      <button class="filter" data-filter="expired">Caducados</button>
      <form method="POST" action="/panel/kyc/logout" style="display:inline;margin-left:0.5rem;">
        <button class="filter" type="submit" title="Salir">salir</button>
      </form>
    </div>
  </div>
  <main id="grid">{{BODY}}</main>
  <div class="lightbox" id="lightbox" onclick="this.classList.remove('show')"><img id="lightboxImg"></div>

<script>
// Lee la cookie ami-kyc-reviewer (no httpOnly readable desde JS sería el patrón
// normal; aquí está como httpOnly. Como fallback pedimos por prompt UNA vez y
// lo memorizamos en sessionStorage para no molestar al revisor en cada acción).
function reviewerName() {
  var n = sessionStorage.getItem('ami-kyc-reviewer');
  if (!n) {
    n = prompt('Tu nombre (queda en esta sesión):') || 'admin';
    sessionStorage.setItem('ami-kyc-reviewer', n);
  }
  return n;
}

// Lee el CSRF token (cookie NO-HttpOnly) y lo manda como header
// X-CSRF-Token. Patrón double-submit-cookie estándar.
function csrfToken() {
  var re = new RegExp('(?:^|;\\\\s*)ami-kyc-csrf=([^;]+)');
  var match = document.cookie.match(re);
  return match ? decodeURIComponent(match[1]) : '';
}

async function verifyKyc(id) {
  if (!confirm('¿Verificar KYC ' + id + '?')) return;
  var r = await fetch('/v1/admin/kyc/' + id + '/verify', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
    body: JSON.stringify({ reviewer: reviewerName() }),
  });
  if (r.ok) location.reload();
  else alert('Error: ' + r.status + ' ' + await r.text());
}

async function rejectKyc(id) {
  var reason = prompt('Motivo del rechazo:');
  if (!reason) return;
  var r = await fetch('/v1/admin/kyc/' + id + '/reject', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
    body: JSON.stringify({ reason: reason, reviewer: reviewerName() }),
  });
  if (r.ok) location.reload();
  else alert('Error: ' + r.status + ' ' + await r.text());
}

// Filtros
document.querySelectorAll('.filter').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.filter').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var f = btn.dataset.filter;
    document.querySelectorAll('.card').forEach(function(c) {
      c.style.display = (f === 'all' || c.dataset.status === f) ? '' : 'none';
    });
  });
});

// Lightbox
document.querySelectorAll('.img img').forEach(function(img) {
  img.addEventListener('click', function() {
    document.getElementById('lightboxImg').src = img.src;
    document.getElementById('lightbox').classList.add('show');
  });
});
</script>
</body>
</html>"""
