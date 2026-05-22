"""Panel del cliente — dashboard server-rendered con la API key como auth.

Las rutas REST de AMI ya cubren todo lo necesario por MCP/HTTP, pero un
customer humano que llega al producto necesita una pantalla amable que le
diga: cuántos números tiene activos, cuánto está gastando, quién está
recibiendo SMS y llamadas, qué webhooks tiene, y cómo rotar tokens.

Single-tenant v1: hay una única AMI_API_KEY del customer y todos los MIDs
pertenecen a ese customer. El panel autentica con esa misma key vía form
de login, persiste cookie httpOnly, y server-renderea las páginas leyendo
directamente del STATE in-memory (sin hacer HTTP a sí mismo).

Páginas:
  GET  /panel/login            — formulario de auth
  POST /panel/login            — valida → set-cookie + redirect /panel
  POST /panel/logout           — borra cookie + redirect /panel/login
  GET  /panel                  — lista de MIDs + actividad reciente
  GET  /panel/mid/{mid}        — detalle del MID
"""
from __future__ import annotations
import html as _html
import secrets


def _esc(s) -> str:
    return _html.escape(str(s)) if s is not None else ""


def check_panel_cookie(handler):
    """Devuelve el account_id si la cookie ami-panel-token resuelve a un
    customer-account activo; None si no. La cookie contiene la API key
    plain del customer; comparamos su hash contra customer_accounts.

    Esto reemplaza la versión single-tenant que devolvía bool. Mantiene
    backwards-compat con la legacy AMI_API_KEY porque _bootstrap_default_customer
    crea cust_default con esa key al arrancar."""
    import ami_api
    cookie = handler.headers.get("Cookie", "") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "ami-panel-token" and v:
            cust = ami_api._customer_by_api_key(v)
            if cust:
                return cust["id"]
    return None


# ===================== TEMPLATING =====================

_BASE_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --surface-hover: #1a1a25;
    --line: #1f1f2c;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --green: #4ade80;
    --amber: #fbbf24;
    --red: #ff6b8a;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans); color: var(--ink); background: var(--bg);
    line-height: 1.5; -webkit-font-smoothing: antialiased;
  }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { color: #b9e6ff; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 1.5rem 1.75rem; }
  .pn-header {
    border-bottom: 1px solid var(--line);
    background: rgba(6,6,10,0.85);
    backdrop-filter: blur(10px);
  }
  .pn-header .wrap {
    display: flex; align-items: center; justify-content: space-between;
    padding-top: 1rem; padding-bottom: 1rem;
  }
  .pn-brand {
    font-family: var(--mono); font-weight: 700; font-size: 1rem;
    letter-spacing: 0.02em; color: var(--ink); display: flex; align-items: center; gap: 0.5rem;
  }
  .pn-brand .dot { color: var(--accent); }
  .pn-nav { display: flex; gap: 1rem; align-items: center; font-family: var(--mono); font-size: 0.8rem; }
  .pn-nav a, .pn-logout { color: var(--ink-soft); }
  .pn-nav a:hover, .pn-logout:hover { color: var(--ink); }
  .pn-logout {
    background: none; border: 0; cursor: pointer; padding: 0; font-family: var(--mono); font-size: 0.8rem;
  }
  h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 0.4rem; }
  h2 { font-size: 1rem; font-weight: 600; letter-spacing: -0.01em; margin: 2rem 0 0.8rem;
       text-transform: uppercase; color: var(--ink-soft); font-family: var(--mono);
       font-size: 0.78rem; letter-spacing: 0.15em; }
  .pn-subtitle { color: var(--ink-soft); font-family: var(--mono); font-size: 0.82rem; margin: 0 0 1.4rem; }

  /* Cards / tables */
  table.pn-table { width: 100%; border-collapse: collapse; }
  table.pn-table th, table.pn-table td {
    text-align: left; padding: 0.7rem 0.8rem;
    border-bottom: 1px solid var(--line);
    font-size: 0.85rem;
  }
  table.pn-table th {
    font-family: var(--mono); font-size: 0.7rem; text-transform: uppercase;
    letter-spacing: 0.12em; color: var(--ink-mute); font-weight: 600;
  }
  table.pn-table tbody tr:hover { background: var(--surface); }
  td.mono, .mono { font-family: var(--mono); font-size: 0.82rem; color: var(--ink-soft); }
  .pill {
    display: inline-block; font-family: var(--mono); font-size: 0.68rem;
    padding: 0.15rem 0.55rem; border-radius: 99px; letter-spacing: 0.06em;
    border: 1px solid var(--line);
  }
  .pill.active { color: var(--green); border-color: rgba(74,222,128,0.4); background: rgba(74,222,128,0.08); }
  .pill.suspended, .pill.failed, .pill.cancelled { color: var(--red); border-color: rgba(255,107,138,0.4); background: rgba(255,107,138,0.08); }
  .pill.delivered, .pill.completed, .pill.received { color: var(--ink-soft); }
  .pill.queued, .pill.sent, .pill.initiated, .pill.ringing, .pill.in_progress {
    color: var(--accent-2); border-color: rgba(93,209,255,0.4); background: rgba(93,209,255,0.08);
  }

  .stat-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1rem; margin: 1rem 0 0;
  }
  .stat-card {
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 8px; padding: 1rem 1.1rem;
  }
  .stat-card .label {
    font-family: var(--mono); font-size: 0.68rem; text-transform: uppercase;
    letter-spacing: 0.14em; color: var(--ink-mute); margin-bottom: 0.4rem;
  }
  .stat-card .value {
    font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em;
    font-family: var(--mono); color: var(--ink);
  }
  .stat-card .sub { font-family: var(--mono); font-size: 0.72rem; color: var(--ink-soft); margin-top: 0.3rem; }
  .progress {
    margin-top: 0.5rem; height: 4px; background: var(--surface); border-radius: 2px; overflow: hidden;
  }
  .progress > i {
    display: block; height: 100%;
    background: linear-gradient(90deg, var(--accent-2), var(--accent));
  }
  .empty {
    color: var(--ink-mute); font-family: var(--mono); font-size: 0.82rem;
    padding: 1.5rem; text-align: center; border: 1px dashed var(--line); border-radius: 8px;
  }
"""

_LOGIN_CSS = _BASE_CSS + """
  body { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .login-box {
    width: 100%; max-width: 380px; padding: 2.5rem 2rem;
    background: var(--bg-soft); border: 1px solid var(--line); border-radius: 12px;
  }
  .login-box h1 { text-align: center; }
  .login-box .pn-subtitle { text-align: center; margin-bottom: 2rem; }
  .login-box label {
    display: block; font-family: var(--mono); font-size: 0.7rem;
    text-transform: uppercase; letter-spacing: 0.14em; color: var(--ink-mute);
    margin-bottom: 0.5rem;
  }
  .login-box input[type="password"] {
    width: 100%; background: var(--surface); color: var(--ink);
    border: 1px solid var(--line); border-radius: 6px;
    padding: 0.7rem 0.9rem; font-family: var(--mono); font-size: 0.85rem;
  }
  .login-box input[type="password"]:focus { outline: none; border-color: var(--accent); }
  .login-box button {
    width: 100%; margin-top: 1.2rem;
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; border: 0; cursor: pointer;
    padding: 0.75rem; border-radius: 6px; font-weight: 500;
  }
  .login-box .err {
    color: var(--red); font-family: var(--mono); font-size: 0.78rem;
    text-align: center; margin-bottom: 1rem;
  }
"""


def render_login(error: str = "") -> str:
    err_block = f'<div class="err">{_esc(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI Panel · Sign in</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_LOGIN_CSS}</style>
</head>
<body>
  <div class="login-box">
    <h1>AMI<span style="color:var(--accent)">.</span></h1>
    <p class="pn-subtitle">Customer panel</p>
    {err_block}
    <form method="POST" action="/panel/login">
      <label for="k">API key</label>
      <input id="k" type="password" name="key" autocomplete="off" required autofocus />
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>"""


def _pill(text: str, kind: str = "") -> str:
    cls = f"pill {_esc(kind)}".strip()
    return f'<span class="{cls}">{_esc(text)}</span>'


def _progress_bar(consumed_pct) -> str:
    if consumed_pct is None:
        return ""
    pct = max(0, min(100, float(consumed_pct)))
    return f'<div class="progress"><i style="width:{pct:.1f}%"></i></div>'


def render_home(account_id: str) -> str:
    """Dashboard principal del customer-account: lista MIDs + actividad +
    spend, **filtrado estricto por account_id**. Un customer NO ve los MIDs
    de otro NI los legacy/demo sin owner (estos se atribuyen a un sentinel
    aparte; el panel solo muestra los que son explícitamente del account)."""
    import ami_api, ami_limits
    mids = [m for m in ami_api.STATE["mobile_identities"].values()
            if m.get("account_id") == account_id]
    mids.sort(key=lambda i: i.get("activated_at") or "", reverse=True)
    mid_ids = {m["id"] for m in mids}

    # Stats agregadas
    total_spend = 0.0
    total_sms_today = 0
    total_calls_today = 0
    for m in mids:
        snap = ami_limits.usage_snapshot(m)
        total_spend += snap["usage"]["spend_this_month_eur"]
        total_sms_today += snap["usage"]["sms_count_today"]
        total_calls_today += snap["usage"]["calls_count_today"]

    rows = []
    if not mids:
        rows.append('<tr><td colspan="5"><div class="empty">No numbers yet. Provision one via POST /v1/sim-requests.</div></td></tr>')
    for m in mids:
        snap = ami_limits.usage_snapshot(m)
        spend = snap["usage"]["spend_this_month_eur"]
        budget = snap["limits"]["monthly_budget_eur"]
        pct = snap["consumed_pct"]["monthly_budget"]
        rows.append(f"""
          <tr>
            <td class="mono"><a href="/panel/mid/{_esc(m['id'])}">{_esc(m.get('phone_number','-'))}</a></td>
            <td>{_pill(m.get('status','-'), m.get('status',''))}</td>
            <td class="mono">{_esc(m['id'])}</td>
            <td class="mono">€{spend:.2f} / €{budget:.0f}{_progress_bar(pct)}</td>
            <td class="mono">{_esc(m.get('activated_at','-'))[:19].replace('T',' ')}</td>
          </tr>
        """)

    # Audit log filtrado: solo eventos que tocan MIDs del account, más eventos
    # cuyo entity_id es uno de esos MIDs o cualquier sms/call asociado.
    def _own(ev):
        if ev.get("entity_type") == "mobile_identity":
            return ev.get("entity_id") in mid_ids
        if ev.get("entity_type") in ("sms_message", "call"):
            data = ev.get("data") or {}
            return data.get("mid") in mid_ids
        return False
    events = [e for e in ami_api.STATE["events"] if _own(e)][-15:][::-1]
    if events:
        ev_rows = "".join(
            f"<tr><td class='mono'>{_esc(e['at'][:19].replace('T',' '))}</td>"
            f"<td class='mono'>{_esc(e['action'])}</td>"
            f"<td class='mono'>{_esc(e.get('entity_type','-'))}</td>"
            f"<td class='mono'>{_esc(e.get('entity_id','-'))}</td></tr>"
            for e in events
        )
        events_block = f'<table class="pn-table"><thead><tr><th>When</th><th>Action</th><th>Entity</th><th>Id</th></tr></thead><tbody>{ev_rows}</tbody></table>'
    else:
        events_block = '<div class="empty">No activity yet.</div>'

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI Panel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_BASE_CSS}</style>
</head>
<body>
  <header class="pn-header">
    <div class="wrap">
      <a class="pn-brand" href="/panel">AMI<span class="dot">.</span> Panel</a>
      <nav class="pn-nav">
        <a href="/">Public site</a>
        <a href="/spec">Spec</a>
        <form method="POST" action="/panel/logout" style="display:inline">
          <button class="pn-logout" type="submit">Sign out</button>
        </form>
      </nav>
    </div>
  </header>

  <main class="wrap">
    <h1>Overview</h1>
    <p class="pn-subtitle">{_esc(len(mids))} number{'s' if len(mids) != 1 else ''} · €{total_spend:.2f} this month · {_esc(total_sms_today)} SMS / {_esc(total_calls_today)} calls today</p>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="label">Numbers active</div>
        <div class="value">{sum(1 for m in mids if m.get('status') == 'active')}</div>
        <div class="sub">of {len(mids)} total</div>
      </div>
      <div class="stat-card">
        <div class="label">Spend this month</div>
        <div class="value">€{total_spend:.2f}</div>
        <div class="sub">across all numbers</div>
      </div>
      <div class="stat-card">
        <div class="label">SMS today</div>
        <div class="value">{total_sms_today}</div>
        <div class="sub">across all numbers</div>
      </div>
      <div class="stat-card">
        <div class="label">Calls today</div>
        <div class="value">{total_calls_today}</div>
        <div class="sub">across all numbers</div>
      </div>
    </div>

    <h2>Numbers</h2>
    <table class="pn-table">
      <thead><tr><th>Number</th><th>Status</th><th>MID</th><th>Spend this month</th><th>Activated</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>

    <h2>Recent activity</h2>
    {events_block}
  </main>
</body>
</html>"""


def render_mid_detail(mid: str, account_id: str) -> tuple[int, str]:
    """Detalle de un MID, scoped al account del request. Si el MID no
    pertenece a este account, devolvemos 404 (no 403: no filtramos la
    existencia del id a customers no dueños)."""
    import ami_api, ami_limits, ami_webhooks
    identity = ami_api.STATE["mobile_identities"].get(mid)
    if not identity:
        return 404, _render_not_found()
    # Filter estricto: solo el dueño explícito. Legacy/demo MIDs sin owner
    # NO se muestran a ningún customer.
    if identity.get("account_id") != account_id:
        return 404, _render_not_found()

    snap = ami_limits.usage_snapshot(identity)
    sms_msgs = sorted(
        [m for m in ami_api.STATE["sms_messages"].values() if m["mid"] == mid],
        key=lambda x: x["created_at"], reverse=True,
    )[:20]
    calls = sorted(
        [c for c in ami_api.STATE["calls"].values() if c["mid"] == mid],
        key=lambda x: x["created_at"], reverse=True,
    )[:20]
    webhooks = [w for w in ami_api.STATE["webhooks"].values() if w["mid"] == mid]

    def _stat(label, value, sub="", pct=None):
        return f"""
          <div class="stat-card">
            <div class="label">{_esc(label)}</div>
            <div class="value">{_esc(value)}</div>
            <div class="sub">{_esc(sub)}</div>
            {_progress_bar(pct)}
          </div>"""

    sms_rows = "".join(
        f"<tr><td class='mono'>{_esc(m['created_at'][:19].replace('T',' '))}</td>"
        f"<td>{_pill(m['direction'])}</td>"
        f"<td class='mono'>{_esc(m['from'] if m['direction']=='inbound' else m['to'])}</td>"
        f"<td class='mono' style='max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{_esc(m['body'])}</td>"
        f"<td>{_pill(m['status'], m['status'])}</td></tr>"
        for m in sms_msgs
    ) or "<tr><td colspan='5'><div class='empty'>No SMS yet.</div></td></tr>"

    call_rows = "".join(
        f"<tr><td class='mono'>{_esc(c['created_at'][:19].replace('T',' '))}</td>"
        f"<td>{_pill(c['direction'])}</td>"
        f"<td class='mono'>{_esc(c['from'] if c['direction']=='inbound' else c['to'])}</td>"
        f"<td>{_pill(c['status'], c['status'])}</td>"
        f"<td class='mono'>{_esc(c.get('duration_sec') or '-')}</td></tr>"
        for c in calls
    ) or "<tr><td colspan='5'><div class='empty'>No calls yet.</div></td></tr>"

    wh_rows = "".join(
        f"<tr><td class='mono'>{_esc(w['url'])}</td>"
        f"<td class='mono'>{_esc(', '.join(w['events']))}</td>"
        f"<td>{_pill(w['status'], w['status'])}</td>"
        f"<td class='mono'>{_esc(w.get('failure_count', 0))}</td></tr>"
        for w in webhooks
    ) or "<tr><td colspan='4'><div class='empty'>No webhooks registered.</div></td></tr>"

    inbound_uri = identity.get("inbound_sip_uri") or "(not configured — incoming calls will be rejected)"

    return 200, f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI Panel · {_esc(identity.get('phone_number','-'))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_BASE_CSS}</style>
</head>
<body>
  <header class="pn-header">
    <div class="wrap">
      <a class="pn-brand" href="/panel">AMI<span class="dot">.</span> Panel</a>
      <nav class="pn-nav">
        <a href="/panel">← Numbers</a>
        <form method="POST" action="/panel/logout" style="display:inline">
          <button class="pn-logout" type="submit">Sign out</button>
        </form>
      </nav>
    </div>
  </header>

  <main class="wrap">
    <h1>{_esc(identity.get('phone_number','-'))} {_pill(identity.get('status','-'), identity.get('status',''))}</h1>
    <p class="pn-subtitle">{_esc(identity['id'])} · activated {_esc(identity.get('activated_at','-')[:19].replace('T',' '))}</p>

    <h2>Usage</h2>
    <div class="stat-grid">
      {_stat("SMS today",   f"{snap['usage']['sms_count_today']} / {snap['limits']['sms_per_day']}", f"{snap['consumed_pct']['sms_day']}% of daily limit", snap['consumed_pct']['sms_day'])}
      {_stat("Calls today", f"{snap['usage']['calls_count_today']} / {snap['limits']['calls_per_day']}", f"{snap['consumed_pct']['calls_day']}% of daily limit", snap['consumed_pct']['calls_day'])}
      {_stat("Spend this month", f"€{snap['usage']['spend_this_month_eur']:.2f} / €{snap['limits']['monthly_budget_eur']:.0f}", f"{snap['consumed_pct']['monthly_budget']}% of budget", snap['consumed_pct']['monthly_budget'])}
      {_stat("Pricing", f"€{snap['pricing']['sms_eur']:.2f} / SMS", f"€{snap['pricing']['call_per_min_eur']:.2f} / call·min")}
    </div>

    <h2>Inbound voice routing</h2>
    <div class="stat-card">
      <div class="label">inbound_sip_uri</div>
      <div class="mono" style="word-break:break-all;color:var(--ink);font-size:0.9rem;margin-top:0.4rem">{_esc(inbound_uri)}</div>
      <div class="sub" style="margin-top:0.6rem">POST /v1/mobile-identities/{_esc(mid)}/inbound-config to change</div>
    </div>

    <h2>Recent SMS (last 20)</h2>
    <table class="pn-table">
      <thead><tr><th>When</th><th>Dir</th><th>Counterparty</th><th>Body</th><th>Status</th></tr></thead>
      <tbody>{sms_rows}</tbody>
    </table>

    <h2>Recent calls (last 20)</h2>
    <table class="pn-table">
      <thead><tr><th>When</th><th>Dir</th><th>Counterparty</th><th>Status</th><th>Duration (s)</th></tr></thead>
      <tbody>{call_rows}</tbody>
    </table>

    <h2>Webhooks</h2>
    <table class="pn-table">
      <thead><tr><th>URL</th><th>Events</th><th>Status</th><th>Failures</th></tr></thead>
      <tbody>{wh_rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def _render_not_found() -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>AMI Panel · Not found</title>
<style>{_BASE_CSS}</style>
</head>
<body>
  <main class="wrap" style="text-align:center;padding-top:6rem">
    <h1>Not found</h1>
    <p class="pn-subtitle">No MobileIdentity matches that id.</p>
    <p><a href="/panel">← Back to numbers</a></p>
  </main>
</body>
</html>"""
