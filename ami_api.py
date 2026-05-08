#!/usr/bin/env python3
"""AMI v1 mock API: contratación y aprovisionamiento de SIM/eSIM para agentes.

Sin dependencias externas. La SIM física es lo único stub: el resto del flujo
(oferta → datos cliente → contrato → firma → MobileIdentity activa) se comporta
como producción y respeta la máquina de estados de la spec §17.6.
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
import json, os, re, uuid

STATE = {
    "sim_requests": {},
    "offers": {},
    "customers": {},
    "contracts": {},
    "mobile_identities": {},
    "events": [],
}

COUNTRIES = {
    "ES": {
        "sim_types": ["eSIM", "SIM"],
        "capabilities": ["sms", "voice", "data", "whatsapp_ready"],
        "price": {"monthly": 8.90, "setup": 5.00, "currency": "EUR"},
        "activation_time": "mock: immediate after signature",
    }
}

# Máquina de estados de SIMRequest (spec §17.6).
TRANSITIONS = {
    "requested":                ["offer_created", "cancelled", "failed"],
    "offer_created":            ["offer_accepted", "cancelled", "rejected"],
    "offer_accepted":           ["customer_data_submitted", "cancelled"],
    "customer_data_submitted":  ["signature_pending", "cancelled"],
    "signature_pending":        ["signed", "cancelled"],
    "signed":                   ["provisioning", "failed"],
    "provisioning":             ["active", "failed"],
    "active":                   [],
    "cancelled":                [],
    "rejected":                 [],
    "failed":                   [],
}
TERMINAL = {"active", "cancelled", "rejected", "failed"}


API_KEY = os.environ.get("AMI_API_KEY") or None

# Rutas públicas (no requieren API key): landing, descubrimiento, install y firma desde el navegador.
PUBLIC_GET_PATHS = ("/", "/index.html", "/v1/health", "/llms.txt", "/openapi.json", "/install.sh", "/favicon.ico", "/spec")
PUBLIC_GET_REGEX = re.compile(r"^/(v1/sign/[^/]+|identity/[^/]+)$")
PUBLIC_POST_PATHS = ("/v1/demo/quick",)
PUBLIC_POST_REGEX = re.compile(r"^/v1/sign/[^/]+/confirm$")

# Cache del install.sh leído del disco al arrancar.
_INSTALL_SH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install.sh")
try:
    with open(_INSTALL_SH_PATH, "r", encoding="utf-8") as _f:
        INSTALL_SH = _f.read()
except FileNotFoundError:
    INSTALL_SH = "#!/bin/sh\necho 'install.sh missing on server'\n"

# Cache de la spec del protocolo (markdown), también leída al arrancar.
_SPEC_MD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs", "SPEC.md",
)
try:
    with open(_SPEC_MD_PATH, "r", encoding="utf-8") as _f:
        SPEC_MD = _f.read()
except FileNotFoundError:
    SPEC_MD = "# AMI spec missing on server\n"

# Configuración pública (URLs que aparecen en la landing y en /llms.txt).
REPO_URL = os.environ.get("AMI_REPO_URL", "https://github.com/Gamino17/AMI")
MCP_HTTP_URL = os.environ.get("AMI_MCP_HTTP_URL", "https://ami-mcp-http.onrender.com/mcp/")


def is_public(method, path):
    if method == "OPTIONS": return True
    if method == "GET":
        return path in PUBLIC_GET_PATHS or bool(PUBLIC_GET_REGEX.match(path))
    if method == "POST":
        return path in PUBLIC_POST_PATHS or bool(PUBLIC_POST_REGEX.match(path))
    return False


def check_auth(handler):
    if API_KEY is None:
        return True  # dev mode: sin AMI_API_KEY seteada se permite todo
    auth = handler.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and auth[7:] == API_KEY


def now(): return datetime.now(timezone.utc).isoformat()
def new_id(prefix): return f"{prefix}_{uuid.uuid4().hex[:10]}"


def event(action, entity_type, entity_id, data=None):
    e = {"id": new_id("evt"), "at": now(), "action": action,
         "entity_type": entity_type, "entity_id": entity_id, "data": data or {}}
    STATE["events"].append(e)
    return e


def transition_sim_request(req, new_status, reason=None):
    """Aplica una transición validada en una SIMRequest y emite AuditEvent."""
    cur = req["status"]
    if new_status not in TRANSITIONS.get(cur, []):
        raise ValueError(f"invalid_transition: {cur} -> {new_status}")
    req["status"] = new_status
    req["updated_at"] = now()
    if reason and new_status in TERMINAL:
        req["status_reason"] = reason
    event(f"sim_request_{new_status}", "sim_request", req["id"],
          {"from": cur, "to": new_status, "reason": reason})


def response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def respond_html(handler, status, html):
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def respond_text(handler, status, text, content_type="text/plain; charset=utf-8"):
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    n = int(handler.headers.get("Content-Length", "0") or 0)
    if not n: return {}
    return json.loads(handler.rfile.read(n).decode())


def run_quick_demo():
    """Orquesta el flujo end-to-end completo y devuelve un resumen estructurado.

    Reusa los mismos helpers internos (transition_sim_request, event, sign_contract)
    que el resto del backend para no duplicar lógica. Es solo un orquestador.
    """
    t0 = datetime.now(timezone.utc)
    steps = []

    # 1) SIMRequest + oferta inmediata
    country = "ES"
    req_id = new_id("simreq")
    req = {
        "id": req_id, "status": "requested", "country": country,
        "sim_type": "eSIM",
        "capabilities": ["sms", "voice", "data"],
        "agent": {"name": "demo-agent", "operator": "AMI try-me"},
        "commercial_constraints": {"max_monthly_price": 10},
        "created_at": now(), "updated_at": now(),
    }
    STATE["sim_requests"][req_id] = req
    event("sim_request_created", "sim_request", req_id, req)
    steps.append({"step": "request_sim_offer", "id": req_id})

    offer_id = new_id("offer")
    price = COUNTRIES[country]["price"]
    offer = {
        "id": offer_id, "sim_request_id": req_id, "status": "offer_created",
        "country": country, "sim_type": req["sim_type"],
        "capabilities": req["capabilities"],
        "monthly_price": price["monthly"], "setup_fee": price["setup"],
        "currency": price["currency"], "requires_contract": True,
        "requires_customer_data": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "created_at": now(),
    }
    STATE["offers"][offer_id] = offer
    req["offer_id"] = offer_id
    transition_sim_request(req, "offer_created")
    event("offer_created", "offer", offer_id, offer)
    steps.append({
        "step": "offer_created", "id": offer_id,
        "monthly_price": offer["monthly_price"], "currency": offer["currency"],
    })

    # 2) Aceptar oferta
    offer["status"] = "offer_accepted"; offer["accepted_at"] = now()
    event("offer_accepted", "offer", offer["id"], offer)
    transition_sim_request(req, "offer_accepted")
    steps.append({"step": "offer_accepted", "id": offer_id})

    # 3) Crear customer ficticio y vincularlo
    cid = new_id("customer")
    customer = {
        "id": cid, "status": "created",
        "legal_name": "Demo Industries S.L.",
        "tax_id": "B00000000",
        "billing_email": "demo@ami.local",
        "address": "Calle Demo 1, 28001 Madrid, ES",
        "representative_name": "Demo Representative",
        "created_at": now(),
    }
    STATE["customers"][cid] = customer
    req["customer_id"] = cid
    event("customer_created", "customer", cid, customer)
    transition_sim_request(req, "customer_data_submitted")
    steps.append({"step": "customer_linked", "id": cid})

    # 4) Crear contrato
    contract_id = new_id("contract")
    base_url = os.environ.get("AMI_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    contract = {
        "id": contract_id, "status": "signature_pending",
        "offer_id": offer_id, "customer_id": cid,
        "sim_request_id": req_id,
        "signature_url": f"{base_url}/v1/sign/{contract_id}",
        "created_at": now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }
    STATE["contracts"][contract_id] = contract
    event("contract_created", "contract", contract_id, contract)
    transition_sim_request(req, "signature_pending")
    steps.append({"step": "contract_created", "id": contract_id})

    # 5) Firmar contrato (saltarse la página de firma para esta demo)
    sign_contract(contract_id)
    steps.append({"step": "contract_signed", "id": contract_id})

    # 6) Activar MobileIdentity
    if req["status"] == "signed":
        transition_sim_request(req, "provisioning")
    mid = new_id("mid")
    phone = "+34 600 " + str(int(uuid.uuid4().hex[:6], 16))[-6:].rjust(6, "0")[:6]
    identity = {
        "id": mid, "status": "active", "phone_number": phone,
        "sim_type": offer["sim_type"], "capabilities": offer["capabilities"],
        "contract_id": contract_id, "customer_id": cid,
        "sim_request_id": req_id,
        "provider_activation_id": new_id("mockact"),
        "esim_qr_url": f"https://telco.mock/esim/{mid}.qr",
        "activated_at": now(),
    }
    STATE["mobile_identities"][mid] = identity
    event("mobile_identity_active", "mobile_identity", mid, identity)
    transition_sim_request(req, "active")
    req["mobile_identity_id"] = mid
    steps.append({"step": "mobile_identity_active", "id": mid, "phone_number": phone})

    elapsed_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    return {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "steps": steps,
        "mobile_identity": identity,
    }


def sign_contract(contract_id):
    """Marca un contrato como firmado y avanza la SIMRequest. Devuelve (code, body)."""
    contract = STATE["contracts"].get(contract_id)
    if not contract: return 404, {"error": "contract_not_found"}
    if contract["status"] == "signed": return 409, {"error": "already_signed", "contract": contract}
    contract["status"] = "signed"; contract["signed_at"] = now()
    event("contract_signed", "contract", contract["id"], contract)
    req = STATE["sim_requests"].get(contract.get("sim_request_id"))
    if req and req["status"] == "signature_pending":
        transition_sim_request(req, "signed")
    return 200, contract


# --------------------------------------------------------------- HTML pages
PAGE_CSS = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 640px; margin: 4rem auto; padding: 0 1.5rem; color: #111; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .sub { color: #666; margin-top: 0; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
           font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  .badge.pending { background: #fff3cd; color: #856404; }
  .badge.signed  { background: #d4edda; color: #155724; }
  .card { border: 1px solid #e5e5e5; border-radius: 8px; padding: 1rem 1.25rem; margin: 1.25rem 0; }
  dl { display: grid; grid-template-columns: 9rem 1fr; gap: 0.5rem 1rem; margin: 0; }
  dt { color: #666; }
  dd { margin: 0; font-weight: 500; }
  button { background: #111; color: #fff; border: 0; padding: 0.75rem 1.5rem;
           border-radius: 6px; font-size: 1rem; cursor: pointer; }
  button:hover { background: #333; }
  .legal { color: #888; font-size: 0.8rem; margin-top: 1rem; }
  .ok { color: #155724; }
"""


def html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))


def render_sign_page(contract):
    offer = STATE["offers"].get(contract["offer_id"], {})
    customer = STATE["customers"].get(contract["customer_id"], {})
    caps = ", ".join(offer.get("capabilities", [])) or "—"
    is_signed = contract["status"] == "signed"
    badge = '<span class="badge signed">Firmado</span>' if is_signed else \
            '<span class="badge pending">Pendiente de firma</span>'
    action_block = (
        f'<p class="ok">✓ Contrato firmado el {html_escape(contract.get("signed_at",""))}.</p>'
        f'<p>El proveedor de identidad móvil completará la activación.</p>'
        if is_signed else
        f'<form method="post" action="/v1/sign/{html_escape(contract["id"])}/confirm">'
        f'  <button type="submit">Firmar contrato</button>'
        f'</form>'
        f'<p class="legal">Al pulsar "Firmar contrato" aceptas los términos del servicio AMI v1 '
        f'y confirmas la veracidad de los datos del cliente.</p>'
    )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Firma de contrato AMI · {html_escape(contract["id"])}</title>
<style>{PAGE_CSS}</style></head>
<body>
  <h1>Firma de contrato AMI</h1>
  <p class="sub">{badge}</p>

  <div class="card">
    <h2 style="margin-top:0;font-size:1rem;">Cliente</h2>
    <dl>
      <dt>Razón social</dt><dd>{html_escape(customer.get("legal_name","—"))}</dd>
      <dt>NIF/CIF</dt><dd>{html_escape(customer.get("tax_id","—"))}</dd>
      <dt>Representante</dt><dd>{html_escape(customer.get("representative_name","—"))}</dd>
      <dt>Email</dt><dd>{html_escape(customer.get("billing_email","—"))}</dd>
      <dt>Dirección</dt><dd>{html_escape(customer.get("address","—"))}</dd>
    </dl>
  </div>

  <div class="card">
    <h2 style="margin-top:0;font-size:1rem;">Servicio</h2>
    <dl>
      <dt>Tipo</dt><dd>{html_escape(offer.get("sim_type","—"))} · {html_escape(offer.get("country","—"))}</dd>
      <dt>Capacidades</dt><dd>{html_escape(caps)}</dd>
      <dt>Precio mensual</dt><dd>{offer.get("monthly_price","—")} {html_escape(offer.get("currency",""))}</dd>
      <dt>Alta</dt><dd>{offer.get("setup_fee","—")} {html_escape(offer.get("currency",""))}</dd>
    </dl>
  </div>

  <div class="card">
    <h2 style="margin-top:0;font-size:1rem;">Contrato</h2>
    <dl>
      <dt>ID</dt><dd>{html_escape(contract["id"])}</dd>
      <dt>Creado</dt><dd>{html_escape(contract.get("created_at",""))}</dd>
      <dt>Vence</dt><dd>{html_escape(contract.get("expires_at",""))}</dd>
    </dl>
  </div>

  {action_block}
</body></html>"""


def render_sign_error(message):
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Firma AMI · error</title><style>{PAGE_CSS}</style></head>
<body><h1>No se puede firmar</h1><p>{html_escape(message)}</p></body></html>"""


# --------------------------------------------------------- Landing & discovery
LANDING_CSS = """
  :root {
    --bg:        #08080c;
    --bg-soft:   #0e0e14;
    --surface:   #14141d;
    --line:      #1f1f2c;
    --line-soft: #16161f;
    --ink:       #ededf2;
    --ink-soft:  #8888a0;
    --ink-mute:  #5a5a70;
    --accent:    #8b6cff;
    --accent-2:  #5dd1ff;
    --accent-bg: rgba(139, 108, 255, 0.10);
    --green:     #4ade80;
    --amber:     #fbbf24;
    --code-bg:   #0c0c12;
    --sans:      "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono:      "JetBrains Mono", "SF Mono", "Menlo", "Monaco", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background: var(--bg);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }
  /* Toggle bilingüe */
  html[lang="es"] [data-lang="en"] { display: none; }
  html[lang="en"] [data-lang="es"] { display: none; }

  ::selection { background: var(--accent); color: #fff; }

  a { color: var(--accent-2); text-decoration: none; }
  a:hover { color: #b9e6ff; }

  .wrap { max-width: 1120px; margin: 0 auto; padding: 0 1.75rem; }

  /* HEADER -------------------------------------------------------------- */
  header {
    border-bottom: 1px solid var(--line);
    padding: 1rem 0;
    background: rgba(8, 8, 12, 0.72);
    position: sticky; top: 0; z-index: 50;
    backdrop-filter: saturate(140%) blur(14px);
    -webkit-backdrop-filter: saturate(140%) blur(14px);
  }
  header .wrap { display: flex; align-items: center; justify-content: space-between; }
  .brand {
    font-family: var(--mono); font-weight: 700; font-size: 0.95rem;
    letter-spacing: 0.02em; display: flex; align-items: center; gap: 0.55rem;
  }
  .brand .dot { color: var(--accent); }
  .brand .logo {
    width: 24px; height: 24px; display: block; flex-shrink: 0;
    color: var(--accent);
  }
  .brand .logo path,
  .brand .logo line,
  .brand .logo circle { vector-effect: non-scaling-stroke; }
  nav { display: flex; align-items: center; gap: 1.6rem; }
  nav a { color: var(--ink-soft); font-size: 0.88rem; font-weight: 500; }
  nav a:hover { color: var(--ink); }
  .lang-toggle {
    background: transparent; border: 1px solid var(--line);
    color: var(--ink-soft); padding: 0.3rem 0.7rem;
    font-family: var(--mono); font-size: 0.75rem; font-weight: 600;
    border-radius: 5px; cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }
  .lang-toggle:hover { border-color: var(--accent); color: var(--ink); }

  /* HERO ---------------------------------------------------------------- */
  .hero {
    position: relative; padding: 7rem 0 6rem; overflow: hidden;
  }
  .hero::before, .hero::after {
    content: ""; position: absolute; pointer-events: none; z-index: 0;
    border-radius: 50%; filter: blur(110px); opacity: 0.55;
  }
  .hero::before {
    width: 600px; height: 600px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 65%);
    top: -200px; left: -120px;
  }
  .hero::after {
    width: 520px; height: 520px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 65%);
    top: 120px; right: -80px;
    opacity: 0.35;
  }
  .hero .wrap { position: relative; z-index: 1; }

  .pill {
    display: inline-flex; align-items: center; gap: 0.5rem;
    background: var(--surface);
    border: 1px solid var(--line);
    padding: 0.35rem 0.85rem; border-radius: 999px;
    font-family: var(--mono); font-size: 0.72rem; font-weight: 500;
    color: var(--ink-soft); letter-spacing: 0.02em;
    margin-bottom: 2rem;
  }
  .pill .live {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 8px var(--green);
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.4; }
  }
  .pill .sep { color: var(--ink-mute); }

  h1.hero-title {
    font-weight: 700; font-size: clamp(3rem, 7.5vw, 5.5rem);
    line-height: 0.98; letter-spacing: -0.045em;
    margin: 0 0 1.5rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 120%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .lead {
    font-size: 1.15rem; color: var(--ink-soft); max-width: 38em;
    margin: 0 0 2.5rem 0; line-height: 1.55;
  }
  .cta-row { display: flex; gap: 0.75rem; flex-wrap: wrap; }
  .btn {
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.85rem 1.4rem; border-radius: 8px;
    font-family: var(--sans); font-size: 0.95rem; font-weight: 500;
    text-decoration: none; cursor: pointer;
    transition: transform 0.06s, box-shadow 0.2s, background 0.15s, border-color 0.15s;
  }
  .btn:active { transform: translateY(1px); }
  .btn-primary {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff;
    box-shadow: 0 1px 0 rgba(255,255,255,0.15) inset, 0 8px 24px -8px rgba(123, 92, 255, 0.5);
  }
  .btn-primary:hover { box-shadow: 0 1px 0 rgba(255,255,255,0.2) inset, 0 12px 32px -8px rgba(123, 92, 255, 0.65); }
  .btn-secondary {
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--line);
  }
  .btn-secondary:hover { border-color: var(--accent); color: #fff; }

  /* TERMINAL HERO ------------------------------------------------------- */
  .terminal {
    margin-top: 4rem;
    background: var(--code-bg);
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 30px 80px -20px rgba(0,0,0,0.6),
                0 0 0 1px rgba(139, 108, 255, 0.08);
    max-width: 760px; margin-left: auto; margin-right: auto;
  }
  .terminal-bar {
    display: flex; align-items: center; gap: 0.45rem;
    padding: 0.7rem 1rem;
    border-bottom: 1px solid var(--line);
    background: rgba(255,255,255,0.015);
  }
  .terminal-bar .dot { width: 11px; height: 11px; border-radius: 50%; background: #2a2a3a; }
  .terminal-bar .title {
    margin-left: 0.6rem; font-family: var(--mono); font-size: 0.72rem; color: var(--ink-mute);
  }
  .terminal-body {
    padding: 1.4rem 1.5rem;
    font-family: var(--mono); font-size: 0.86rem; line-height: 1.7;
    color: var(--ink);
  }
  .terminal-body .prompt { color: var(--ink-mute); }
  .terminal-body .tool   { color: var(--accent); }
  .terminal-body .key    { color: #c4b3ff; }
  .terminal-body .str    { color: #82e0a4; }
  .terminal-body .num    { color: #fbbf24; }
  .terminal-body .out    { color: var(--ink-soft); }
  .terminal-body .ok     { color: var(--green); }
  .terminal-body .arrow  { color: var(--accent); }

  /* QUICK START -------------------------------------------------------- */
  .qs {
    padding: 5rem 0 6rem;
    border-top: 1px solid var(--line);
    position: relative;
  }
  .qs::before {
    content: ""; position: absolute; pointer-events: none; z-index: 0;
    width: 600px; height: 300px;
    background: radial-gradient(ellipse, #8b6cff 0%, transparent 65%);
    top: -50px; left: 50%; transform: translateX(-50%);
    filter: blur(120px); opacity: 0.18;
  }
  .qs .wrap { position: relative; z-index: 1; }
  .qs-head {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 1.25rem; flex-wrap: wrap; gap: 1rem;
  }
  .qs-title {
    font-size: 1.4rem; font-weight: 600; letter-spacing: -0.01em; margin: 0;
    display: flex; align-items: center; gap: 0.7rem;
  }
  .qs-title .chev { color: var(--accent); font-family: var(--mono); }
  .qs-help {
    color: var(--ink-mute); font-family: var(--mono); font-size: 0.78rem;
  }
  .terminal-card {
    background: var(--code-bg);
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 30px 80px -20px rgba(0,0,0,0.6),
                0 0 0 1px rgba(139, 108, 255, 0.08);
  }
  .terminal-card .bar {
    display: flex; align-items: center; gap: 0.45rem;
    padding: 0.65rem 1rem;
    border-bottom: 1px solid var(--line);
    background: rgba(255,255,255,0.02);
    flex-wrap: wrap;
  }
  .terminal-card .bar .dot { width: 11px; height: 11px; border-radius: 50%; background: #2a2a3a; }
  .tabs {
    display: flex; gap: 0.25rem; margin-left: 0.8rem;
  }
  .tab {
    background: transparent; border: 0; cursor: pointer;
    color: var(--ink-soft); padding: 0.32rem 0.7rem;
    font-family: var(--mono); font-size: 0.78rem; font-weight: 500;
    border-radius: 5px;
    transition: background 0.12s, color 0.12s;
  }
  .tab:hover { color: var(--ink); background: rgba(255,255,255,0.03); }
  .tab.active {
    background: var(--accent);
    color: #fff;
  }
  .badge-beta {
    margin-left: auto;
    font-family: var(--mono); font-size: 0.68rem; font-weight: 700;
    padding: 0.2rem 0.55rem; border-radius: 4px;
    background: var(--surface); color: var(--accent-2);
    border: 1px solid var(--line); letter-spacing: 0.06em;
  }
  .panes { position: relative; }
  .pane {
    display: none;
    padding: 1.4rem 1.5rem 1.6rem;
    font-family: var(--mono); font-size: 0.86rem; line-height: 1.7;
    color: var(--ink); position: relative;
  }
  .pane.active { display: block; }
  .pane .comment { color: var(--ink-mute); }
  .pane .prompt  { color: var(--ink-mute); }
  .pane .cmd     { color: var(--ink); }
  .pane .accent  { color: var(--accent-2); }
  .copy-btn {
    position: absolute; top: 0.9rem; right: 0.9rem;
    background: var(--surface); border: 1px solid var(--line);
    color: var(--ink-soft);
    padding: 0.35rem 0.6rem;
    font-family: var(--mono); font-size: 0.7rem;
    border-radius: 5px; cursor: pointer;
    transition: border-color 0.12s, color 0.12s;
  }
  .copy-btn:hover { border-color: var(--accent); color: var(--ink); }
  .copy-btn.copied { color: var(--green); border-color: var(--green); }
  .qs-foot {
    color: var(--ink-soft); font-size: 0.88rem; line-height: 1.5;
    margin-top: 1.25rem; max-width: 50em;
  }
  .qs-foot code {
    background: var(--surface); border: 1px solid var(--line);
    padding: 0.1em 0.45em; border-radius: 4px;
    font-size: 0.85em; color: var(--accent-2);
  }

  /* TRY IN BROWSER ----------------------------------------------------- */
  .try-block { margin-top: 2.5rem; }
  .try-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 1.25rem; flex-wrap: wrap;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.25rem 1.4rem;
  }
  .try-title { font-size: 1rem; font-weight: 600; color: var(--ink); }
  .try-sub   { color: var(--ink-soft); font-size: 0.88rem; margin-top: 0.25rem; }
  .try-btn { white-space: nowrap; }
  .try-btn[disabled] {
    opacity: 0.7; cursor: progress;
    background: var(--surface); color: var(--ink-soft);
    border: 1px solid var(--line); box-shadow: none;
  }
  .try-panel { margin-top: 1rem; }
  .try-body {
    padding: 1.2rem 1.4rem 1.4rem;
    font-family: var(--mono); font-size: 0.84rem; line-height: 1.7;
    color: var(--ink);
  }
  .try-status {
    color: var(--ink-soft); font-family: var(--mono); font-size: 0.78rem;
    letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 0.5rem;
  }
  .try-status.error { color: #ff8b8b; }
  .try-status.ok    { color: var(--green); }
  .try-log .row {
    display: grid; grid-template-columns: 4.5rem 11rem 1fr; gap: 0.6rem;
    align-items: baseline; padding: 0.18rem 0;
  }
  .try-log .ts   { color: var(--ink-mute); font-size: 0.78rem; }
  .try-log .step { color: var(--accent); font-weight: 600; }
  .try-log .id   { color: var(--ink-soft); word-break: break-all; }
  .try-log .row.ok .step { color: var(--green); }
  .try-result {
    margin-top: 1rem; padding-top: 1rem;
    border-top: 1px dashed var(--line);
  }
  .try-result .label {
    font-size: 0.72rem; color: var(--ink-mute); letter-spacing: 0.06em;
    text-transform: uppercase; margin-bottom: 0.3rem;
  }
  .try-result .phone {
    font-family: var(--mono); font-size: 1.6rem; color: var(--ink);
    font-weight: 600; letter-spacing: 0.02em;
  }
  .try-result .mid-id {
    font-family: var(--mono); font-size: 0.82rem; color: var(--accent-2);
    word-break: break-all;
  }
  .try-result .links { margin-top: 0.9rem; display: flex; gap: 1.2rem; flex-wrap: wrap; }
  .try-result .links a {
    font-family: var(--mono); font-size: 0.82rem;
    color: var(--accent-2);
  }
  .try-result .links a:hover { color: #b9e6ff; }

  /* SECTIONS ------------------------------------------------------------ */
  section { padding: 6rem 0; position: relative; }
  section + section { border-top: 1px solid var(--line); }
  section.qs + section { border-top: 1px solid var(--line); }

  .eyebrow {
    font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.12em;
    margin-bottom: 0.85rem;
  }
  h2 {
    font-weight: 600; font-size: clamp(1.8rem, 3.5vw, 2.6rem);
    line-height: 1.1; letter-spacing: -0.02em;
    margin: 0 0 1rem 0; color: var(--ink);
  }
  h2 + .sub { color: var(--ink-soft); font-size: 1.05rem; max-width: 38em; margin: 0 0 3rem 0; }

  /* BENTO --------------------------------------------------------------- */
  .bento {
    display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem;
  }
  .bento .cell {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.6rem;
    transition: border-color 0.2s, transform 0.2s;
  }
  .bento .cell:hover { border-color: rgba(139, 108, 255, 0.4); transform: translateY(-2px); }
  .bento .cell h3 {
    margin: 0 0 0.55rem 0; font-size: 1.05rem; font-weight: 600; color: var(--ink);
    display: flex; align-items: center; gap: 0.5rem;
  }
  .bento .cell h3 .icon {
    font-family: var(--mono); font-size: 0.82rem; color: var(--accent);
    background: var(--accent-bg); padding: 2px 7px; border-radius: 4px;
  }
  .bento .cell p { margin: 0; color: var(--ink-soft); font-size: 0.92rem; line-height: 1.55; }
  @media (max-width: 720px) { .bento { grid-template-columns: 1fr; } }

  /* CONNECT CARDS ------------------------------------------------------- */
  .connect {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
  }
  .connect .card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.6rem; display: flex; flex-direction: column;
    transition: border-color 0.2s;
  }
  .connect .card:hover { border-color: rgba(139, 108, 255, 0.4); }
  .connect .card .step-tag {
    font-family: var(--mono); font-size: 0.7rem; color: var(--ink-mute);
    text-transform: uppercase; letter-spacing: 0.12em; margin-bottom: 0.6rem;
  }
  .connect .card h3 { margin: 0 0 0.6rem 0; font-size: 1.1rem; font-weight: 600; }
  .connect .card p { margin: 0 0 1.2rem 0; color: var(--ink-soft); font-size: 0.9rem; flex-grow: 1; }
  .connect .card pre { margin-top: auto; }
  @media (max-width: 900px) { .connect { grid-template-columns: 1fr; } }

  /* CODE BLOCKS --------------------------------------------------------- */
  pre {
    background: var(--code-bg); color: var(--ink);
    padding: 1rem 1.2rem; border-radius: 8px;
    font-family: var(--mono); font-size: 0.8rem; line-height: 1.6;
    overflow-x: auto; margin: 0;
    border: 1px solid var(--line);
  }
  code { font-family: var(--mono); font-size: 0.92em; }
  p code, li code, h3 code, .step-body code {
    background: var(--surface); padding: 0.12em 0.45em;
    border-radius: 4px; color: var(--accent-2); font-size: 0.88em;
    border: 1px solid var(--line);
  }

  /* TOOLS GRID ---------------------------------------------------------- */
  .tools-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1px;
    background: var(--line); border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden;
  }
  .tool-cell {
    background: var(--bg-soft); padding: 1.2rem 1.4rem;
    transition: background 0.15s;
  }
  .tool-cell:hover { background: var(--surface); }
  .tool-cell .name {
    font-family: var(--mono); font-size: 0.85rem; color: var(--accent);
    font-weight: 500; margin-bottom: 0.3rem;
  }
  .tool-cell .desc { color: var(--ink-soft); font-size: 0.85rem; line-height: 1.5; }

  /* ENDPOINTS ----------------------------------------------------------- */
  .endpoint-list {
    list-style: none; padding: 0; margin: 0;
    background: var(--bg-soft); border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden;
  }
  .endpoint-list li {
    padding: 0.85rem 1.4rem;
    border-bottom: 1px solid var(--line-soft);
    display: grid; grid-template-columns: 4rem 1fr 2fr; gap: 1rem; align-items: baseline;
    font-size: 0.88rem;
  }
  .endpoint-list li:last-child { border-bottom: 0; }
  .endpoint-list li:hover { background: var(--surface); }
  .verb {
    font-family: var(--mono); font-weight: 600; font-size: 0.78rem;
    color: var(--accent); letter-spacing: 0.02em;
  }
  .endpoint-list code { color: var(--ink); background: transparent; border: 0; padding: 0; font-size: 0.82rem; }
  .endpoint-list .desc { color: var(--ink-soft); }
  @media (max-width: 720px) {
    .endpoint-list li { grid-template-columns: 3.5rem 1fr; }
    .endpoint-list .desc { grid-column: 1 / -1; padding-left: 4.5rem; font-size: 0.82rem; }
  }

  /* FLOW DIAGRAM -------------------------------------------------------- */
  .flow {
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.5rem;
    overflow-x: auto;
  }
  .flow pre {
    background: transparent; border: 0; padding: 0; color: var(--ink-soft);
    font-size: 0.8rem; white-space: pre;
  }
  .flow .hl { color: var(--accent); font-weight: 600; }
  .flow .hl2 { color: var(--accent-2); font-weight: 600; }

  /* CTA FINAL ----------------------------------------------------------- */
  .final-cta {
    text-align: center;
    padding: 7rem 0;
    position: relative; overflow: hidden;
    border-top: 1px solid var(--line);
  }
  .final-cta::before {
    content: ""; position: absolute; pointer-events: none;
    width: 800px; height: 400px;
    background: radial-gradient(ellipse, #8b6cff 0%, transparent 65%);
    top: 50%; left: 50%; transform: translate(-50%, -50%);
    filter: blur(120px); opacity: 0.30;
  }
  .final-cta .wrap { position: relative; z-index: 1; }
  .final-cta h2 {
    font-size: clamp(2rem, 5vw, 3.4rem); margin-bottom: 1.5rem;
    background: linear-gradient(180deg, #ffffff 20%, #a8a8c8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  /* FOOTER -------------------------------------------------------------- */
  footer {
    padding: 3rem 0; color: var(--ink-mute);
    border-top: 1px solid var(--line);
  }
  footer .wrap {
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 1rem;
    font-family: var(--mono); font-size: 0.78rem;
  }
  footer .links a { margin-left: 1.4rem; color: var(--ink-soft); }
  footer .links a:hover { color: var(--ink); }

  @media (max-width: 600px) {
    section { padding: 4rem 0; }
    .hero { padding: 4.5rem 0 4rem; }
    nav { gap: 0.9rem; }
    nav a { font-size: 0.82rem; }
    footer .wrap { flex-direction: column; align-items: flex-start; }
    footer .links a { margin: 0 1.2rem 0 0; }
  }
"""


# Símbolo AMI: SIM card (rect con esquina cortada) + nodo y ondas de señal.
# Pensado para ser monocromo y legible a 24px (header) y 32px (favicon).
AMI_LOGO_SVG_INNER = (
    '<path d="M3 6 L13 6 L17 10 L17 22 Q17 24 15 24 L3 24 Q1 24 1 22 L1 8 Q1 6 3 6 Z" '
    'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>'
    '<path d="M5 11 L13 11 M5 14 L13 14 M5 17 L13 17" '
    'stroke="currentColor" stroke-width="1.1" stroke-linecap="round" opacity="0.55"/>'
    '<circle cx="22.5" cy="9.5" r="1.4" fill="currentColor"/>'
    '<path d="M21 13 Q24 13 25.5 11" fill="none" '
    'stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.85"/>'
    '<path d="M20.2 15.4 Q25 15.4 27.5 12.5" fill="none" '
    'stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.55"/>'
)
AMI_LOGO_SVG = (
    '<svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30" '
    'aria-hidden="true" focusable="false">' + AMI_LOGO_SVG_INNER + '</svg>'
)

# Favicon: el mismo símbolo sobre fondo dark, con tinta violeta sólida.
# Se sirve inline como data URI (sin codificar más allá de comillas simples,
# que es lo que tolera el atributo href del navegador).
_FAVICON_INNER = AMI_LOGO_SVG_INNER.replace("currentColor", "#8b6cff")
FAVICON_SVG_DATA_URI = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='6' fill='%230a0a0f'/>"
    "<g transform='translate(1,1)'>"
    + _FAVICON_INNER.replace('"', "'").replace("#8b6cff", "%238b6cff")
    + "</g></svg>"
)


def _tools_for_landing():
    return [
        ("ami.search_sim_options",      "Lista países, tipos de SIM/eSIM y capacidades disponibles."),
        ("ami.request_sim_offer",       "Crea una SIMRequest y devuelve la oferta inmediata del partner telco."),
        ("ami.accept_offer",            "Acepta una oferta antes de generar contrato."),
        ("ami.submit_customer_data",    "Envía los datos legales/fiscales del cliente y los vincula a la solicitud."),
        ("ami.create_contract",         "Genera el contrato y devuelve la URL de firma."),
        ("ami.get_contract_status",     "Consulta el estado actual de un contrato."),
        ("ami.confirm_signature_status","Comprueba si el contrato ya está firmado."),
        ("ami.activate_sim_identity",   "Inicia el provisioning con el partner telco tras la firma."),
        ("ami.get_identity_status",     "Consulta el estado de una MobileIdentity activa."),
        ("ami.cancel_request",          "Cancela una SIMRequest antes de la activación."),
        ("ami.list_events",             "Devuelve los últimos AuditEvents (debug e inspección)."),
    ]


def _endpoints_for_landing():
    return [
        ("GET",  "/v1/health",                              "Healthcheck (público)."),
        ("GET",  "/v1/sim-options",                         "Países y SIMs disponibles."),
        ("POST", "/v1/sim-requests",                        "Crea SIMRequest y oferta."),
        ("POST", "/v1/sim-requests/{id}/cancel",            "Cancela una SIMRequest."),
        ("POST", "/v1/sim-requests/{id}/customer-data",     "Vincula datos del cliente."),
        ("POST", "/v1/offers/{id}/accept",                  "Acepta una oferta."),
        ("POST", "/v1/contracts",                           "Crea contrato y signature_url."),
        ("GET",  "/v1/contracts/{id}",                      "Consulta el contrato."),
        ("GET",  "/v1/sign/{id}",                           "Página HTML de firma (pública)."),
        ("POST", "/v1/sign/{id}/confirm",                   "Callback del form de firma (público)."),
        ("POST", "/v1/mobile-identities/activate",          "Activa la MobileIdentity tras la firma."),
        ("GET",  "/v1/mobile-identities/{id}",              "Consulta una MobileIdentity."),
        ("GET",  "/v1/events",                              "Últimos AuditEvents."),
        ("POST", "/v1/demo/quick",                          "Flujo end-to-end completo en una llamada (público, sin auth)."),
        ("GET",  "/identity/{id}",                          "Página pública de una MobileIdentity activa."),
        ("GET",  "/spec",                                   "Spec del protocolo renderizada en HTML."),
    ]


def render_landing():
    tools_html = "".join(
        f'<div class="tool-cell"><div class="name">{n}</div><div class="desc">{html_escape(d)}</div></div>'
        for n, d in _tools_for_landing()
    )
    endpoints_html = "".join(
        f'<li><span class="verb">{v}</span><code>{p}</code><span class="desc">{html_escape(d)}</span></li>'
        for v, p, d in _endpoints_for_landing()
    )
    claude_config = json.dumps({
        "mcpServers": {
            "ami": {
                "command": "python3",
                "args": ["/path/to/AMI/ami_mcp.py"],
                "env": {
                    "AMI_API_URL": "https://ami-mock-api.onrender.com",
                    "AMI_API_KEY": "<your-api-key>"
                }
            }
        }
    }, indent=2)
    repo = html_escape(REPO_URL)
    mcp_url = html_escape(MCP_HTTP_URL)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI — Mobile identity for AI agents</title>
<meta name="description" content="The protocol layer for AI agents to request, contract and activate their own mobile identity. SIM, eSIM and phone numbers — programmable, auditable, governed.">
<meta property="og:title" content="AMI — Mobile identity for AI agents">
<meta property="og:description" content="The protocol layer for AI agents to provision their own mobile identity.">
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{LANDING_CSS}</style>
<script>
  // Restore language preference before paint to avoid FOUC.
  (function() {{
    try {{
      var l = localStorage.getItem('ami-lang');
      if (l === 'es' || l === 'en') document.documentElement.lang = l;
    }} catch(e) {{}}
  }})();
</script>
</head>
<body>

  <header>
    <div class="wrap">
      <div class="brand">{AMI_LOGO_SVG} AMI<span class="dot">.</span></div>
      <nav>
        <a href="#protocol" data-lang="es">Protocolo</a>
        <a href="#protocol" data-lang="en">Protocol</a>
        <a href="#connect" data-lang="es">Conectar</a>
        <a href="#connect" data-lang="en">Connect</a>
        <a href="#tools">Tools</a>
        <a href="#api">API</a>
        <a href="/spec">Spec</a>
        <a href="{repo}">GitHub</a>
        <button class="lang-toggle" id="langToggle" aria-label="Toggle language">EN</button>
      </nav>
    </div>
  </header>

  <!-- HERO ============================================================== -->
  <section class="hero">
    <div class="wrap">
      <span class="pill">
        <span class="live"></span>
        <span data-lang="es">v1.0 · referencia · contratación &amp; aprovisionamiento</span>
        <span data-lang="en">v1.0 · reference · contracting &amp; provisioning</span>
      </span>

      <h1 class="hero-title">
        <span data-lang="es">Identidad móvil<br>para agentes&nbsp;AI.</span>
        <span data-lang="en">Mobile identity<br>for AI agents.</span>
      </h1>

      <p class="lead">
        <span data-lang="es">AMI es la capa de protocolo para que un agente solicite, contrate y active su propia identidad móvil — SIM, eSIM, número de teléfono — sin pasar por procesos pensados para humanos. Programable, auditable, gobernada.</span>
        <span data-lang="en">AMI is the protocol layer for AI agents to request, contract and activate their own mobile identity — SIM, eSIM, phone numbers — without going through human-designed processes. Programmable, auditable, governed.</span>
      </p>

      <div class="cta-row">
        <a class="btn btn-primary" href="#connect">
          <span data-lang="es">Conectar tu agente →</span>
          <span data-lang="en">Connect your agent →</span>
        </a>
        <a class="btn btn-secondary" href="{repo}">
          <span data-lang="es">Ver código</span>
          <span data-lang="en">View source</span>
        </a>
      </div>

      <div class="terminal" role="img" aria-label="AMI terminal example">
        <div class="terminal-bar">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <span class="title">agent.session — provisioning eSIM</span>
        </div>
        <div class="terminal-body">
<span class="prompt">›</span> <span class="tool">ami.request_sim_offer</span>({{
  <span class="key">country</span>: <span class="str">"ES"</span>,
  <span class="key">capabilities</span>: [<span class="str">"sms"</span>, <span class="str">"voice"</span>],
  <span class="key">max_monthly_price</span>: <span class="num">10</span>
}})

<span class="out">  → offer_b76915a004 · 8.90 EUR/mo · ready</span>

<span class="prompt">›</span> <span class="tool">ami.create_contract</span>(...) · <span class="tool">ami.activate_sim_identity</span>(...)

<span class="arrow">←</span> <span class="ok">mobile identity active</span>
  <span class="key">phone</span>:    <span class="str">"+34 600 ███ ███"</span>
  <span class="key">sim_type</span>: <span class="str">"eSIM"</span>
  <span class="key">contract</span>: <span class="str">"signed"</span>  ·  <span class="ok">1.4s</span>
        </div>
      </div>
    </div>
  </section>

  <!-- QUICK START ======================================================= -->
  <section class="qs" id="quickstart">
    <div class="wrap">
      <div class="qs-head">
        <h2 class="qs-title">
          <span class="chev">›</span>
          <span data-lang="es">Quick Start</span>
          <span data-lang="en">Quick Start</span>
        </h2>
        <span class="qs-help">
          <span data-lang="es">macOS · Linux · Python 3.10+</span>
          <span data-lang="en">macOS · Linux · Python 3.10+</span>
        </span>
      </div>

      <div class="terminal-card">
        <div class="bar">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <div class="tabs" role="tablist">
            <button class="tab active" data-pane="pane-oneliner" role="tab">One-liner</button>
            <button class="tab" data-pane="pane-clone" role="tab">git clone</button>
            <button class="tab" data-pane="pane-claude" role="tab">Claude config</button>
          </div>
          <span class="badge-beta">β BETA</span>
        </div>

        <div class="panes">
          <div class="pane active" id="pane-oneliner">
            <button class="copy-btn" data-copy="curl -fsSL https://ami-mock-api.onrender.com/install.sh | sh">copy</button>
<span class="comment"># <span data-lang="es">Instala AMI en ~/.ami: clona repo, monta venv, instala MCP SDK.</span><span data-lang="en">Installs AMI into ~/.ami: clones repo, builds venv, installs MCP SDK.</span></span>
<span class="prompt">$</span> <span class="cmd">curl -fsSL <span class="accent">https://ami-mock-api.onrender.com/install.sh</span> | sh</span>
          </div>

          <div class="pane" id="pane-clone">
            <button class="copy-btn" data-copy="git clone https://github.com/Gamino17/AMI && cd AMI && uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -r requirements.txt">copy</button>
<span class="comment"># <span data-lang="es">Setup manual con uv (recomendado).</span><span data-lang="en">Manual setup with uv (recommended).</span></span>
<span class="prompt">$</span> <span class="cmd">git clone <span class="accent">https://github.com/Gamino17/AMI</span></span>
<span class="prompt">$</span> <span class="cmd">cd AMI</span>
<span class="prompt">$</span> <span class="cmd">uv venv --python 3.13 .venv</span>
<span class="prompt">$</span> <span class="cmd">uv pip install --python .venv/bin/python -r requirements.txt</span>
          </div>

          <div class="pane" id="pane-claude">
            <button class="copy-btn" data-copy='{{
  "mcpServers": {{
    "ami": {{
      "command": "/Users/you/.ami/.venv/bin/python",
      "args": ["/Users/you/.ami/ami_mcp.py"],
      "env": {{
        "AMI_API_URL": "https://ami-mock-api.onrender.com",
        "AMI_API_KEY": "your-api-key"
      }}
    }}
  }}
}}'>copy</button>
<span class="comment"># <span data-lang="es">Pega esto en claude_desktop_config.json y reinicia el cliente.</span><span data-lang="en">Paste into claude_desktop_config.json and restart the client.</span></span>
<span class="cmd">{{
  <span class="accent">"mcpServers"</span>: {{
    <span class="accent">"ami"</span>: {{
      <span class="accent">"command"</span>: "/Users/you/.ami/.venv/bin/python",
      <span class="accent">"args"</span>: ["/Users/you/.ami/ami_mcp.py"],
      <span class="accent">"env"</span>: {{
        <span class="accent">"AMI_API_URL"</span>: "https://ami-mock-api.onrender.com",
        <span class="accent">"AMI_API_KEY"</span>: "&lt;your-api-key&gt;"
      }}
    }}
  }}
}}</span>
          </div>
        </div>
      </div>

      <p class="qs-foot">
        <span data-lang="es">El one-liner instala todo (repo, Python venv, dependencias) y deja el MCP server listo. Después pega el bloque de <code>Claude config</code> en tu <code>claude_desktop_config.json</code> y reinicia: las 11 tools <code>ami.*</code> aparecerán en tu agente.</span>
        <span data-lang="en">The one-liner installs everything (repo, Python venv, dependencies) and leaves the MCP server ready. Then paste the <code>Claude config</code> block into your <code>claude_desktop_config.json</code> and restart: the 11 <code>ami.*</code> tools will appear in your agent.</span>
      </p>

      <!-- Try in browser ============================================ -->
      <div class="try-block">
        <div class="try-head">
          <div>
            <div class="try-title" data-lang="es">¿Sin tiempo para instalar? Pruébalo en el navegador.</div>
            <div class="try-title" data-lang="en">No time to install? Try it in your browser.</div>
            <div class="try-sub" data-lang="es">Ejecuta el flujo completo —solicitud, oferta, contrato, firma, activación— sin escribir una línea.</div>
            <div class="try-sub" data-lang="en">Run the full flow —request, offer, contract, signature, activation— without writing a line.</div>
          </div>
          <button id="tryBtn" class="btn btn-primary try-btn" type="button">
            <span data-lang="es">Probar ahora</span>
            <span data-lang="en">Try me</span>
          </button>
        </div>

        <div id="tryPanel" class="try-panel" hidden>
          <div class="terminal-card">
            <div class="bar">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              <span class="title-bar">demo.session — end-to-end provisioning</span>
            </div>
            <div class="try-body" id="tryBody">
              <div class="try-status" id="tryStatus"></div>
              <div class="try-log" id="tryLog"></div>
              <div class="try-result" id="tryResult" hidden></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- WHAT IS THE PROTOCOL ============================================== -->
  <section id="protocol">
    <div class="wrap">
      <div class="eyebrow" data-lang="es">El protocolo</div>
      <div class="eyebrow" data-lang="en">The protocol</div>
      <h2>
        <span data-lang="es">Cuatro principios. Una capa estándar.</span>
        <span data-lang="en">Four principles. One standard layer.</span>
      </h2>
      <p class="sub">
        <span data-lang="es">AMI separa el "cómo se contrata identidad móvil" del "qué operador la sirve". El agente habla un solo protocolo; el operador se enchufa por debajo.</span>
        <span data-lang="en">AMI separates "how mobile identity is contracted" from "which operator serves it". The agent speaks one protocol; the carrier plugs in underneath.</span>
      </p>

      <div class="bento">
        <div class="cell">
          <h3><span class="icon">01</span>
            <span data-lang="es">Contratación programable</span>
            <span data-lang="en">Programmable contracting</span>
          </h3>
          <p data-lang="es">Solicitud, oferta, datos del cliente, contrato y firma como llamadas de tool. Sin formularios. Sin humanos atascando el flujo.</p>
          <p data-lang="en">Request, offer, customer data, contract and signature as tool calls. No forms. No humans bottlenecking the flow.</p>
        </div>
        <div class="cell">
          <h3><span class="icon">02</span>
            <span data-lang="es">Identidad gobernada</span>
            <span data-lang="en">Governed identity</span>
          </h3>
          <p data-lang="es">Cada número tiene propietario, contrato firmado, política y trazabilidad. Sin shadow IT. Sin números fantasma operando en nombre de empresas.</p>
          <p data-lang="en">Every number has an owner, signed contract, policy and audit trail. No shadow IT. No phantom numbers operating on behalf of companies.</p>
        </div>
        <div class="cell">
          <h3><span class="icon">03</span>
            <span data-lang="es">Multi-operador por diseño</span>
            <span data-lang="en">Multi-carrier by design</span>
          </h3>
          <p data-lang="es">El agente consume AMI; debajo se elige operador, BSP o gateway. Cambias de proveedor sin tocar la integración del agente.</p>
          <p data-lang="en">The agent consumes AMI; underneath you pick carrier, BSP or gateway. Swap providers without touching the agent integration.</p>
        </div>
        <div class="cell">
          <h3><span class="icon">04</span>
            <span data-lang="es">Auditoría por defecto</span>
            <span data-lang="en">Audit-first</span>
          </h3>
          <p data-lang="es">Cada transición de estado emite un AuditEvent. Quién, qué, cuándo, con qué payload. Compliance no es una capa extra — es el modelo.</p>
          <p data-lang="en">Every state transition emits an AuditEvent. Who, what, when, with what payload. Compliance isn't an extra layer — it's the model.</p>
        </div>
      </div>
    </div>
  </section>

  <!-- HOW IT WORKS ====================================================== -->
  <section id="how">
    <div class="wrap">
      <div class="eyebrow" data-lang="es">El flujo</div>
      <div class="eyebrow" data-lang="en">The flow</div>
      <h2>
        <span data-lang="es">De solicitud a línea activa, sin humano en medio.</span>
        <span data-lang="en">From request to active line, no human in the loop.</span>
      </h2>
      <p class="sub">
        <span data-lang="es">El agente recorre la máquina de estados completa por MCP. La firma sucede en el navegador del firmante (o vía webhook de proveedor de firma); el resto es máquina.</span>
        <span data-lang="en">The agent walks the full state machine via MCP. The signature happens in the signer's browser (or via signature provider webhook); everything else is machine-to-machine.</span>
      </p>

      <div class="flow">
<pre><span class="hl">Agent</span>  ─┐  MCP tools (ami.*)
        ▼
<span class="hl">ami_mcp.py</span>      stdio  +  streamable-http
        │  HTTPS + Bearer
        ▼
<span class="hl">ami_api.py</span>      REST v1  ·  state machine  ·  audit log
        │
        ├──▶ <span class="hl2">SIMRequest</span>         requested
        ├──▶ <span class="hl2">Offer</span>              offer_created → offer_accepted
        ├──▶ <span class="hl2">Customer</span>           customer_data_submitted
        ├──▶ <span class="hl2">Contract</span>           signature_pending
        │           ↑  signed via /v1/sign/{{id}} (browser)
        ├──▶ <span class="hl2">Signed</span>             signed
        ├──▶ <span class="hl2">Provisioning</span>       carrier adapter
        └──▶ <span class="hl2">MobileIdentity</span>     active  ·  number  ·  QR</pre>
      </div>
    </div>
  </section>

  <!-- CONNECT =========================================================== -->
  <section id="connect">
    <div class="wrap">
      <div class="eyebrow" data-lang="es">Empezar</div>
      <div class="eyebrow" data-lang="en">Get started</div>
      <h2>
        <span data-lang="es">Tres formas de conectar tu agente.</span>
        <span data-lang="en">Three ways to connect your agent.</span>
      </h2>
      <p class="sub">
        <span data-lang="es">El backend está en producción. Lo único simulado es la SIM física: el resto —oferta, contrato, firma y activación— es real y end-to-end.</span>
        <span data-lang="en">The backend is in production. Only the physical SIM is simulated: the rest —offer, contract, signature, activation— is real and end-to-end.</span>
      </p>

      <div class="connect">
        <div class="card">
          <div class="step-tag">stdio · local</div>
          <h3>Claude Desktop / Code</h3>
          <p data-lang="es">Clona el repo, instala el venv y añade el bloque a tu <code>claude_desktop_config.json</code>. Las 11 tools <code>ami.*</code> aparecen al reiniciar.</p>
          <p data-lang="en">Clone the repo, install the venv and add the block to your <code>claude_desktop_config.json</code>. The 11 <code>ami.*</code> tools appear after restart.</p>
<pre>{html_escape(claude_config)}</pre>
        </div>

        <div class="card">
          <div class="step-tag">streamable-http · remote</div>
          <h3>
            <span data-lang="es">HTTP remoto</span>
            <span data-lang="en">Remote HTTP</span>
          </h3>
          <p data-lang="es">Cualquier cliente MCP que soporte el transporte <code>streamable-http</code> conecta directo. Cero instalación, cero deps locales.</p>
          <p data-lang="en">Any MCP client that supports <code>streamable-http</code> connects directly. Zero install, zero local deps.</p>
<pre>{mcp_url}</pre>
        </div>

        <div class="card">
          <div class="step-tag">REST · custom</div>
          <h3>
            <span data-lang="es">Cliente propio</span>
            <span data-lang="en">Build your own</span>
          </h3>
          <p data-lang="es">¿No usas MCP? Habla directo a la REST API. Esquema OpenAPI 3.1 publicado en <a href="/openapi.json">/openapi.json</a>. Auth con Bearer.</p>
          <p data-lang="en">Not using MCP? Talk directly to the REST API. OpenAPI 3.1 schema at <a href="/openapi.json">/openapi.json</a>. Bearer auth.</p>
<pre>curl -H "Authorization: Bearer $AMI_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"country":"ES"}}' \\
  https://&lt;host&gt;/v1/sim-requests</pre>
        </div>
      </div>
    </div>
  </section>

  <!-- TOOLS ============================================================= -->
  <section id="tools">
    <div class="wrap">
      <div class="eyebrow">MCP</div>
      <h2>
        <span data-lang="es">11 tools. Un namespace.</span>
        <span data-lang="en">11 tools. One namespace.</span>
      </h2>
      <p class="sub">
        <span data-lang="es">Todas en <code>ami.*</code>. Cada una mapea a un endpoint REST equivalente; el agente puede usar la que prefiera sin perder semántica.</span>
        <span data-lang="en">All under <code>ami.*</code>. Each maps to an equivalent REST endpoint; the agent can use whichever it prefers without losing semantics.</span>
      </p>
      <div class="tools-grid">{tools_html}</div>
    </div>
  </section>

  <!-- API =============================================================== -->
  <section id="api">
    <div class="wrap">
      <div class="eyebrow">REST</div>
      <h2>
        <span data-lang="es">API estable, JSON, OpenAPI 3.1.</span>
        <span data-lang="en">Stable JSON API, OpenAPI 3.1.</span>
      </h2>
      <p class="sub">
        <span data-lang="es">Auth con <code>Authorization: Bearer $AMI_API_KEY</code> excepto en healthcheck y la página pública de firma.</span>
        <span data-lang="en">Auth with <code>Authorization: Bearer $AMI_API_KEY</code> except for healthcheck and the public signature page.</span>
      </p>
      <ul class="endpoint-list">{endpoints_html}</ul>
    </div>
  </section>

  <!-- FINAL CTA ========================================================= -->
  <section class="final-cta">
    <div class="wrap">
      <h2>
        <span data-lang="es">Identidad móvil es la primitiva<br>que faltaba para los agentes.</span>
        <span data-lang="en">Mobile identity is the missing<br>primitive for agents.</span>
      </h2>
      <div class="cta-row" style="justify-content:center;">
        <a class="btn btn-primary" href="{repo}">
          <span data-lang="es">Empezar en GitHub →</span>
          <span data-lang="en">Get started on GitHub →</span>
        </a>
        <a class="btn btn-secondary" href="/llms.txt">
          <span data-lang="es">Para agentes: llms.txt</span>
          <span data-lang="en">For agents: llms.txt</span>
        </a>
      </div>
    </div>
  </section>

  <footer>
    <div class="wrap">
      <div>
        <span data-lang="es">Parallax IEI · AMI v1.0 · implementación de referencia</span>
        <span data-lang="en">Parallax IEI · AMI v1.0 · reference implementation</span>
      </div>
      <div class="links">
        <a href="{repo}">GitHub</a>
        <a href="/openapi.json">openapi.json</a>
        <a href="/llms.txt">llms.txt</a>
        <a href="/v1/health">status</a>
      </div>
    </div>
  </footer>

<script>
  // Language toggle ------------------------------------------------------
  (function() {{
    var btn = document.getElementById('langToggle');
    function sync() {{
      var cur = document.documentElement.lang || 'es';
      btn.textContent = cur === 'es' ? 'EN' : 'ES';
    }}
    btn.addEventListener('click', function() {{
      var cur = document.documentElement.lang || 'es';
      var next = cur === 'es' ? 'en' : 'es';
      document.documentElement.lang = next;
      try {{ localStorage.setItem('ami-lang', next); }} catch(e) {{}}
      sync();
    }});
    sync();
  }})();

  // Quick Start tabs -----------------------------------------------------
  document.querySelectorAll('.tab').forEach(function(tab) {{
    tab.addEventListener('click', function() {{
      var target = tab.getAttribute('data-pane');
      document.querySelectorAll('.tab').forEach(function(t) {{ t.classList.toggle('active', t === tab); }});
      document.querySelectorAll('.pane').forEach(function(p) {{ p.classList.toggle('active', p.id === target); }});
    }});
  }});

  // Try in browser -------------------------------------------------------
  (function() {{
    var btn    = document.getElementById('tryBtn');
    var panel  = document.getElementById('tryPanel');
    var status = document.getElementById('tryStatus');
    var log    = document.getElementById('tryLog');
    var result = document.getElementById('tryResult');
    if (!btn) return;

    var labelByLang = {{
      es: {{
        idle:    'Probar ahora',
        running: 'Ejecutando…',
        done:    'Volver a probar',
        statusRunning: 'Ejecutando flujo end-to-end…',
        statusOk:      'Flujo completado',
        statusErr:     'Error',
        phoneLbl:      'Número activo',
        midLbl:        'mobile_identity_id',
        viewIdentity:  'Ver identidad pública →',
        viewSign:      'Ver contrato firmado →',
      }},
      en: {{
        idle:    'Try me',
        running: 'Running…',
        done:    'Run again',
        statusRunning: 'Running end-to-end flow…',
        statusOk:      'Flow completed',
        statusErr:     'Error',
        phoneLbl:      'Active number',
        midLbl:        'mobile_identity_id',
        viewIdentity:  'View public identity →',
        viewSign:      'View signed contract →',
      }},
    }};
    function L() {{ return labelByLang[document.documentElement.lang === 'en' ? 'en' : 'es']; }}

    function setBtn(state) {{
      var labels = L();
      btn.querySelector('[data-lang="es"]').textContent = labelByLang.es[state];
      btn.querySelector('[data-lang="en"]').textContent = labelByLang.en[state];
      btn.disabled = (state === 'running');
    }}

    function fmtTs(ms) {{
      var s = (ms / 1000).toFixed(2);
      return '+' + s + 's';
    }}

    btn.addEventListener('click', async function() {{
      var labels = L();
      setBtn('running');
      panel.hidden = false;
      status.className = 'try-status';
      status.textContent = labels.statusRunning;
      log.innerHTML = '';
      result.hidden = true;

      var t0 = performance.now();
      try {{
        var res = await fetch('/v1/demo/quick', {{ method: 'POST' }});
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var data = await res.json();
        if (!data.ok) throw new Error(data.error || 'demo_failed');

        // Render steps with simulated progressive timestamps
        var steps = data.steps || [];
        var total = data.elapsed_ms || (performance.now() - t0);
        steps.forEach(function(s, i) {{
          var row = document.createElement('div');
          var isLast = (i === steps.length - 1);
          row.className = 'row' + (isLast ? ' ok' : '');
          var tsApprox = (total * (i + 1)) / steps.length;
          var stepTxt  = s.step.replace(/_/g, ' ');
          var idTxt    = s.id || '';
          row.innerHTML =
            '<span class="ts">' + fmtTs(tsApprox) + '</span>' +
            '<span class="step">' + stepTxt + '</span>' +
            '<span class="id">' + idTxt + '</span>';
          log.appendChild(row);
        }});

        var L2 = L();
        status.className = 'try-status ok';
        status.textContent = L2.statusOk + ' · ' + (data.elapsed_ms || 0) + ' ms';

        var mid = data.mobile_identity || {{}};
        result.innerHTML =
          '<div class="label">' + L2.phoneLbl + '</div>' +
          '<div class="phone">' + (mid.phone_number || '') + '</div>' +
          '<div class="label" style="margin-top:0.8rem;">' + L2.midLbl + '</div>' +
          '<div class="mid-id">' + (mid.id || '') + '</div>' +
          '<div class="links">' +
            '<a href="/identity/' + encodeURIComponent(mid.id || '') + '">' + L2.viewIdentity + '</a>' +
            '<a href="/v1/sign/' + encodeURIComponent(mid.contract_id || '') + '">' + L2.viewSign + '</a>' +
          '</div>';
        result.hidden = false;
        setBtn('done');
      }} catch (err) {{
        var L3 = L();
        status.className = 'try-status error';
        status.textContent = L3.statusErr + ': ' + (err.message || err);
        setBtn('idle');
      }}
    }});
  }})();

  // Copy buttons ---------------------------------------------------------
  document.querySelectorAll('.copy-btn').forEach(function(btn) {{
    btn.addEventListener('click', async function() {{
      var text = btn.getAttribute('data-copy');
      try {{
        await navigator.clipboard.writeText(text);
      }} catch(e) {{
        // fallback: select + execCommand for older browsers
        var ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        try {{ document.execCommand('copy'); }} catch(_) {{}}
        document.body.removeChild(ta);
      }}
      btn.classList.add('copied');
      var orig = btn.textContent; btn.textContent = 'copied';
      setTimeout(function() {{ btn.classList.remove('copied'); btn.textContent = orig; }}, 1400);
    }});
  }});
</script>
</body>
</html>"""


def _qr_grid_for(seed):
    """Genera un QR fake determinístico 21x21 a partir de un id (sin deps).

    No es un QR válido — es un patrón visual estilizado para la maqueta pública.
    """
    import hashlib
    h = hashlib.sha256(seed.encode()).digest()
    # Repetimos el hash hasta llenar 21*21 = 441 bits.
    bits = []
    while len(bits) < 441:
        for byte in h:
            for k in range(8):
                bits.append((byte >> k) & 1)
        h = hashlib.sha256(h).digest()
    bits = bits[:441]
    cells = []
    for r in range(21):
        for c in range(21):
            on = bits[r * 21 + c]
            # Marcadores de esquina (estilo QR real, puramente decorativo)
            in_corner = (
                (r < 7 and c < 7) or
                (r < 7 and c >= 14) or
                (r >= 14 and c < 7)
            )
            if in_corner:
                rr = r if r < 7 else r - 14
                cc = c if c < 7 else c - 14
                edge   = (rr == 0 or rr == 6 or cc == 0 or cc == 6)
                center = (2 <= rr <= 4 and 2 <= cc <= 4)
                on = 1 if (edge or center) else 0
            if on:
                cells.append(f'<rect x="{c}" y="{r}" width="1" height="1" fill="currentColor"/>')
    return "".join(cells)


def render_identity_page(identity):
    contract = STATE["contracts"].get(identity.get("contract_id"), {})
    customer = STATE["customers"].get(identity.get("customer_id"), {})
    caps_html = "".join(
        f'<span class="cap-pill">{html_escape(c)}</span>'
        for c in identity.get("capabilities", [])
    ) or '<span class="cap-pill">—</span>'
    qr_cells = _qr_grid_for(identity["id"])
    sign_url = f"/v1/sign/{html_escape(identity.get('contract_id',''))}"

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · {html_escape(identity.get("phone_number","identity"))}</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{LANDING_CSS}
  .id-wrap {{ max-width: 880px; margin: 0 auto; padding: 4rem 1.75rem 5rem; position: relative; }}
  .id-wrap::before {{
    content: ""; position: absolute; pointer-events: none; z-index: 0;
    width: 700px; height: 400px;
    background: radial-gradient(ellipse, #8b6cff 0%, transparent 65%);
    top: -120px; left: 50%; transform: translateX(-50%);
    filter: blur(120px); opacity: 0.20;
  }}
  .id-wrap > * {{ position: relative; z-index: 1; }}
  .id-eyebrow {{
    font-family: var(--mono); font-size: 0.72rem; color: var(--accent);
    letter-spacing: 0.14em; text-transform: uppercase; margin-bottom: 0.85rem;
  }}
  .id-phone {{
    font-family: var(--mono); font-weight: 600;
    font-size: clamp(3rem, 7vw, 5rem); line-height: 1;
    letter-spacing: -0.01em; margin: 0 0 1.2rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 120%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .id-status {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    padding: 0.35rem 0.85rem; border-radius: 999px;
    background: rgba(74, 222, 128, 0.10); border: 1px solid rgba(74, 222, 128, 0.35);
    color: var(--green); font-family: var(--mono); font-size: 0.78rem;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  }}
  .id-status .ldot {{
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 8px var(--green);
    animation: pulse 2s ease-in-out infinite;
  }}
  .id-meta {{ margin-top: 1.4rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .cap-pill {{
    background: var(--surface); border: 1px solid var(--line);
    padding: 0.3rem 0.7rem; border-radius: 999px;
    font-family: var(--mono); font-size: 0.74rem; color: var(--ink-soft);
  }}
  .id-grid {{
    display: grid; grid-template-columns: 1fr 220px; gap: 1.25rem;
    margin-top: 2.5rem;
  }}
  @media (max-width: 720px) {{ .id-grid {{ grid-template-columns: 1fr; }} }}
  .id-card {{
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.4rem 1.5rem;
  }}
  .id-card h3 {{
    margin: 0 0 1rem 0; font-size: 0.78rem; font-weight: 600;
    color: var(--ink-mute); text-transform: uppercase; letter-spacing: 0.1em;
    font-family: var(--mono);
  }}
  .id-card dl {{
    display: grid; grid-template-columns: 9rem 1fr; gap: 0.55rem 1rem; margin: 0;
  }}
  .id-card dt {{ color: var(--ink-mute); font-size: 0.85rem; }}
  .id-card dd {{
    margin: 0; color: var(--ink); font-size: 0.9rem;
    word-break: break-all;
  }}
  .id-card dd.mono {{ font-family: var(--mono); font-size: 0.82rem; color: var(--accent-2); }}
  .id-card dd a {{ color: var(--accent-2); }}
  .qr-card {{
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.2rem;
  }}
  .qr-svg {{
    width: 100%; height: auto; max-width: 180px; aspect-ratio: 1 / 1;
    color: var(--ink); background: var(--bg-soft);
    border-radius: 8px; padding: 10px; box-sizing: border-box;
  }}
  .qr-label {{
    margin-top: 0.7rem; font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-mute); letter-spacing: 0.06em;
  }}
  .id-back {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    color: var(--ink-soft); font-family: var(--mono); font-size: 0.82rem;
  }}
  .id-back:hover {{ color: var(--ink); }}
</style>
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

  <header>
    <div class="wrap">
      <a href="/" class="brand id-back" style="text-decoration:none;">
        {AMI_LOGO_SVG}
        <span>← AMI</span>
      </a>
      <nav>
        <a href="/#protocol" data-lang="es">Protocolo</a>
        <a href="/#protocol" data-lang="en">Protocol</a>
        <a href="/spec">Spec</a>
        <a href="/openapi.json">openapi.json</a>
      </nav>
    </div>
  </header>

  <div class="id-wrap">
    <div class="id-eyebrow">
      <span data-lang="es">Identidad móvil activa</span>
      <span data-lang="en">Active mobile identity</span>
    </div>

    <h1 class="id-phone">{html_escape(identity.get("phone_number","—"))}</h1>

    <div>
      <span class="id-status"><span class="ldot"></span>{html_escape(identity.get("status","active"))}</span>
    </div>

    <div class="id-meta">
      <span class="cap-pill" style="color:var(--accent);border-color:rgba(139,108,255,0.4);">{html_escape(identity.get("sim_type","—"))}</span>
      {caps_html}
    </div>

    <div class="id-grid">
      <div class="id-card">
        <h3>
          <span data-lang="es">Detalles de la identidad</span>
          <span data-lang="en">Identity details</span>
        </h3>
        <dl>
          <dt data-lang="es">Identidad</dt><dt data-lang="en">Identity</dt>
          <dd class="mono">{html_escape(identity["id"])}</dd>

          <dt data-lang="es">Activación</dt><dt data-lang="en">Activation</dt>
          <dd class="mono">{html_escape(identity.get("provider_activation_id","—"))}</dd>

          <dt data-lang="es">Contrato</dt><dt data-lang="en">Contract</dt>
          <dd><a href="{sign_url}">{html_escape(identity.get("contract_id","—"))}</a></dd>

          <dt data-lang="es">Cliente</dt><dt data-lang="en">Customer</dt>
          <dd>{html_escape(customer.get("legal_name","—"))}</dd>

          <dt data-lang="es">NIF/Tax ID</dt><dt data-lang="en">Tax ID</dt>
          <dd class="mono">{html_escape(customer.get("tax_id","—"))}</dd>

          <dt data-lang="es">Activado</dt><dt data-lang="en">Activated</dt>
          <dd class="mono">{html_escape(identity.get("activated_at","—"))}</dd>
        </dl>
      </div>

      <div class="qr-card">
        <svg class="qr-svg" viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg" aria-label="eSIM QR placeholder">
          {qr_cells}
        </svg>
        <div class="qr-label">eSIM QR</div>
      </div>
    </div>
  </div>

  <footer>
    <div class="wrap">
      <div>
        <span data-lang="es">Parallax IEI · AMI v1.0 · identidad móvil activa</span>
        <span data-lang="en">Parallax IEI · AMI v1.0 · active mobile identity</span>
      </div>
      <div class="links">
        <a href="/spec" data-lang="es">Protocolo</a>
        <a href="/spec" data-lang="en">Protocol</a>
        <a href="/openapi.json">openapi.json</a>
        <a href="/llms.txt">llms.txt</a>
      </div>
    </div>
  </footer>

<script>
  (function() {{
    var seen = {{}};
    document.querySelectorAll('header nav [data-lang]').forEach(function(){{}});
  }})();
</script>
</body>
</html>"""


def render_identity_404():
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>AMI · identidad no encontrada</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<style>{LANDING_CSS}
  .nf {{ max-width: 540px; margin: 6rem auto; padding: 0 1.5rem; text-align: center; }}
  .nf h1 {{ font-size: 2rem; margin: 0 0 0.6rem 0; }}
  .nf p {{ color: var(--ink-soft); }}
</style></head>
<body>
  <div class="nf">
    <h1>404</h1>
    <p>
      <span data-lang="es">No encontramos esa identidad móvil. Puede haber expirado o nunca haber existido.</span>
      <span data-lang="en">We couldn't find that mobile identity. It may have expired or never existed.</span>
    </p>
    <p><a href="/">← AMI</a></p>
  </div>
</body></html>"""


_HEADING_RE     = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")
_OL_RE          = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_UL_RE          = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_BLOCKQUOTE_RE  = re.compile(r"^>\s?(.*)$")
_CODE_FENCE_RE  = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
_LINK_RE        = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE        = re.compile(r"\*\*([^*]+)\*\*")
_ITAL_RE        = re.compile(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _slugify_heading(s):
    s = re.sub(r"[^\w\s-]", "", s.lower(), flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip())
    return s or "section"


def _md_inline(text):
    """Aplica reemplazos inline (preservando code spans intactos)."""
    # Extraer code inline a placeholders para evitar que formatten su contenido.
    spans = []
    def stash(m):
        spans.append(m.group(1))
        return f"\x00CODE{len(spans)-1}\x00"
    text = _INLINE_CODE_RE.sub(stash, text)
    # Escape HTML del resto.
    text = html_escape(text)
    # Links: [text](url)
    def linkify(m):
        label = m.group(1)
        url = m.group(2)
        # url ya está escapado porque pasó por html_escape
        return f'<a href="{url}">{label}</a>'
    text = _LINK_RE.sub(linkify, text)
    # Bold y italic
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITAL_RE.sub(r"<em>\1</em>", text)
    # Restaurar code spans (con su propio escape).
    def unstash(m):
        idx = int(m.group(1))
        return f"<code>{html_escape(spans[idx])}</code>"
    text = re.sub(r"\x00CODE(\d+)\x00", unstash, text)
    return text


def render_markdown(md):
    """Mini-parser markdown → HTML en stdlib. Devuelve (html, toc_items).

    Soporta: H1-H4, párrafos, listas (- y 1.), code blocks ```, code inline `,
    links [t](u), blockquote >, bold **, italic *. Sin tablas/footnotes.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    toc = []  # list of (level, text, slug) — usaremos level 2 para TOC sticky
    i = 0
    n = len(lines)
    used_slugs = {}

    def push_paragraph(buf):
        if not buf: return
        text = " ".join(s.strip() for s in buf).strip()
        if text:
            out.append(f"<p>{_md_inline(text)}</p>")

    def push_list(items, ordered):
        if not items: return
        tag = "ol" if ordered else "ul"
        out.append(f"<{tag}>")
        for it in items:
            out.append(f"<li>{_md_inline(it)}</li>")
        out.append(f"</{tag}>")

    def push_blockquote(buf):
        if not buf: return
        text = "<br>".join(_md_inline(b) for b in buf)
        out.append(f"<blockquote>{text}</blockquote>")

    para_buf = []
    list_items = []
    list_ordered = None
    bq_buf = []

    def flush_all():
        nonlocal para_buf, list_items, list_ordered, bq_buf
        push_paragraph(para_buf); para_buf = []
        push_list(list_items, bool(list_ordered)); list_items = []; list_ordered = None
        push_blockquote(bq_buf); bq_buf = []

    while i < n:
        line = lines[i]

        # Code fence
        m = _CODE_FENCE_RE.match(line)
        if m:
            flush_all()
            lang = m.group(1)
            i += 1
            code_lines = []
            while i < n and not _CODE_FENCE_RE.match(lines[i]):
                code_lines.append(lines[i]); i += 1
            i += 1  # skip closing fence (or EOF)
            code_html = html_escape("\n".join(code_lines))
            cls = f' class="lang-{html_escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{code_html}</code></pre>")
            continue

        # Horizontal rule
        if re.match(r"^\s*(?:---+|\*\*\*+|___+)\s*$", line):
            flush_all()
            out.append("<hr>")
            i += 1
            continue

        # Heading
        m = _HEADING_RE.match(line)
        if m:
            flush_all()
            level = len(m.group(1))
            text = m.group(2)
            slug = _slugify_heading(re.sub(r"[`*_]", "", text))
            base = slug; k = 2
            while slug in used_slugs:
                slug = f"{base}-{k}"; k += 1
            used_slugs[slug] = True
            inner = _md_inline(text)
            out.append(f'<h{level} id="{slug}">{inner}</h{level}>')
            if level == 2:
                toc.append((level, text, slug))
            i += 1
            continue

        # Blockquote
        m = _BLOCKQUOTE_RE.match(line)
        if m:
            push_paragraph(para_buf); para_buf = []
            push_list(list_items, bool(list_ordered)); list_items = []; list_ordered = None
            bq_buf.append(m.group(1))
            i += 1
            continue
        elif bq_buf and not line.strip():
            push_blockquote(bq_buf); bq_buf = []
            i += 1
            continue

        # Lists
        m_ol = _OL_RE.match(line)
        m_ul = _UL_RE.match(line)
        if m_ol or m_ul:
            push_paragraph(para_buf); push_blockquote(bq_buf)
            para_buf = []; bq_buf = []
            ordered = bool(m_ol)
            text = (m_ol.group(3) if m_ol else m_ul.group(2))
            if list_ordered is None:
                list_ordered = ordered
            elif list_ordered != ordered:
                push_list(list_items, list_ordered); list_items = []
                list_ordered = ordered
            list_items.append(text)
            i += 1
            continue
        elif list_items and not line.strip():
            push_list(list_items, bool(list_ordered)); list_items = []; list_ordered = None
            i += 1
            continue

        # Blank line: flush paragraph
        if not line.strip():
            push_paragraph(para_buf); para_buf = []
            i += 1
            continue

        # Default: paragraph buffer
        para_buf.append(line)
        i += 1

    flush_all()
    return "\n".join(out), toc


def render_spec_page():
    body_html, toc = render_markdown(SPEC_MD)
    toc_html = "".join(
        f'<li><a href="#{slug}">{html_escape(text)}</a></li>'
        for _level, text, slug in toc
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Spec del protocolo</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{LANDING_CSS}
  .doc-wrap {{
    display: grid; grid-template-columns: 240px minmax(0, 760px);
    gap: 3rem; max-width: 1100px; margin: 0 auto;
    padding: 3.5rem 1.75rem 5rem;
  }}
  @media (max-width: 880px) {{ .doc-wrap {{ grid-template-columns: 1fr; gap: 1rem; }} .doc-toc {{ display: none; }} }}
  .doc-toc {{
    position: sticky; top: 5rem; align-self: start;
    max-height: calc(100vh - 7rem); overflow-y: auto;
    padding-right: 0.5rem;
  }}
  .doc-toc h4 {{
    font-size: 0.7rem; font-family: var(--mono); letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--ink-mute); margin: 0 0 0.8rem 0;
    font-weight: 600;
  }}
  .doc-toc ol {{ list-style: none; padding: 0; margin: 0; counter-reset: tocitem; }}
  .doc-toc li {{
    counter-increment: tocitem; margin: 0; padding: 0;
    border-left: 1px solid var(--line-soft);
  }}
  .doc-toc a {{
    display: block; padding: 0.35rem 0 0.35rem 0.85rem;
    font-size: 0.82rem; color: var(--ink-soft);
    line-height: 1.35; transition: color 0.12s, border-color 0.12s;
    border-left: 2px solid transparent; margin-left: -1px;
  }}
  .doc-toc a:hover {{ color: var(--ink); border-left-color: var(--accent); }}
  .doc {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", sans-serif;
    color: var(--ink); font-size: 1.05rem; line-height: 1.75;
  }}
  .doc h1 {{
    font-size: clamp(2rem, 4vw, 2.8rem); font-weight: 700;
    letter-spacing: -0.02em; line-height: 1.15;
    margin: 0 0 1.5rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .doc h2 {{
    font-size: 1.6rem; font-weight: 600; letter-spacing: -0.01em;
    margin: 3rem 0 1rem 0; padding-top: 0.5rem;
    border-top: 1px solid var(--line);
    color: var(--ink);
  }}
  .doc h3 {{
    font-size: 1.18rem; font-weight: 600; margin: 2.2rem 0 0.7rem 0;
    color: var(--ink);
  }}
  .doc h4 {{
    font-size: 1rem; font-weight: 600; margin: 1.6rem 0 0.5rem 0;
    color: var(--ink-soft);
  }}
  .doc p {{ margin: 0 0 1.1rem 0; color: var(--ink); }}
  .doc strong {{ color: #fff; font-weight: 600; }}
  .doc em {{ color: var(--ink); font-style: italic; }}
  .doc ul, .doc ol {{ margin: 0 0 1.2rem 0; padding-left: 1.4rem; color: var(--ink); }}
  .doc li {{ margin: 0.25rem 0; }}
  .doc li > code {{ background: var(--surface); }}
  .doc a {{ color: var(--accent-2); text-decoration: underline; text-decoration-color: rgba(93, 209, 255, 0.3); }}
  .doc a:hover {{ text-decoration-color: var(--accent-2); }}
  .doc blockquote {{
    margin: 1.25rem 0; padding: 0.85rem 1.2rem;
    border-left: 3px solid var(--accent);
    background: var(--accent-bg); border-radius: 0 8px 8px 0;
    color: var(--ink); font-style: italic;
  }}
  .doc blockquote p {{ margin: 0; }}
  .doc hr {{
    border: 0; border-top: 1px solid var(--line);
    margin: 2.5rem 0;
  }}
  .doc pre {{
    background: var(--code-bg); border: 1px solid var(--line);
    border-radius: 8px; padding: 1rem 1.2rem;
    font-family: var(--mono); font-size: 0.85rem; line-height: 1.6;
    overflow-x: auto; margin: 1.2rem 0; color: var(--ink);
  }}
  .doc code {{
    font-family: var(--mono); font-size: 0.9em;
  }}
  .doc p code, .doc li code, .doc h2 code, .doc h3 code, .doc h4 code {{
    background: var(--surface); border: 1px solid var(--line);
    padding: 0.1em 0.4em; border-radius: 4px;
    color: var(--accent-2); font-size: 0.88em;
  }}
  .doc pre code {{
    background: transparent; border: 0; padding: 0; color: inherit;
  }}
</style>
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

  <header>
    <div class="wrap">
      <a href="/" class="brand" style="text-decoration:none;color:inherit;">
        {AMI_LOGO_SVG} AMI<span class="dot">.</span>
      </a>
      <nav>
        <a href="/">Home</a>
        <a href="/openapi.json">openapi.json</a>
        <a href="/llms.txt">llms.txt</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
      </nav>
    </div>
  </header>

  <div class="doc-wrap">
    <aside class="doc-toc">
      <h4>
        <span data-lang="es">Índice</span>
        <span data-lang="en">Contents</span>
      </h4>
      <ol>{toc_html}</ol>
    </aside>
    <article class="doc">
      {body_html}
    </article>
  </div>

  <footer>
    <div class="wrap">
      <div>
        <span data-lang="es">Parallax IEI · AMI v1.0 · spec del protocolo</span>
        <span data-lang="en">Parallax IEI · AMI v1.0 · protocol spec</span>
      </div>
      <div class="links">
        <a href="/">Home</a>
        <a href="/openapi.json">openapi.json</a>
        <a href="/llms.txt">llms.txt</a>
      </div>
    </div>
  </footer>

</body>
</html>"""


def render_llms_txt():
    """Formato emergente https://llmstxt.org — índice corto en markdown para LLMs."""
    tool_lines = "\n".join(f"- {n}: {d}" for n, d in _tools_for_landing())
    endpoint_lines = "\n".join(f"- {v} {p} — {d}" for v, p, d in _endpoints_for_landing())
    return f"""# AMI — Agent Mobile Identity Protocol

> Protocolo estándar para que un agente AI solicite, contrate y active una identidad móvil
> (SIM, eSIM o número de teléfono). v1 cubre el flujo de contratación y aprovisionamiento;
> la operación (llamadas, SMS, WhatsApp) llega en v2.

AMI expone un MCP server con 11 tools y una REST API JSON. El agente recorre:
solicitud → oferta → datos cliente → contrato → firma → MobileIdentity activa.
Lo único simulado actualmente es la SIM física; el resto del flujo es real.

## Conexión

- MCP HTTP remoto: {MCP_HTTP_URL}
- MCP stdio (instalable local): clonar {REPO_URL} y configurar Claude Desktop.
- Auth REST: header `Authorization: Bearer <AMI_API_KEY>`.

## Tools MCP (namespace ami.*)

{tool_lines}

## Endpoints REST

{endpoint_lines}

## Documentación

- Repo y README: {REPO_URL}
- Spec completa (markdown en GitHub): {REPO_URL}/blob/main/docs/SPEC.md
- Spec renderizada en HTML: /spec
- OpenAPI 3.1: /openapi.json
- Identidad móvil pública (ejemplo): /identity/<mobile_identity_id>
- Demo end-to-end sin auth: POST /v1/demo/quick
"""


def render_openapi():
    """Spec OpenAPI 3.1 mínima pero correcta de los endpoints v1."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AMI — Agent Mobile Identity Protocol",
            "version": "1.0.0",
            "description": "API REST para contratación y aprovisionamiento de identidad móvil para agentes AI.",
            "contact": {"url": REPO_URL},
        },
        "servers": [{"url": "https://ami-mock-api.onrender.com", "description": "Mock público"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "AMI_API_KEY"}
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/v1/health":                                 {"get":  {"summary": "Healthcheck", "security": [], "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-options":                            {"get":  {"summary": "Países y SIMs disponibles", "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-requests":                           {"post": {"summary": "Crear SIMRequest + oferta", "responses": {"201": {"description": "Created"}}}},
            "/v1/sim-requests/{id}":                      {"get":  {"summary": "Obtener SIMRequest", "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-requests/{id}/cancel":               {"post": {"summary": "Cancelar SIMRequest", "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-requests/{id}/customer-data":        {"post": {"summary": "Adjuntar datos del cliente", "responses": {"201": {"description": "Created"}}}},
            "/v1/offers/{id}":                            {"get":  {"summary": "Obtener oferta", "responses": {"200": {"description": "OK"}}}},
            "/v1/offers/{id}/accept":                     {"post": {"summary": "Aceptar oferta", "responses": {"200": {"description": "OK"}}}},
            "/v1/customers":                              {"post": {"summary": "Crear cliente suelto", "responses": {"201": {"description": "Created"}}}},
            "/v1/customers/{id}":                         {"get":  {"summary": "Obtener cliente", "responses": {"200": {"description": "OK"}}}},
            "/v1/contracts":                              {"post": {"summary": "Crear contrato + signature_url", "responses": {"201": {"description": "Created"}}}},
            "/v1/contracts/{id}":                         {"get":  {"summary": "Obtener contrato", "responses": {"200": {"description": "OK"}}}},
            "/v1/contracts/{id}/mock-sign":               {"post": {"summary": "Firma directa (atajo programático)", "responses": {"200": {"description": "OK"}}}},
            "/v1/sign/{id}":                              {"get":  {"summary": "Página HTML de firma", "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/v1/sign/{id}/confirm":                      {"post": {"summary": "Callback de firma desde el form", "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/v1/mobile-identities/activate":             {"post": {"summary": "Activar MobileIdentity", "responses": {"201": {"description": "Created"}}}},
            "/v1/mobile-identities/{id}":                 {"get":  {"summary": "Obtener MobileIdentity", "responses": {"200": {"description": "OK"}}}},
            "/v1/events":                                 {"get":  {"summary": "Últimos AuditEvents", "responses": {"200": {"description": "OK"}}}},
            "/v1/demo/quick":                             {"post": {"summary": "Demo end-to-end (público, sin auth)", "security": [], "responses": {"200": {"description": "OK"}}}},
            "/identity/{id}":                             {"get":  {"summary": "Página pública de MobileIdentity", "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/spec":                                      {"get":  {"summary": "Spec del protocolo en HTML", "security": [], "responses": {"200": {"description": "HTML"}}}},
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("AMI", self.address_string(), fmt % args)

    def do_OPTIONS(self): response(self, 200, {"ok": True})

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        p = urlparse(self.path).path
        if not is_public("GET", p) and not check_auth(self):
            return response(self, 401, {"error": "unauthorized"})
        if p == "/" or p == "/index.html":
            return respond_html(self, 200, render_landing())
        if p == "/llms.txt":
            return respond_text(self, 200, render_llms_txt())
        if p == "/openapi.json":
            return response(self, 200, render_openapi())
        if p == "/install.sh":
            return respond_text(self, 200, INSTALL_SH, content_type="text/x-shellscript; charset=utf-8")
        if p == "/favicon.ico":
            return respond_text(self, 204, "", content_type="image/x-icon")
        if p == "/v1/health":
            return response(self, 200, {"ok": True, "service": "ami-mock", "time": now()})
        if p == "/v1/sim-options":
            return response(self, 200, {"countries": COUNTRIES})
        if p == "/v1/events":
            return response(self, 200, {"events": STATE["events"][-100:]})
        # Página HTML de firma (público; no requiere API key)
        m = re.match(r"^/v1/sign/([^/]+)$", p)
        if m:
            contract = STATE["contracts"].get(m.group(1))
            if not contract:
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            return respond_html(self, 200, render_sign_page(contract))
        # Página pública de identidad móvil
        m = re.match(r"^/identity/([^/]+)$", p)
        if m:
            identity = STATE["mobile_identities"].get(m.group(1))
            if not identity:
                return respond_html(self, 404, render_identity_404())
            return respond_html(self, 200, render_identity_page(identity))
        # Spec del protocolo renderizada como HTML
        if p == "/spec":
            return respond_html(self, 200, render_spec_page())
        m = re.match(r"^/v1/(sim-requests|offers|customers|contracts|mobile-identities)/([^/]+)$", p)
        if m:
            table = m.group(1).replace("-", "_")
            obj = STATE.get(table, {}).get(m.group(2))
            if not obj: return response(self, 404, {"error": "not_found", "id": m.group(2)})
            return response(self, 200, obj)
        return response(self, 404, {"error": "unknown_route", "path": p})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        p = urlparse(self.path).path
        if not is_public("POST", p) and not check_auth(self):
            return response(self, 401, {"error": "unauthorized"})

        # Callback de firma (HTML form). Antes del read_json porque es form-urlencoded.
        m = re.match(r"^/v1/sign/([^/]+)/confirm$", p)
        if m:
            code, body = sign_contract(m.group(1))
            if code == 404:
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            # En ambos casos (recién firmado o ya firmado) renderizamos la página actualizada.
            contract = body if code == 200 else body["contract"]
            return respond_html(self, 200, render_sign_page(contract))

        # Demo público end-to-end (sin API key): orquesta todo el flujo.
        if p == "/v1/demo/quick":
            try:
                return response(self, 200, run_quick_demo())
            except Exception as e:
                return response(self, 500, {"ok": False, "error": "demo_failed", "detail": str(e)})

        try: data = read_json(self)
        except Exception as e: return response(self, 400, {"error": "invalid_json", "detail": str(e)})

        # Crear SIMRequest + oferta inmediata (mock telco)
        if p == "/v1/sim-requests":
            country = data.get("country", "ES")
            if country not in COUNTRIES:
                return response(self, 400, {"error": "unsupported_country", "country": country})
            req_id = new_id("simreq")
            req = {
                "id": req_id, "status": "requested", "country": country,
                "sim_type": data.get("sim_type", "eSIM"),
                "capabilities": data.get("capabilities", []),
                "agent": data.get("agent", {}),
                "commercial_constraints": data.get("commercial_constraints", {}),
                "created_at": now(), "updated_at": now(),
            }
            STATE["sim_requests"][req_id] = req
            event("sim_request_created", "sim_request", req_id, req)

            offer_id = new_id("offer")
            price = COUNTRIES[country]["price"]
            offer = {
                "id": offer_id, "sim_request_id": req_id, "status": "offer_created",
                "country": country, "sim_type": req["sim_type"],
                "capabilities": req["capabilities"] or ["sms", "voice"],
                "monthly_price": price["monthly"], "setup_fee": price["setup"],
                "currency": price["currency"], "requires_contract": True,
                "requires_customer_data": True,
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "created_at": now(),
            }
            STATE["offers"][offer_id] = offer
            req["offer_id"] = offer_id
            transition_sim_request(req, "offer_created")
            event("offer_created", "offer", offer_id, offer)
            return response(self, 201, {"sim_request": req, "offer": offer})

        # Cancelar SIMRequest antes de activación
        m = re.match(r"^/v1/sim-requests/([^/]+)/cancel$", p)
        if m:
            req = STATE["sim_requests"].get(m.group(1))
            if not req: return response(self, 404, {"error": "sim_request_not_found"})
            if req["status"] in TERMINAL:
                return response(self, 409, {"error": "already_terminal", "status": req["status"]})
            try:
                transition_sim_request(req, "cancelled",
                                       reason=data.get("reason", "cancelled_by_client"))
            except ValueError as e:
                return response(self, 409, {"error": str(e)})
            return response(self, 200, req)

        # Submit customer data: crea cliente + lo vincula a la SIMRequest
        m = re.match(r"^/v1/sim-requests/([^/]+)/customer-data$", p)
        if m:
            req = STATE["sim_requests"].get(m.group(1))
            if not req: return response(self, 404, {"error": "sim_request_not_found"})
            if req["status"] != "offer_accepted":
                return response(self, 409, {
                    "error": "invalid_state",
                    "expected": "offer_accepted", "actual": req["status"],
                })
            payload = data.get("customer", data)
            required = ["legal_name", "tax_id", "billing_email", "address", "representative_name"]
            missing = [f for f in required if not payload.get(f)]
            if missing:
                return response(self, 400, {"error": "missing_fields", "fields": missing})
            cid = new_id("customer")
            customer = {
                "id": cid, "status": "created",
                "legal_name": payload["legal_name"], "tax_id": payload["tax_id"],
                "billing_email": payload["billing_email"], "address": payload["address"],
                "representative_name": payload["representative_name"],
                "created_at": now(),
            }
            STATE["customers"][cid] = customer
            req["customer_id"] = cid
            event("customer_created", "customer", cid, customer)
            transition_sim_request(req, "customer_data_submitted")
            return response(self, 201, {"sim_request": req, "customer": customer})

        # Aceptar oferta
        m = re.match(r"^/v1/offers/([^/]+)/accept$", p)
        if m:
            offer = STATE["offers"].get(m.group(1))
            if not offer: return response(self, 404, {"error": "offer_not_found"})
            if offer["status"] != "offer_created":
                return response(self, 409, {"error": "offer_not_acceptable", "status": offer["status"]})
            offer["status"] = "offer_accepted"; offer["accepted_at"] = now()
            event("offer_accepted", "offer", offer["id"], offer)
            req = STATE["sim_requests"].get(offer["sim_request_id"])
            if req: transition_sim_request(req, "offer_accepted")
            return response(self, 200, offer)

        # Crear customer suelto (compatibilidad / casos sin SIMRequest todavía)
        if p == "/v1/customers":
            cid = new_id("customer")
            customer = {
                "id": cid, "status": "created",
                "legal_name": data.get("legal_name"), "tax_id": data.get("tax_id"),
                "billing_email": data.get("billing_email"), "address": data.get("address"),
                "representative_name": data.get("representative_name"),
                "created_at": now(),
            }
            STATE["customers"][cid] = customer
            event("customer_created", "customer", cid, customer)
            return response(self, 201, customer)

        # Crear contrato (avanza la SIMRequest hasta signature_pending)
        if p == "/v1/contracts":
            offer_id, customer_id = data.get("offer_id"), data.get("customer_id")
            offer = STATE["offers"].get(offer_id)
            customer = STATE["customers"].get(customer_id)
            if not offer: return response(self, 404, {"error": "offer_not_found"})
            if not customer: return response(self, 404, {"error": "customer_not_found"})
            if offer["status"] != "offer_accepted":
                return response(self, 409, {"error": "offer_not_accepted", "status": offer["status"]})
            req = STATE["sim_requests"].get(offer["sim_request_id"])
            # Si el cliente fue creado por POST /v1/customers (no por /customer-data),
            # vincularlo aquí y avanzar la SIMRequest al estado intermedio.
            if req and req["status"] == "offer_accepted":
                req["customer_id"] = customer_id
                transition_sim_request(req, "customer_data_submitted")
            cid = new_id("contract")
            base_url = os.environ.get("AMI_PUBLIC_URL", "http://localhost:8000").rstrip("/")
            contract = {
                "id": cid, "status": "signature_pending",
                "offer_id": offer_id, "customer_id": customer_id,
                "sim_request_id": offer["sim_request_id"],
                "signature_url": f"{base_url}/v1/sign/{cid}",
                "created_at": now(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            }
            STATE["contracts"][cid] = contract
            event("contract_created", "contract", cid, contract)
            if req and req["status"] == "customer_data_submitted":
                transition_sim_request(req, "signature_pending")
            return response(self, 201, contract)

        # Firma directa (atajo programático; la firma "real" pasa por /v1/sign/{id})
        m = re.match(r"^/v1/contracts/([^/]+)/mock-sign$", p)
        if m:
            code, body = sign_contract(m.group(1))
            return response(self, code, body)

        # Activar MobileIdentity (telco mock = lo único realmente simulado)
        if p == "/v1/mobile-identities/activate":
            contract_id = data.get("contract_id")
            contract = STATE["contracts"].get(contract_id)
            if not contract: return response(self, 404, {"error": "contract_not_found"})
            if contract["status"] != "signed":
                return response(self, 409, {"error": "contract_not_signed", "status": contract["status"]})
            offer = STATE["offers"][contract["offer_id"]]
            req = STATE["sim_requests"].get(contract.get("sim_request_id"))
            if req and req["status"] == "signed":
                transition_sim_request(req, "provisioning")
            mid = new_id("mid")
            phone = "+34 600 " + str(int(uuid.uuid4().hex[:6], 16))[-6:].rjust(6, "0")[:6]
            identity = {
                "id": mid, "status": "active", "phone_number": phone,
                "sim_type": offer["sim_type"], "capabilities": offer["capabilities"],
                "contract_id": contract_id, "customer_id": contract["customer_id"],
                "sim_request_id": contract.get("sim_request_id"),
                "provider_activation_id": new_id("mockact"),
                "esim_qr_url": f"https://telco.mock/esim/{mid}.qr",
                "activated_at": now(),
            }
            STATE["mobile_identities"][mid] = identity
            event("mobile_identity_active", "mobile_identity", mid, identity)
            if req and req["status"] == "provisioning":
                transition_sim_request(req, "active")
                req["mobile_identity_id"] = mid
            return response(self, 201, identity)

        return response(self, 404, {"error": "unknown_route", "path": p})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    if API_KEY is None:
        print("AMI: WARNING — AMI_API_KEY not set; running in DEV MODE (auth disabled)")
    else:
        print("AMI: auth enabled (Bearer AMI_API_KEY required)")
    print(f"AMI mock API listening on :{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
