#!/usr/bin/env python3
"""AMI v1 mock API: contratación y aprovisionamiento de SIM/eSIM para agentes.

Sin dependencias externas. La SIM física es lo único stub: el resto del flujo
(oferta → datos cliente → contrato → firma → MobileIdentity activa) se comporta
como producción y respeta la máquina de estados de la spec §17.6.
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
import hashlib, json, os, re, secrets, threading, uuid

from ami_telco import get_active_adapter
import ami_webhooks
import ami_limits
import ami_panel
import ami_storage
import ami_metrics
import ami_pages_en
import ami_security
import ami_docs
import ami_chrome
import ami_live
import ami_use_cases
import ami_pricing
import ami_waitlist
import ami_pitch
import ami_sandbox
import ami_status
import ami_security_page
import ami_kyc
import ami_kyc_admin
import ami_kyc_storage
import ami_notify
import ami_backup
import ami_poc_co
import ami_sip_interconnect
import ami_internal_brief
import ami_vps_runbook
import ami_openai_realtime
import ami_log

# Caps de seguridad / DoS. Tunables por env si hace falta.
MAX_BODY_BYTES = int(os.environ.get("AMI_MAX_BODY_BYTES") or 1_000_000)  # 1 MB
_EVENTS_MAX_KEPT_ENV = os.environ.get("AMI_EVENTS_MAX_KEPT") or os.environ.get("AMI_MAX_EVENTS_RETAINED")
MAX_EVENTS_RETAINED = int(_EVENTS_MAX_KEPT_ENV or 10000)
MAX_WEBHOOKS_PER_MID = int(os.environ.get("AMI_MAX_WEBHOOKS_PER_MID") or 10)

# Rate-limit del panel login (anti credential stuffing).
PANEL_LOGIN_MAX_ATTEMPTS = int(os.environ.get("AMI_PANEL_LOGIN_MAX") or 10)
PANEL_LOGIN_WINDOW_S = int(os.environ.get("AMI_PANEL_LOGIN_WINDOW_S") or 300)
_PANEL_LOGIN_ATTEMPTS: dict[str, list[float]] = {}  # ip -> [ts, ...]
_PANEL_LOGIN_LOCK = threading.Lock()


def _panel_login_throttled(client_ip: str) -> bool:
    """Devuelve True si la IP ya superó PANEL_LOGIN_MAX_ATTEMPTS en
    PANEL_LOGIN_WINDOW_S. Sliding window simple."""
    import time as _time
    now_ts = _time.time()
    with _PANEL_LOGIN_LOCK:
        attempts = _PANEL_LOGIN_ATTEMPTS.setdefault(client_ip, [])
        # Limpiar entries antiguos
        cutoff = now_ts - PANEL_LOGIN_WINDOW_S
        attempts[:] = [t for t in attempts if t > cutoff]
        if len(attempts) >= PANEL_LOGIN_MAX_ATTEMPTS:
            return True
        attempts.append(now_ts)
        return False


# CORS allowlist: por defecto * (compat), pero si AMI_CORS_ORIGINS está seteada,
# solo esos orígenes pueden hacer cross-origin a endpoints autenticados.
_CORS_ORIGINS = [o.strip() for o in (os.environ.get("AMI_CORS_ORIGINS") or "*").split(",")]


def _cors_origin_for(handler) -> str:
    """Decide qué Access-Control-Allow-Origin devolver. Si la allowlist es '*',
    cualquiera. Si es restringida, eco del Origin solo si está en la lista."""
    if _CORS_ORIGINS == ["*"]:
        return "*"
    origin = handler.headers.get("Origin", "")
    if origin and origin in _CORS_ORIGINS:
        return origin
    return ""  # vacío = no header → browser bloquea

# Lock global del STATE. RLock porque algunos handlers anidan llamadas que
# también necesitan acceso (p.ej. event() dentro de transition_*). El coste
# es bajo porque las operaciones son O(1) y el server es I/O-bound. En v2
# si la concurrencia sube se puede particionar por sub-bucket.
STATE_LOCK = threading.RLock()


STATE = {
    "sim_requests": {},
    "offers": {},
    "customers": {},
    "contracts": {},
    "mobile_identities": {},
    "events": [],
    # Scoped agent tokens (Nivel 2 de auth).
    # token_hash (sha256 hex) -> {mid, customer_id, token_prefix, created_at, status, revoked_at, revoke_reason}
    "agent_tokens": {},
    # Operations v2 · SMS. msg_id -> message record (ver schema en helpers SMS).
    "sms_messages": {},
    # Operations v2 · Voz. call_id -> call record (ver schema en helpers Calls).
    "calls": {},
    # Webhooks salientes scoped por MID. wh_id -> webhook record.
    "webhooks": {},
    # Multi-tenancy: customer_id -> {id, name, api_key_hash, status,
    # billing_email, created_at, suspended_at, suspend_reason}. La api_key
    # solo se guarda hasheada (SHA256 hex). Si AMI_API_KEY (legacy) está
    # seteada, se crea automáticamente un customer "default" con esa key al
    # bootear — mantiene compat con la versión single-tenant.
    "customer_accounts": {},
    # Waitlist: leads captados desde /waitlist o el form embebido en landing.
    # Lista de dicts {id, name, email, company, use_case, context, created_at,
    # ip, user_agent}. Persiste en SQLite igual que el resto.
    "waitlist": [],
    # KYC verifications: verificación humana del representante legal antes
    # de generar contrato. kyc_id -> dict (ver ami_kyc.py para schema).
    # Indexado también por token público (en lookup, no como dict propio).
    "kyc_verifications": {},
}

# Carga del STATE persistido (SQLite). Si AMI_DB_PATH no existe, arranca en
# blanco. Si AMI_DB_PATH=:memory: (modo dev/tests), no persiste nada.
# Se hace en el primer import del módulo. Las claves nuevas (introducidas en
# versiones posteriores) heredan el default vacío de arriba si no están en DB.
ami_storage.load_state(STATE)

COUNTRIES = {
    "ES": {
        "sim_types": ["eSIM", "SIM"],
        "capabilities": ["sms", "voice", "data", "whatsapp_ready"],
        "price": {"monthly": 8.90, "setup": 5.00, "currency": "EUR"},
        "activation_time": "mock: immediate after signature",
        "msisdn_prefix": "+34",
        "id_document_label": {"es": "DNI / NIE", "en": "National ID"},
    },
    "CO": {
        "sim_types": ["eSIM", "SIM"],
        "capabilities": ["sms", "voice", "data", "whatsapp_ready"],
        "price": {"monthly": 32000, "setup": 18000, "currency": "COP"},
        "activation_time": "mock: immediate after signature",
        "msisdn_prefix": "+57",
        "id_document_label": {"es": "Cédula de Ciudadanía", "en": "National ID (Cédula)"},
    },
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
# Master key para los endpoints /v1/admin/* (gestión de customers). Si no
# está seteada, esos endpoints devuelven 503.
ADMIN_KEY = os.environ.get("AMI_ADMIN_KEY") or None

# Rutas públicas (no requieren API key): landing, descubrimiento, install y firma desde el navegador.
PUBLIC_GET_PATHS = ("/", "/index.html", "/v1/health", "/llms.txt", "/openapi.json", "/install.sh", "/favicon.ico", "/spec", "/partners", "/experience", "/diagram", "/docs", "/live", "/use-cases", "/pricing", "/calculator", "/waitlist", "/pitch", "/sandbox", "/status", "/security", "/panel", "/panel/login", "/panel/kyc", "/poc-co", "/poc-co/sip", "/internal/brief-co", "/internal/vps-co", "/metrics", "/v1/admin/customers", "/v1/admin/waitlist", "/v1/admin/kyc")
PUBLIC_GET_REGEX = re.compile(r"^/(v1/sign/[^/]+|identity/[^/]+|panel/mid/[^/]+|kyc/[^/]+)$")
PUBLIC_POST_PATHS = ("/v1/demo/quick", "/panel/login", "/panel/logout", "/panel/kyc/login", "/panel/kyc/logout", "/v1/admin/customers", "/v1/admin/mobile-identities/manual", "/v1/waitlist", "/v1/_openai/realtime/webhook")
PUBLIC_POST_REGEX = re.compile(r"^(/v1/sign/[^/]+/confirm|/v1/admin/customers/[^/]+/(rotate-key|suspend|activate)|/kyc/[^/]+/submit|/v1/admin/kyc/purge|/v1/admin/backup/now|/v1/admin/kyc/[^/]+/(verify|reject))$")

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
_SPEC_MD_EN_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs", "SPEC.en.md",
)
try:
    with open(_SPEC_MD_PATH, "r", encoding="utf-8") as _f:
        SPEC_MD = _f.read()
except FileNotFoundError:
    SPEC_MD = "# AMI spec missing on server\n"
try:
    with open(_SPEC_MD_EN_PATH, "r", encoding="utf-8") as _f:
        SPEC_MD_EN = _f.read()
except FileNotFoundError:
    SPEC_MD_EN = SPEC_MD  # fallback al castellano si no hay traducción

# Cache del documento de partnership (markdown), leído al arrancar.
_PARTNERSHIP_MD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs", "PARTNERSHIP.md",
)
_PARTNERSHIP_MD_EN_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs", "PARTNERSHIP.en.md",
)
try:
    with open(_PARTNERSHIP_MD_PATH, "r", encoding="utf-8") as _f:
        PARTNERSHIP_MD = _f.read()
except FileNotFoundError:
    PARTNERSHIP_MD = "# AMI partnership doc missing on server\n"
try:
    with open(_PARTNERSHIP_MD_EN_PATH, "r", encoding="utf-8") as _f:
        PARTNERSHIP_MD_EN = _f.read()
except FileNotFoundError:
    PARTNERSHIP_MD_EN = PARTNERSHIP_MD

# Configuración pública (URLs que aparecen en la landing y en /llms.txt).
REPO_URL = os.environ.get("AMI_REPO_URL", "https://github.com/Gamino17/AMI")
MCP_HTTP_URL = os.environ.get("AMI_MCP_HTTP_URL", "https://mcp.protocolami.com/mcp/")


# i18n helper inyectado en /spec, /partners, /experience, /diagram. Estas
# páginas hoy son sólo en castellano; cuando el visitante tiene `ami-lang`=en
# en localStorage (lo cambia desde la landing), mostramos un banner pequeño
# y un toggle EN/ES en la esquina superior derecha que persiste la
# preferencia. El contenido sigue mostrándose en castellano hasta que se
# traduzca cada page en una tanda dedicada — pero el usuario sabe por qué
# y puede volver a la landing en inglés con un click.
#
# Es JS plano (no f-string), así que sus llaves son llaves reales.
LANG_AUGMENT_CSS = """
  .ami-lang-toggle {
    position: fixed; top: 12px; right: 12px; z-index: 9999;
    background: rgba(20,20,29,0.85);
    backdrop-filter: saturate(140%) blur(10px);
    -webkit-backdrop-filter: saturate(140%) blur(10px);
    border: 1px solid rgba(255,255,255,0.10);
    color: #ededf2;
    font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.05em;
    padding: 0.4rem 0.7rem; border-radius: 6px; cursor: pointer;
  }
  .ami-lang-toggle:hover { border-color: #8b6cff; }
  .ami-lang-banner {
    position: sticky; top: 0; z-index: 99;
    background: rgba(139,108,255,0.10);
    border-bottom: 1px solid rgba(139,108,255,0.25);
    color: #ededf2;
    font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
    font-size: 0.78rem;
    padding: 0.55rem 1rem; text-align: center;
  }
  .ami-lang-banner a { color: #5dd1ff; text-decoration: underline; }
  .ami-lang-banner a:hover { color: #b9e6ff; }
"""

def _path_template(raw_path: str) -> str:
    """Normaliza la URL para métricas: sustituye IDs por placeholders {id}
    para evitar cardinalidad infinita en Prometheus. Quita query string."""
    p = (raw_path.split("?", 1)[0] or "/")
    # Reemplaza segmentos que parecen IDs (prefijos conocidos o hex >=8 chars)
    out = []
    for seg in p.split("/"):
        if not seg:
            out.append(seg); continue
        # IDs con prefijo conocido: simreq_xxx, offer_xxx, mid_xxx, msg_xxx, etc.
        if "_" in seg and any(seg.startswith(pfx) for pfx in
                               ("simreq_", "offer_", "cust_", "contract_",
                                "mid_", "msg_", "call_", "wh_", "evt_")):
            out.append("{id}")
        # Tokens de firma de contrato (URL-friendly hex)
        elif re.fullmatch(r"[0-9a-f]{12,}", seg):
            out.append("{id}")
        else:
            out.append(seg)
    return "/".join(out) or "/"


def _detect_lang(handler) -> str:
    """Decide qué idioma servir para una página dada. Orden de preferencia:
      1. Query string ?lang=en|es (lo que pone el toggle al recargar)
      2. Cookie ami-lang (si el browser la ha promovido al server)
      3. Accept-Language del browser
      4. Castellano por defecto."""
    try:
        qs = dict(re.findall(r"([^&=?]+)=([^&]*)", urlparse(handler.path).query or ""))
    except Exception:
        qs = {}
    if qs.get("lang") in ("en", "es"):
        return qs["lang"]
    cookie = handler.headers.get("Cookie", "")
    m = re.search(r"ami-lang=(en|es)", cookie)
    if m:
        return m.group(1)
    accept = (handler.headers.get("Accept-Language") or "").lower()
    if accept.startswith("en"):
        return "en"
    return "es"


def _with_chrome(html: str, active: str = "", lang: str = "es") -> str:
    """Sustituye el <header>...</header> de la página por el header común
    de ami_chrome, y añade el footer común antes del </body>. Inyecta
    chrome_css() en el primer <style> y chrome_js() antes del </body>.

    Esta función se aplica a TODAS las páginas públicas (landing, spec,
    partners, experience, diagram, docs) para garantizar nav y footer
    consistentes. Las páginas con flujo específico (panel, sign, identity)
    NO la usan."""
    import re as _re

    # Inyectar CSS del chrome al final del primer <style>...</style>
    css = ami_chrome.chrome_css()
    if "</style>" in html:
        html = html.replace("</style>", css + "\n</style>", 1)
    else:
        # Sin <style> dedicado — meter uno justo antes del </head>
        html = html.replace("</head>", f"<style>{css}</style>\n</head>", 1)

    # Reemplazar el primer <header>...</header> de la página por el común.
    # Si la página no tiene <header>, lo insertamos justo después del <body>.
    new_header = ami_chrome.header_html(active=active, lang=lang).strip()
    pattern = _re.compile(r"<header[^>]*>.*?</header>", _re.S | _re.I)
    if pattern.search(html):
        html = pattern.sub(new_header, html, count=1)
    else:
        html = html.replace("<body>", "<body>\n" + new_header, 1)

    # Reemplazar el primer <footer>...</footer> (si existe) por el común;
    # si no existe, insertar antes del </body>.
    new_footer = ami_chrome.footer_html(lang=lang).strip()
    pattern_f = _re.compile(r"<footer[^>]*>.*?</footer>", _re.S | _re.I)
    if pattern_f.search(html):
        html = pattern_f.sub(new_footer, html, count=1)
    else:
        html = html.replace("</body>", new_footer + "\n</body>", 1)

    # JS del chrome al final del body
    js_block = f"<script>{ami_chrome.chrome_js()}</script>\n</body>"
    html = html.replace("</body>", js_block, 1)

    return html


def _with_lang_augment(html: str, lang: str = "es", has_en_content: bool = False) -> str:
    """Inyecta el toggle EN/ES + (si toca) banner 'currently in Spanish'.

    - Si `lang == "en"` y la página NO ha sido renderizada con contenido en
      inglés (has_en_content=False), añade banner pidiendo ir a la landing en
      inglés. Si SÍ ha sido renderizada en inglés (has_en_content=True), no
      hay banner — solo el toggle.
    - Reemplaza `<html lang="es">` por `<html lang="<lang>">` si toca.
    Idempotente: si la landing ya trae su propio toggle, el JS lo respeta."""
    meta = ""
    if has_en_content and lang == "en":
        meta = '<meta name="ami-content-lang" content="en">\n'
    style = f"{meta}<style>{LANG_AUGMENT_CSS}</style>\n</head>"
    script = f"<script>{LANG_AUGMENT_JS}</script>\n</body>"
    out = html.replace("</head>", style, 1).replace("</body>", script, 1)
    if lang == "en":
        out = out.replace('<html lang="es">', '<html lang="en">', 1)
    return out


LANG_AUGMENT_JS = """
(function() {
  // Helper: persiste 'ami-lang' como cookie de sesión + localStorage, y navega
  // a la misma URL añadiendo ?lang=xx para que el server (que no ve localStorage)
  // sirva la versión correcta del contenido en el próximo request.
  function persistAndReload(next) {
    try { localStorage.setItem('ami-lang', next); } catch(e) {}
    document.cookie = 'ami-lang=' + next + '; path=/; max-age=' + (60*60*24*365);
    var url = new URL(window.location.href);
    url.searchParams.set('lang', next);
    window.location.href = url.toString();
  }

  // Si llegamos con ?lang=xx (vía toggle o link), sincroniza el lang del <html>
  // y el localStorage por si el usuario navega después.
  var qsLang = new URLSearchParams(window.location.search).get('lang');
  if (qsLang === 'es' || qsLang === 'en') {
    document.documentElement.lang = qsLang;
    try { localStorage.setItem('ami-lang', qsLang); } catch(e) {}
    document.cookie = 'ami-lang=' + qsLang + '; path=/; max-age=' + (60*60*24*365);
  }

  // Idempotente: si la página ya trae su propio toggle (landing), no añadimos
  // otro — pero sí actualizamos el comportamiento del existente.
  var existing = document.getElementById('langToggle');
  var lang = document.documentElement.lang || 'es';

  if (existing) {
    existing.addEventListener('click', function(e) {
      e.preventDefault(); e.stopPropagation();
      persistAndReload(lang === 'es' ? 'en' : 'es');
    }, true);
    return;
  }

  // Banner cuando se eligió EN pero esta página sigue en castellano (sin
  // SPEC.en.md / PARTNERSHIP.en.md, p.ej. /experience y /diagram). El meta
  // 'ami-content-lang' indica que el contenido SÍ está en inglés y no hace
  // falta avisar.
  var contentLangMeta = document.querySelector('meta[name="ami-content-lang"]');
  var contentInEn = contentLangMeta && contentLangMeta.getAttribute('content') === 'en';
  if (lang === 'en' && !contentInEn) {
    var banner = document.createElement('div');
    banner.className = 'ami-lang-banner';
    banner.innerHTML = 'This page is currently in Spanish. ' +
                       '<a href="/?lang=en">View the AMI homepage in English →</a>';
    document.body.insertBefore(banner, document.body.firstChild);
  }

  // Toggle EN/ES persistente.
  var btn = document.createElement('button');
  btn.className = 'ami-lang-toggle';
  btn.setAttribute('aria-label', 'Toggle language');
  btn.textContent = lang === 'es' ? 'EN' : 'ES';
  btn.addEventListener('click', function() {
    persistAndReload(lang === 'es' ? 'en' : 'es');
  });
  document.body.appendChild(btn);
})();
"""


def is_public(method, path):
    if method == "OPTIONS": return True
    if method == "GET":
        return path in PUBLIC_GET_PATHS or bool(PUBLIC_GET_REGEX.match(path))
    if method == "POST":
        return path in PUBLIC_POST_PATHS or bool(PUBLIC_POST_REGEX.match(path))
    return False


def _hash_api_key(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _bootstrap_default_customer():
    """Si AMI_API_KEY (legacy) está seteada y no hay customer aún con esa key,
    crea uno automáticamente con id 'cust_default'. Esto mantiene 100% de
    compat con la versión single-tenant: la misma AMI_API_KEY sigue
    funcionando, todo el state queda asociado a este customer."""
    if not API_KEY:
        return
    h = _hash_api_key(API_KEY)
    # Si ya existe un customer con esta key, no hacemos nada
    for c in STATE["customer_accounts"].values():
        if c.get("api_key_hash") == h:
            return
    cust = {
        "id": "cust_default",
        "name": "Default",
        "api_key_hash": h,
        "api_key_prefix": API_KEY[:8] if len(API_KEY) >= 8 else API_KEY,
        "status": "active",
        "billing_email": None,
        "created_at": now() if STATE.get("events") is not None else None,
        "suspended_at": None,
        "suspend_reason": None,
    }
    STATE["customer_accounts"]["cust_default"] = cust


def _customer_by_api_key(plain: str):
    """Resuelve una API key plain a un customer record. Devuelve None si no
    existe customer con esa key o si está suspended. Usa compare_digest para
    no leakear el match por timing."""
    h = _hash_api_key(plain)
    for c in STATE["customer_accounts"].values():
        stored = c.get("api_key_hash") or ""
        if (c.get("status") == "active"
                and len(stored) == len(h)
                and secrets.compare_digest(stored, h)):
            return c
    return None


def check_auth(handler):
    """Resuelve el Authorization header al account_id del customer-account
    multi-tenant. Devuelve el id (truthy) o None (falsy). El nombre "account"
    distingue de "customer" legal (legal_name, tax_id) que vive en STATE["customers"].

    En modo dev (sin AMI_API_KEY ni customer_accounts en DB), devuelve
    "acct_dev" para mantener el comportamiento permisivo de v1."""
    if API_KEY is None and not STATE["customer_accounts"]:
        return "acct_dev"  # dev mode
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    plain = auth[7:].strip()
    cust = _customer_by_api_key(plain)
    if cust is None:
        return None
    handler._account_id = cust["id"]
    return cust["id"]


def _request_account_id(handler):
    """Acceso conveniente al account_id del request actual. Se asume que
    check_auth ya pasó (o que el endpoint es público y por ende no filtra)."""
    return getattr(handler, "_account_id", None)


def _scoped(obj, account_id):
    """Filter de multi-tenancy. Devuelve obj si pertenece al account o si no
    tiene owner asignado; None si pertenece a otro account.

    Esto se aplica en los GETs genéricos de sim-requests, contracts,
    mobile-identities, etc.: una request del account A no puede ver objetos
    del account B aunque conozca su id."""
    if not account_id:
        return obj
    owner = obj.get("account_id") if isinstance(obj, dict) else None
    if owner and owner != account_id:
        return None
    return obj


def check_admin_auth(handler):
    """Auth de los endpoints /v1/admin/*. Acepta solo el AMI_ADMIN_KEY."""
    if ADMIN_KEY is None:
        return False
    auth = handler.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], ADMIN_KEY)


def check_kyc_admin_auth(handler):
    """Auth para endpoints KYC admin. Acepta Bearer, cookie de sesión
    (set por POST /panel/kyc/login) o query ?key= para demos. El JS del
    panel envía cookie automáticamente, por eso lo aceptamos aquí.

    Si la auth pasa por cookie, los endpoints que mutan estado (verify,
    reject, purge) DEBEN además exigir un header X-CSRF-Token que coincida
    con la cookie ami-kyc-csrf — double-submit cookie pattern. Para Bearer
    o query no aplica CSRF (no son explotables cross-site).
    """
    if check_admin_auth(handler):
        return True
    if ADMIN_KEY is None:
        return False
    if ami_kyc_admin.check_kyc_admin_cookie(handler, ADMIN_KEY):
        return True
    try:
        from urllib.parse import parse_qs
        qs = parse_qs(urlparse(handler.path).query or "")
        key_q = (qs.get("key") or [None])[0]
        if key_q is not None and secrets.compare_digest(key_q, ADMIN_KEY):
            return True
    except Exception:
        pass
    return False


def check_kyc_admin_csrf(handler) -> bool:
    """Si la auth es por cookie, verifica double-submit CSRF token.
    Si la auth es por Bearer, devuelve True (CSRF no aplica).
    Llamar SOLO después de check_kyc_admin_auth().
    """
    # Si hay Bearer válido, ignoramos CSRF (clients programáticos)
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and ADMIN_KEY:
        if secrets.compare_digest(auth[7:], ADMIN_KEY):
            return True
    # Si la auth es por cookie, exigimos matching CSRF.
    cookie = handler.headers.get("Cookie", "") or ""
    csrf_cookie = None
    has_session = False
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "ami-kyc-csrf":
            csrf_cookie = v
        elif k == ami_kyc_admin.COOKIE_NAME and v:
            has_session = True
    if not has_session:
        # No es sesión cookie — probablemente query ?key=. Lo aceptamos
        # por compat con la demo CLI (curl).
        return True
    header_token = handler.headers.get("X-CSRF-Token") or ""
    if not csrf_cookie or not header_token:
        return False
    try:
        return secrets.compare_digest(csrf_cookie, header_token)
    except (TypeError, ValueError):
        return False


# ----- Scoped agent tokens (Nivel 2 de auth) -------------------------
# Cada MobileIdentity activa tiene su propio bearer. Devuelto en plano
# una sola vez al activar el número; guardamos solo el hash. Endpoints
# de operación scoped-a-agente (send_sms, start_call, etc., cuando los
# añadamos) validan este token y comprueban que el MID en URL coincide
# con el MID al que pertenece el token. Rotación hard: el viejo muere.

def _hash_token(plain): return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def mint_agent_token(mid, customer_id):
    """Genera un agent_token scoped al MID. Devuelve el plano una vez."""
    plain = "amiagt_live_" + secrets.token_hex(32)
    th = _hash_token(plain)
    STATE["agent_tokens"][th] = {
        "mid": mid,
        "customer_id": customer_id,
        "token_prefix": plain[:18],   # para mostrar "amiagt_live_7f3a9b" sin exponer el resto
        "created_at": now(),
        "status": "active",
        "revoked_at": None,
        "revoke_reason": None,
    }
    return plain, th


def revoke_agent_tokens_for_mid(mid, reason):
    """Revoca TODOS los tokens activos vinculados a un MID."""
    n = 0
    for th, t in STATE["agent_tokens"].items():
        if t["mid"] == mid and t["status"] == "active":
            t["status"] = "revoked"
            t["revoked_at"] = now()
            t["revoke_reason"] = reason
            n += 1
    return n


def validate_agent_token(handler, expected_mid=None):
    """Verifica el header Authorization Bearer como agent_token.

    Devuelve el record del token si es válido (y opcionalmente está
    bound al expected_mid). Devuelve None si no es válido.
    """
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    plain = auth[7:].strip()
    if not plain.startswith("amiagt_"):
        return None
    rec = STATE["agent_tokens"].get(_hash_token(plain))
    if not rec or rec["status"] != "active":
        return None
    if expected_mid is not None and rec["mid"] != expected_mid:
        return None
    return rec


# ----- Operations v2 · SMS ------------------------------------------
# Schema de mensaje:
#   id: msg_xxx
#   mid: MobileIdentity dueña
#   direction: 'outbound' | 'inbound'
#   from: E.164 (string con + y dígitos; espacios permitidos)
#   to:   E.164
#   body: string ≤ 1000 chars (varios SMS concatenados)
#   status: 'queued' | 'sent' | 'delivered' | 'failed' (outbound)
#                  | 'received'                              (inbound)
#   created_at, delivered_at: ISO
#   telco_ref: ref opcional del SMSC/partner (string)

# E.164 normalizado: signo +, 6-15 dígitos. Aceptamos espacios y guiones
# en input — los limpiamos a la hora de validar y guardar.
_E164_RE = re.compile(r"^\+[1-9]\d{5,14}$")

def _normalize_msisdn(raw):
    """Acepta '+34 600 549 832', '+34-600-549-832', etc.; devuelve '+34600549832'.
    Devuelve None si el formato es inválido tras limpieza."""
    if not isinstance(raw, str): return None
    s = re.sub(r"[\s\-]", "", raw.strip())
    if not _E164_RE.match(s): return None
    return s


def _find_mid_by_phone(phone_norm):
    """Busca una MobileIdentity activa cuyo número (normalizado) coincide."""
    for mid, identity in STATE["mobile_identities"].items():
        ph = _normalize_msisdn(identity.get("phone_number", ""))
        if ph and ph == phone_norm:
            return mid, identity
    return None, None


# Política de retries del envío al telco. Si el adapter (Kannel o mock) deja
# el SMS en `failed` antes de llegar a `sent`, reintentamos automáticamente
# con backoff. Esto cubre fallos transitorios de red, 5xx temporales del
# SMSC, throttling momentáneo, etc. Tras `SMS_MAX_RETRIES` fallos consecutivos
# el SMS queda como `failed` definitivo.
SMS_MAX_RETRIES = 3
SMS_RETRY_BACKOFF_S = (2.0, 8.0, 30.0)
CALL_MAX_RETRIES = 2
CALL_RETRY_BACKOFF_S = (3.0, 15.0)


def _schedule_sms_retry(msg_id):
    """Programa un Timer que, si tras el delay el SMS sigue failed,
    lo reintenta con el adapter."""
    import threading
    msg = STATE["sms_messages"].get(msg_id)
    if not msg: return
    attempt = msg.get("retry_count", 0)
    if attempt >= SMS_MAX_RETRIES:
        ami_metrics.log_error("sms_retries_exhausted", msg_id=msg_id,
                               mid=msg.get("mid"), attempts=attempt)
        return
    delay = SMS_RETRY_BACKOFF_S[min(attempt, len(SMS_RETRY_BACKOFF_S) - 1)]
    def attempt_retry():
        with STATE_LOCK:
            m = STATE["sms_messages"].get(msg_id)
            if not m or m["status"] not in ("failed",):
                return  # ya progresó (sent/delivered) o lo borraron
            m["retry_count"] = attempt + 1
            m["status"] = "queued"
            event("sms_retry", "sms_message", msg_id,
                  {"attempt": attempt + 1, "mid": m.get("mid")})
            ami_metrics.log_info("sms_retry", msg_id=msg_id, mid=m.get("mid"),
                                  attempt=attempt + 1)
            try:
                get_active_adapter().send_sms(m)
            except Exception as e:
                m["status"] = "failed"
                ami_metrics.log_error("sms_retry_error", msg_id=msg_id, error=str(e))
        # Si tras este intento sigue failed, encolar otro retry
        threading.Timer(0.5, lambda: _schedule_sms_retry(msg_id)).start()
    threading.Timer(delay, attempt_retry).start()


def _schedule_call_retry(call_id):
    """Análogo a _schedule_sms_retry para llamadas que fallan antes de
    entrar en in_progress."""
    import threading
    call = STATE["calls"].get(call_id)
    if not call: return
    attempt = call.get("retry_count", 0)
    if attempt >= CALL_MAX_RETRIES:
        ami_metrics.log_error("call_retries_exhausted", call_id=call_id,
                               mid=call.get("mid"), attempts=attempt)
        return
    # Solo reintentamos si el fallo ocurrió antes de answer (failed o no_answer
    # antes de in_progress). Una llamada que llegó a in_progress y luego falló
    # NO debe reintentarse sin acción humana — la conversación ya empezó.
    if call.get("started_at"):
        return
    delay = CALL_RETRY_BACKOFF_S[min(attempt, len(CALL_RETRY_BACKOFF_S) - 1)]
    def attempt_retry():
        with STATE_LOCK:
            c = STATE["calls"].get(call_id)
            if not c or c["status"] not in ("failed", "no_answer"):
                return
            c["retry_count"] = attempt + 1
            # Resetear estado a initiated y volver a originar
            c["status"] = "initiated"
            c["ended_at"] = None
            c["hangup_cause"] = None
            event("call_retry", "call", call_id,
                  {"attempt": attempt + 1, "mid": c.get("mid")})
            ami_metrics.log_info("call_retry", call_id=call_id, mid=c.get("mid"),
                                  attempt=attempt + 1)
            try:
                get_active_adapter().place_call(c)
            except Exception as e:
                c["status"] = "failed"
                ami_metrics.log_error("call_retry_error", call_id=call_id, error=str(e))
    threading.Timer(delay, attempt_retry).start()


def send_sms_outbound(mid, to_raw, body):
    """Encola un SMS saliente desde el MID. Devuelve (status_code, payload).

    Estado inicial 'queued'; tras un timer corto pasa a 'sent', luego 'delivered'
    (DLR simulado en v1; cuando conectemos SMSC real lo dispara el partner).
    """
    identity = STATE["mobile_identities"].get(mid)
    if not identity or identity.get("status") != "active":
        return 409, {"error": "mobile_identity_not_active", "mid": mid}
    to = _normalize_msisdn(to_raw)
    if not to:
        return 400, {"error": "invalid_to", "detail": "expected E.164 (+digits)"}
    if not isinstance(body, str) or not body.strip():
        return 400, {"error": "empty_body"}
    if len(body) > 1000:
        return 400, {"error": "body_too_long", "max": 1000, "got": len(body)}

    # Enforcement de límites + presupuesto. Si bloquea, 429 con razón.
    ok, reason = ami_limits.check_and_charge_sms(identity, to)
    if not ok:
        event("sms_blocked", "mobile_identity", mid, {"reason": reason, "to": to})
        ami_metrics.log_warn("sms_blocked", mid=mid, reason=reason, to=to)
        return 429, {"error": reason, "mid": mid}

    msg = {
        "id": new_id("msg"),
        "mid": mid,
        "direction": "outbound",
        "from": _normalize_msisdn(identity["phone_number"]),
        "to": to,
        "body": body,
        "status": "queued",
        "created_at": now(),
        "delivered_at": None,
        "telco_ref": None,
    }
    STATE["sms_messages"][msg["id"]] = msg
    event("sms_queued", "sms_message", msg["id"],
          {"mid": mid, "to": to, "len": len(body)})
    ami_metrics.SMS_SENT.inc(status="queued")
    ami_metrics.log_info("sms_queued", msg_id=msg["id"], mid=mid, to=to,
                          len=len(body), account_id=identity.get("account_id"))

    # Delegar el envío al telco. En mock simula DLR con Timers; en live habla
    # a Kannel y los cambios de estado llegan vía webhook /v1/_telco/sms/dlr.
    get_active_adapter().send_sms(msg)

    return 201, msg


def record_sms_inbound(from_raw, to_raw, body, telco_ref=None):
    """Registra un SMS entrante desde el partner telco hacia un MID nuestro."""
    to = _normalize_msisdn(to_raw)
    if not to:
        return 400, {"error": "invalid_to"}
    from_ = _normalize_msisdn(from_raw) or from_raw  # si el remitente es un short-code no E.164, lo guardamos crudo
    if not isinstance(body, str):
        return 400, {"error": "invalid_body"}
    mid, identity = _find_mid_by_phone(to)
    if not mid:
        return 404, {"error": "destination_not_found", "to": to}
    if identity.get("status") != "active":
        return 409, {"error": "mobile_identity_not_active", "mid": mid}

    msg = {
        "id": new_id("msg"),
        "mid": mid,
        "direction": "inbound",
        "from": from_,
        "to": to,
        "body": body,
        "status": "received",
        "created_at": now(),
        "delivered_at": now(),
        "telco_ref": telco_ref,
    }
    STATE["sms_messages"][msg["id"]] = msg
    event("sms_inbound", "sms_message", msg["id"],
          {"mid": mid, "from": from_, "len": len(body)})
    ami_webhooks.dispatch_event("sms.inbound", mid, msg)
    return 201, msg


def list_sms_for_mid(mid, limit=20, direction=None):
    """Lista mensajes de un MID, más reciente primero."""
    msgs = [m for m in STATE["sms_messages"].values() if m["mid"] == mid]
    if direction in ("outbound", "inbound"):
        msgs = [m for m in msgs if m["direction"] == direction]
    msgs.sort(key=lambda m: m["created_at"], reverse=True)
    return msgs[: max(1, min(int(limit or 20), 200))]


# Webhook del partner telco: clave compartida en header X-Telco-Key.
# Si no está seteada en el entorno, el endpoint queda deshabilitado en prod.
TELCO_INBOUND_KEY = os.environ.get("AMI_TELCO_INBOUND_KEY") or None


# ----- Operations v2 · Voz (bridge-by-API) -------------------------------
# AMI es el operador / SIP provider. NO ejecuta voz ni hace media bridge:
# el "cerebro" del audio vive en el endpoint SIP del cliente (un motor de
# voz en tiempo real, un PBX propio, o cualquier destino que hable SIP).
# AMI sólo origina la llamada al PSTN y la bridgea SIP-to-SIP hacia ese
# destino.
#
# Schema de call record:
#   id: call_xxx
#   mid: MobileIdentity propietaria
#   direction: 'outbound' | 'inbound'
#   from: E.164 (nuestro número en outbound, externo en inbound)
#   to:   E.164 (externo en outbound, nuestro número en inbound)
#   callback_sip_uri: a dónde bridgea AMI (outbound) o se forwardea (inbound)
#   status: initiated | ringing | in_progress | completed
#                     | failed | no_answer | busy | cancelled
#   created_at, started_at, ended_at: ISO (started cuando answered, ended al colgar)
#   duration_sec: int (calculada al ended)
#   hangup_cause: string libre del partner
#   telco_ref: ref del switch (asterisk channel id, etc.)

CALL_TERMINAL = {"completed", "failed", "no_answer", "busy", "cancelled"}
CALL_TRANSITIONS = {
    "initiated":   ["ringing", "failed", "cancelled"],
    "ringing":     ["in_progress", "no_answer", "busy", "failed", "cancelled"],
    "in_progress": ["completed", "failed"],
}

# sip: o sips: scheme + user@host[:port] + parámetros opcionales (;k=v...)
_SIP_URI_RE = re.compile(
    r"^sips?:[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+(?::\d{1,5})?(?:;[A-Za-z0-9._\-]+=[A-Za-z0-9._\-]+)*$"
)


def _validate_sip_uri(uri):
    if not isinstance(uri, str): return False
    return bool(_SIP_URI_RE.match(uri.strip()))


def transition_call(call, new_status, **patch):
    """Aplica una transición validada al call record y emite audit event."""
    cur = call["status"]
    if new_status not in CALL_TRANSITIONS.get(cur, []):
        raise ValueError(f"invalid_call_transition: {cur} -> {new_status}")
    call["status"] = new_status
    if new_status == "in_progress" and not call.get("started_at"):
        call["started_at"] = now()
    if new_status in CALL_TERMINAL:
        call["ended_at"] = now()
        if call.get("started_at"):
            try:
                started = datetime.fromisoformat(call["started_at"])
                ended = datetime.fromisoformat(call["ended_at"])
                call["duration_sec"] = max(0, int((ended - started).total_seconds()))
            except Exception:
                pass
    for k, v in patch.items():
        if v is not None:
            call[k] = v
    event(f"call_{new_status}", "call", call["id"], {"from": cur, "to": new_status, **patch})
    # Contabilidad + métrica del estado terminal + webhook saliente.
    if new_status in CALL_TERMINAL:
        ami_metrics.CALLS_PLACED.inc(status=new_status)
    if new_status == "completed":
        identity = STATE["mobile_identities"].get(call.get("mid"))
        if identity:
            ami_limits.account_call_duration(identity, call.get("duration_sec"))
        ami_webhooks.dispatch_event("call.completed", call["mid"], call)
    elif new_status in ("failed", "no_answer", "busy", "cancelled"):
        ami_webhooks.dispatch_event("call.failed", call["mid"], call)
        # Retry automático para fallos pre-answer (no para busy/cancelled —
        # esos son señales legítimas que NO se reintentan).
        if (new_status in ("failed", "no_answer")
                and not call.get("started_at")
                and call.get("retry_count", 0) < CALL_MAX_RETRIES):
            _schedule_call_retry(call["id"])


def place_call_outbound(mid, to_raw, callback_sip_uri):
    """Origina una llamada saliente desde el MID. AMI marca y la bridgea por SIP."""
    identity = STATE["mobile_identities"].get(mid)
    if not identity or identity.get("status") != "active":
        return 409, {"error": "mobile_identity_not_active", "mid": mid}
    to = _normalize_msisdn(to_raw)
    if not to:
        return 400, {"error": "invalid_to", "detail": "expected E.164 (+digits)"}
    if not _validate_sip_uri(callback_sip_uri):
        return 400, {"error": "invalid_callback_sip_uri",
                     "detail": "expected sip:user@host[:port][;params]"}

    # Enforcement de límites antes de marcar al PSTN.
    ok, reason = ami_limits.check_and_reserve_call(identity, to)
    if not ok:
        event("call_blocked", "mobile_identity", mid, {"reason": reason, "to": to})
        ami_metrics.log_warn("call_blocked", mid=mid, reason=reason, to=to)
        return 429, {"error": reason, "mid": mid}

    call = {
        "id": new_id("call"),
        "mid": mid,
        "direction": "outbound",
        "from": _normalize_msisdn(identity["phone_number"]),
        "to": to,
        "callback_sip_uri": callback_sip_uri.strip(),
        "status": "initiated",
        "created_at": now(),
        "started_at": None,
        "ended_at": None,
        "duration_sec": None,
        "hangup_cause": None,
        "telco_ref": None,
    }
    STATE["calls"][call["id"]] = call
    event("call_placed", "call", call["id"],
          {"mid": mid, "to": to, "callback_sip_uri": call["callback_sip_uri"]})
    ami_metrics.CALLS_PLACED.inc(status="initiated")
    ami_metrics.log_info("call_placed", call_id=call["id"], mid=mid, to=to,
                          account_id=identity.get("account_id"))

    # Delegar la llamada al telco. En mock simula ringing/answer/hangup con
    # Timers; en live pide Originate a Asterisk vía ARI y las transiciones
    # llegan por /v1/_telco/calls/{id}/status.
    get_active_adapter().place_call(call)
    return 201, call


def hangup_call(mid, call_id):
    """Termina manualmente una llamada del MID. Idempotente sobre estados terminales."""
    call = STATE["calls"].get(call_id)
    if not call or call["mid"] != mid:
        return 404, {"error": "call_not_found"}
    if call["status"] in CALL_TERMINAL:
        return 200, call  # ya estaba terminada — idempotente
    # Desde initiated → cancelled; desde ringing → cancelled; desde in_progress → completed.
    target = "cancelled" if call["status"] in ("initiated", "ringing") else "completed"
    try:
        transition_call(call, target, hangup_cause="hangup_by_agent")
    except ValueError as e:
        return 409, {"error": str(e)}
    # Pide al telco que cuelgue (en mock no hace red; en live manda DELETE a ARI).
    get_active_adapter().hangup_call(call)
    return 200, call


def list_calls_for_mid(mid, limit=20, direction=None):
    calls = [c for c in STATE["calls"].values() if c["mid"] == mid]
    if direction in ("outbound", "inbound"):
        calls = [c for c in calls if c["direction"] == direction]
    calls.sort(key=lambda c: c["created_at"], reverse=True)
    return calls[: max(1, min(int(limit or 20), 200))]


def route_call_inbound(from_raw, to_raw, telco_ref=None):
    """Decide a dónde reenviar una llamada entrante. El partner llama esto al
    recibir la llamada por SS7/SIP en su lado; si AMI devuelve un sip_uri, el
    partner hace SIP-forward; si no, rechaza con 486."""
    to = _normalize_msisdn(to_raw)
    if not to:
        return 400, {"error": "invalid_to"}
    mid, identity = _find_mid_by_phone(to)
    if not mid:
        return 404, {"error": "destination_not_found", "to": to}
    if identity.get("status") != "active":
        return 409, {"error": "mobile_identity_not_active", "mid": mid}
    sip_uri = identity.get("inbound_sip_uri")
    if not sip_uri:
        # No hay endpoint configurado para entrantes — el partner debe rechazar.
        event("call_inbound_rejected", "mobile_identity", mid,
              {"reason": "no_inbound_sip_uri", "from": from_raw, "to": to})
        return 409, {"error": "no_inbound_sip_uri",
                     "detail": "MID has no inbound endpoint configured",
                     "mid": mid}
    call = {
        "id": new_id("call"),
        "mid": mid,
        "direction": "inbound",
        "from": _normalize_msisdn(from_raw) or from_raw,
        "to": to,
        "callback_sip_uri": sip_uri,
        "status": "initiated",
        "created_at": now(),
        "started_at": None,
        "ended_at": None,
        "duration_sec": None,
        "hangup_cause": None,
        "telco_ref": telco_ref,
    }
    STATE["calls"][call["id"]] = call
    event("call_inbound_routed", "call", call["id"],
          {"mid": mid, "from": call["from"], "forward_to": sip_uri})
    ami_webhooks.dispatch_event("call.inbound", mid, call)
    return 201, {"call_id": call["id"], "forward_sip_uri": sip_uri, "mid": mid}


def update_call_status(call_id, new_status, **patch):
    """Webhook del partner: cambia el estado de una llamada según señalización real."""
    call = STATE["calls"].get(call_id)
    if not call:
        return 404, {"error": "call_not_found"}
    try:
        transition_call(call, new_status, **patch)
    except ValueError as e:
        return 409, {"error": str(e), "current": call["status"]}
    return 200, call


def now(): return datetime.now(timezone.utc).isoformat()
def new_id(prefix): return f"{prefix}_{uuid.uuid4().hex[:10]}"


def event(action, entity_type, entity_id, data=None):
    e = {"id": new_id("evt"), "at": now(), "action": action,
         "entity_type": entity_type, "entity_id": entity_id, "data": data or {}}
    STATE["events"].append(e)
    # Rotación: mantenemos solo los últimos MAX_EVENTS_RETAINED eventos para
    # evitar que el blob de STATE crezca sin tope (cada request HTTP escribe el
    # snapshot completo via ami_storage.save_state). Configurable via
    # AMI_EVENTS_MAX_KEPT (default 10000). En v2 esto se mueve a una tabla con
    # TTL en SQLite y escrituras incrementales.
    if len(STATE["events"]) > MAX_EVENTS_RETAINED:
        del STATE["events"][:len(STATE["events"]) - MAX_EVENTS_RETAINED]
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
    handler._last_status = status
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    cors_origin = _cors_origin_for(handler)
    if cors_origin:
        handler.send_header("Access-Control-Allow-Origin", cors_origin)
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def respond_html(handler, status, html):
    handler._last_status = status
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    # Headers de seguridad. CSP es permisiva con Google Fonts y jsDelivr
    # (mermaid en /partners). Si añadimos más recursos externos, ampliar
    # aquí explícitamente — el default-src 'self' debe bloquear el resto.
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    handler.send_header("Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'")
    if _is_secure_request(handler):
        handler.send_header("Strict-Transport-Security",
                            "max-age=15552000; includeSubDomains")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def respond_text(handler, status, text, content_type="text/plain; charset=utf-8"):
    handler._last_status = status
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class _BodyTooLarge(Exception): pass


def read_json(handler, max_bytes: int | None = None):
    """Lee y deserializa el body JSON. Aplica un cap (por defecto
    MAX_BODY_BYTES) para evitar DoS de memoria por Content-Length gigante.
    Endpoints concretos pueden pedir un cap mayor (e.g. KYC sube imágenes)."""
    cap = max_bytes if max_bytes is not None else MAX_BODY_BYTES
    try:
        n = int(handler.headers.get("Content-Length", "0") or 0)
    except ValueError:
        raise ValueError("invalid Content-Length")
    if n <= 0:
        return {}
    if n > cap:
        raise _BodyTooLarge(f"body exceeds cap ({n} > {cap})")
    raw = handler.rfile.read(n)
    return json.loads(raw.decode())


def read_form(handler):
    """Body form-urlencoded (panel login). Cap pequeño (8 KB) por seguridad."""
    from urllib.parse import parse_qs
    try:
        n = int(handler.headers.get("Content-Length", "0") or 0)
    except ValueError:
        return {}
    if n <= 0:
        return {}
    if n > 8192:
        raise _BodyTooLarge(f"form body exceeds 8KB ({n})")
    raw = handler.rfile.read(n).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


def _is_secure_request(handler) -> bool:
    """Detecta si la conexión llegó por HTTPS. Render y la mayoría de PaaS
    terminan TLS en su proxy y pasan X-Forwarded-Proto=https. En localhost
    el flag Secure haría que la cookie no se setee — por eso lo aplicamos
    solo cuando detectamos HTTPS."""
    xfp = (handler.headers.get("X-Forwarded-Proto") or "").lower()
    return xfp == "https"


def _redirect(handler, location, set_cookie=None, clear_cookie=False):
    """Helper: 302 redirect, opcionalmente set/clear cookie de panel."""
    handler.send_response(302)
    handler.send_header("Location", location)
    secure = "; Secure" if _is_secure_request(handler) else ""
    if clear_cookie:
        handler.send_header("Set-Cookie",
                            f"ami-panel-token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}")
    elif set_cookie:
        # HttpOnly previene JS read · SameSite=Lax bloquea CSRF cross-site ·
        # Secure en HTTPS evita que la cookie viaje en plain text.
        handler.send_header("Set-Cookie",
                            f"ami-panel-token={set_cookie}; Path=/; HttpOnly; "
                            f"SameSite=Lax; Max-Age={60*60*24*7}{secure}")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


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
    event("customer_created", "customer", cid,
          {"legal_name": customer["legal_name"], "sim_request_id": req["id"]})
    transition_sim_request(req, "customer_data_submitted")
    steps.append({"step": "customer_linked", "id": cid})

    # 4) Crear contrato
    contract_id = new_id("contract")
    signing_token = secrets.token_urlsafe(24)
    base_url = os.environ.get("AMI_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    contract = {
        "id": contract_id, "status": "signature_pending",
        "offer_id": offer_id, "customer_id": cid,
        "sim_request_id": req_id,
        "signature_url": f"{base_url}/v1/sign/{contract_id}?t={signing_token}",
        "signing_token": signing_token,
        "created_at": now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }
    STATE["contracts"][contract_id] = contract
    event("contract_created", "contract", contract_id,
          {"offer_id": offer_id, "customer_id": cid})
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
    event("mobile_identity_active", "mobile_identity", mid,
          {"contract_id": contract_id, "customer_id": cid,
           "sim_type": identity["sim_type"]})
    transition_sim_request(req, "active")
    req["mobile_identity_id"] = mid
    steps.append({"step": "mobile_identity_active", "id": mid, "phone_number": phone})

    # round(.., 2) preserva fracciones <1ms en lugar de truncar a 0; floor a 0.01.
    elapsed_ms = max(0.01, round((datetime.now(timezone.utc) - t0).total_seconds() * 1000, 2))
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
        f'<form method="post" action="/v1/sign/{html_escape(contract["id"])}/confirm?t={html_escape(contract.get("signing_token",""))}">'
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

  /* EXPLORE · sitemap visible ------------------------------------------- */
  .explore {
    padding: 6rem 0 4rem;
    border-top: 1px solid var(--line);
    background: var(--bg);
  }
  .explore .wrap { max-width: 1280px; margin: 0 auto; padding: 0 1.5rem; }
  .explore-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem;
    margin-top: 2.5rem;
  }
  @media (max-width: 880px) {
    .explore-grid { grid-template-columns: 1fr; gap: 1rem; }
  }
  .explore-col {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.8rem 1.6rem;
  }
  .explore-col:hover { border-color: rgba(139,108,255,0.4); }
  .explore-col h3 {
    font-family: var(--mono); font-size: 0.78rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.18em;
    color: var(--accent); margin: 0 0 0.6rem;
  }
  .explore-col .explore-desc {
    color: var(--ink-soft); font-size: 0.9rem; margin: 0 0 1.2rem;
    line-height: 1.5;
  }
  .explore-col ul { list-style: none; padding: 0; margin: 0; }
  .explore-col li {
    padding: 0.55rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.88rem;
  }
  .explore-col li:last-child { border-bottom: 0; }
  .explore-col li a { color: var(--ink-soft); text-decoration: none; }
  .explore-col li a:hover { color: var(--ink); }
  .explore-col li a strong {
    color: var(--ink); font-weight: 600;
    font-family: var(--mono); font-size: 0.86rem;
  }

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
        # --- Contratación y aprovisionamiento (v1) ---
        ("ami.search_sim_options",      "Lista países y capacidades de numeración disponibles (voz · SMS · datos)."),
        ("ami.request_sim_offer",       "Crea una solicitud de número y devuelve la oferta inmediata desde nuestra plataforma."),
        ("ami.accept_offer",            "Acepta una oferta antes de generar contrato."),
        ("ami.submit_customer_data",    "Envía los datos legales/fiscales del cliente y los vincula a la solicitud."),
        ("ami.create_contract",         "Genera el contrato y devuelve la URL de firma."),
        ("ami.get_contract_status",     "Consulta el estado actual de un contrato."),
        ("ami.confirm_signature_status","Comprueba si el contrato ya está firmado."),
        ("ami.activate_sim_identity",   "Activa el número en nuestra plataforma tras la firma del contrato."),
        ("ami.get_identity_status",     "Consulta el estado de una MobileIdentity activa."),
        ("ami.cancel_request",          "Cancela la solicitud antes de la activación del número."),
        ("ami.rotate_agent_token",      "Rota el agent_token de una MobileIdentity (hard rotate, invalida el anterior)."),
        # --- Operations v2 · SMS ---
        ("ami.send_sms",                "Envía un SMS desde la MobileIdentity activa (auth: agent_token Nivel 2)."),
        ("ami.list_sms",                "Lista los SMS de la MobileIdentity (filtrable por dirección)."),
        # --- Operations v2 · Voz (bridge-by-API) ---
        ("ami.place_call",              "Origina una llamada saliente y la bridgea por SIP al endpoint del cliente (motor de voz, PBX o cualquier destino SIP)."),
        ("ami.list_calls",              "Lista las llamadas del MID (filtrable por dirección)."),
        ("ami.get_call",                "Detalle de una llamada (scoped al MID)."),
        ("ami.hangup_call",             "Termina una llamada en curso del MID."),
        ("ami.set_inbound_sip_uri",     "Configura el endpoint SIP al que reenviar las llamadas entrantes del MID."),
        # --- Webhooks salientes ---
        ("ami.create_webhook",          "Registra un webhook saliente para un MID (eventos sms/call inbound y status)."),
        ("ami.list_webhooks",           "Lista los webhooks registrados para un MID."),
        ("ami.delete_webhook",          "Elimina un webhook por id."),
        # --- Rate limits + spending ---
        ("ami.get_limits",              "Lee los límites (rate + budget + countries) de un MID."),
        ("ami.update_limits",           "Actualiza los límites del MID (patch parcial)."),
        ("ami.get_usage",               "Lee el usage actual del MID (auth: customer)."),
        ("ami.get_my_usage",            "Lee el usage del MID dueño del agent_token (auth: agente)."),
        # --- Audit ---
        ("ami.list_events",             "Devuelve los últimos AuditEvents (debug e inspección)."),
    ]


def _endpoints_for_landing():
    return [
        # --- Contratación y aprovisionamiento (v1) ---
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
        ("POST", "/v1/mobile-identities/{id}/rotate-token", "Rota el agent_token (hard rotate)."),
        ("POST", "/v1/mobile-identities/{id}/inbound-config","Setea el endpoint SIP para llamadas entrantes."),
        ("GET",  "/v1/mobile-identities/{id}/limits",       "Lee los límites del MID."),
        ("POST", "/v1/mobile-identities/{id}/limits",       "Actualiza los límites del MID."),
        ("GET",  "/v1/mobile-identities/{id}/usage",        "Usage actual del MID (auth: customer)."),
        ("POST", "/v1/mobile-identities/{id}/webhooks",     "Crea un webhook saliente para un MID."),
        ("GET",  "/v1/mobile-identities/{id}/webhooks",     "Lista los webhooks del MID."),
        ("POST", "/v1/mobile-identities/{id}/webhooks/{wh}/delete","Borra un webhook."),
        # --- Operations v2 · scoped por agent_token (Nivel 2) ---
        ("GET",  "/v1/agent/self",                          "Info de la MobileIdentity dueña del agent_token."),
        ("POST", "/v1/agent/sms/send",                      "Envía un SMS desde el MID (auth: agent_token)."),
        ("GET",  "/v1/agent/sms",                           "Lista los SMS del MID (filtrable por dirección)."),
        ("POST", "/v1/agent/calls/place",                   "Origina una llamada saliente bridgeada por SIP."),
        ("GET",  "/v1/agent/calls",                         "Lista las llamadas del MID."),
        ("GET",  "/v1/agent/calls/{id}",                    "Detalle de una llamada del MID."),
        ("POST", "/v1/agent/calls/{id}/hangup",             "Termina una llamada del MID."),
        ("GET",  "/v1/agent/usage",                         "Usage del MID dueño del agent_token."),
        # --- Admin (multi-tenancy, auth: AMI_ADMIN_KEY) ---
        ("POST", "/v1/admin/customers",                     "Crear customer-account (devuelve API key una vez)."),
        ("GET",  "/v1/admin/customers",                     "Listar customer-accounts (sin secret)."),
        ("POST", "/v1/admin/customers/{id}/rotate-key",     "Rotar la API key de un customer."),
        ("POST", "/v1/admin/customers/{id}/suspend",        "Suspender un customer."),
        ("POST", "/v1/admin/customers/{id}/activate",       "Reactivar un customer suspended."),
        # --- Audit y demo ---
        ("GET",  "/v1/events",                              "Últimos AuditEvents."),
        ("POST", "/v1/demo/quick",                          "Flujo end-to-end completo en una llamada (público, sin auth)."),
        ("GET",  "/identity/{id}",                          "Página pública de una MobileIdentity activa."),
        ("GET",  "/spec",                                   "Spec del protocolo renderizada en HTML."),
        ("GET",  "/diagram",                                "Animación standalone del ciclo de vida del stack."),
        ("GET",  "/docs",                                   "Portal de documentación interactivo (bilingüe)."),
    ]


def render_landing():
    tool_count = len(_tools_for_landing())
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
                    "AMI_API_URL": "https://protocolami.com",
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
        <a href="/docs">Docs</a>
        <a href="#tools">Tools</a>
        <a href="#api">API</a>
        <a href="/spec">Spec</a>
        <a href="/partners">Partners</a>
        <a href="/experience">Experience</a>
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
            <button class="tab active" data-pane="pane-http" role="tab">HTTP remoto</button>
            <button class="tab" data-pane="pane-oneliner" role="tab">One-liner</button>
            <button class="tab" data-pane="pane-clone" role="tab">git clone</button>
            <button class="tab" data-pane="pane-claude" role="tab">Claude config</button>
          </div>
          <span class="badge-beta">β BETA</span>
        </div>

        <div class="panes">
          <div class="pane active" id="pane-http">
            <button class="copy-btn" data-copy="https://mcp.protocolami.com/mcp/">copy</button>
<span class="comment"># <span data-lang="es">Recomendado para agentes: cero instalación, cero API key local.</span><span data-lang="en">Recommended for agents: zero install, zero local API key.</span></span>
<span class="comment"># <span data-lang="es">Apunta tu cliente MCP (transporte streamable-http) a:</span><span data-lang="en">Point your MCP client (streamable-http transport) to:</span></span>

<span class="accent">https://mcp.protocolami.com/mcp/</span>
          </div>

          <div class="pane" id="pane-oneliner">
            <button class="copy-btn" data-copy="curl -fsSL https://protocolami.com/install.sh | sh">copy</button>
<span class="comment"># <span data-lang="es">Instala el MCP en ~/.ami (stdio local). Necesita AMI_API_KEY propia.</span><span data-lang="en">Installs the MCP into ~/.ami (local stdio). Requires your own AMI_API_KEY.</span></span>
<span class="prompt">$</span> <span class="cmd">curl -fsSL <span class="accent">https://protocolami.com/install.sh</span> | sh</span>
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
        "AMI_API_URL": "https://protocolami.com",
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
        <span class="accent">"AMI_API_URL"</span>: "https://protocolami.com",
        <span class="accent">"AMI_API_KEY"</span>: "&lt;your-api-key&gt;"
      }}
    }}
  }}
}}</span>
          </div>
        </div>
      </div>

      <p class="qs-foot">
        <span data-lang="es">Las cuatro vías llegan al mismo MCP server con las {tool_count} tools <code>ami.*</code>. <strong>HTTP remoto</strong> es la opción recomendada para agentes: cero instalación y sin gestionar API key local. Las otras vías son útiles cuando necesitas el MCP corriendo en local (clientes que solo soportan stdio, entornos sin red saliente, etc.).</span>
        <span data-lang="en">All four paths reach the same MCP server with the {tool_count} <code>ami.*</code> tools. <strong>Remote HTTP</strong> is the recommended option for agents: zero install, no local API key to manage. The other paths are useful when you need the MCP running locally (stdio-only clients, no-egress environments, etc.).</span>
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
        <span data-lang="es">De solicitud a número activo, sin humano en medio.</span>
        <span data-lang="en">From request to active number, no human in the loop.</span>
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
          <p data-lang="es">Clona el repo, instala el venv y añade el bloque a tu <code>claude_desktop_config.json</code>. Las {tool_count} tools <code>ami.*</code> aparecen al reiniciar.</p>
          <p data-lang="en">Clone the repo, install the venv and add the block to your <code>claude_desktop_config.json</code>. The {tool_count} <code>ami.*</code> tools appear after restart.</p>
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
        <span data-lang="es">{tool_count} tools. Un namespace.</span>
        <span data-lang="en">{tool_count} tools. One namespace.</span>
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

  <!-- EXPLORE · sitemap visible ========================================= -->
  <section id="explore" class="explore">
    <div class="wrap">
      <div class="eyebrow">
        <span data-lang="es">explora</span>
        <span data-lang="en">explore</span>
      </div>
      <h2>
        <span data-lang="es">Todo lo que puedes consultar.</span>
        <span data-lang="en">Everything you can explore.</span>
      </h2>
      <p class="sub">
        <span data-lang="es">Tres caminos según lo que necesites: ver el producto, construir contra él, u operarlo.</span>
        <span data-lang="en">Three paths based on what you need: see the product, build against it, or operate it.</span>
      </p>

      <div class="explore-grid">
        <div class="explore-col">
          <h3>Product</h3>
          <p class="explore-desc">
            <span data-lang="es">Entiende qué es AMI sin escribir código.</span>
            <span data-lang="en">Understand what AMI is without writing code.</span>
          </p>
          <ul>
            <li><a href="/pitch"><strong>Pitch deck</strong> — <span data-lang="es">presentación navegable con flechas</span><span data-lang="en">slide deck navigable with arrows</span></a></li>
            <li><a href="/live"><strong>Live demo</strong> — <span data-lang="es">demo cinemática en vivo</span><span data-lang="en">live cinematic demo</span></a></li>
            <li><a href="/use-cases"><strong>Use cases</strong> — <span data-lang="es">6 recetas reales con código</span><span data-lang="en">6 real recipes with code</span></a></li>
            <li><a href="/experience"><strong>Experience</strong> — <span data-lang="es">narrativa visual completa</span><span data-lang="en">full visual narrative</span></a></li>
            <li><a href="/diagram"><strong>Diagram</strong> — <span data-lang="es">animación del ciclo de vida</span><span data-lang="en">lifecycle animation</span></a></li>
            <li><a href="/pricing"><strong>Pricing</strong> — <span data-lang="es">planes Starter / Growth / Enterprise</span><span data-lang="en">Starter / Growth / Enterprise plans</span></a></li>
            <li><a href="/calculator"><strong>ROI Calculator</strong> — <span data-lang="es">cuánto ahorras vs wrappers</span><span data-lang="en">how much you save vs wrappers</span></a></li>
          </ul>
        </div>

        <div class="explore-col">
          <h3>Build</h3>
          <p class="explore-desc">
            <span data-lang="es">Integra AMI desde tu agente o tu backend.</span>
            <span data-lang="en">Integrate AMI from your agent or backend.</span>
          </p>
          <ul>
            <li><a href="/docs"><strong>Docs</strong> — <span data-lang="es">portal interactivo con try-it</span><span data-lang="en">interactive portal with try-it</span></a></li>
            <li><a href="/sandbox"><strong>Sandbox</strong> — <span data-lang="es">playground REST en el navegador</span><span data-lang="en">REST playground in the browser</span></a></li>
            <li><a href="/spec"><strong>Spec</strong> — <span data-lang="es">documento técnico canónico</span><span data-lang="en">canonical technical document</span></a></li>
            <li><a href="/security"><strong>Security</strong> — <span data-lang="es">whitepaper de seguridad</span><span data-lang="en">security whitepaper</span></a></li>
            <li><a href="/openapi.json"><strong>OpenAPI 3.1</strong> — <span data-lang="es">para generadores y herramientas</span><span data-lang="en">for generators and tooling</span></a></li>
            <li><a href="/llms.txt"><strong>llms.txt</strong> — <span data-lang="es">índice para agentes AI</span><span data-lang="en">index for AI agents</span></a></li>
            <li><a href="{repo}/tree/main/sdk"><strong>SDKs</strong> — Python · TypeScript</a></li>
            <li><a href="/install.sh"><strong>install.sh</strong> — <span data-lang="es">instalador one-liner</span><span data-lang="en">one-liner installer</span></a></li>
          </ul>
        </div>

        <div class="explore-col">
          <h3>Operate</h3>
          <p class="explore-desc">
            <span data-lang="es">Gestiona tus números, customers, partners.</span>
            <span data-lang="en">Manage your numbers, customers, partners.</span>
          </p>
          <ul>
            <li><a href="/panel"><strong>Panel</strong> — <span data-lang="es">dashboard del customer (auth con API key)</span><span data-lang="en">customer dashboard (API key auth)</span></a></li>
            <li><a href="/partners"><strong>Partners</strong> — <span data-lang="es">para operadores telco</span><span data-lang="en">for telco operators</span></a></li>
            <li><a href="{mcp_url}"><strong>MCP endpoint</strong> — <span data-lang="es">para clientes MCP remotos</span><span data-lang="en">for remote MCP clients</span></a></li>
            <li><a href="/status"><strong>Status</strong> — <span data-lang="es">uptime + componentes</span><span data-lang="en">uptime + components</span></a></li>
            <li><a href="/v1/health"><strong>Health</strong> — <span data-lang="es">JSON crudo</span><span data-lang="en">raw JSON</span></a></li>
            <li><a href="/metrics"><strong>Metrics</strong> — <span data-lang="es">Prometheus exposition</span><span data-lang="en">Prometheus exposition</span></a></li>
            <li><a href="{repo}"><strong>GitHub</strong> — <span data-lang="es">código + issues</span><span data-lang="en">code + issues</span></a></li>
          </ul>
        </div>
      </div>
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
        <a href="/spec">spec</a>
        <a href="/partners">partners</a>
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
        instant:       'instantáneo',
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
        instant:       'instant',
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
      // Escala humana: si todo el flujo fue imperceptible, no muestres µs
      // técnicos por paso; sólo "instantáneo" en el badge final lo cuenta.
      if (ms < 50)   return '';                                 // sub-perceptual
      if (ms < 1000) return '+' + Math.round(ms) + 'ms';
      return '+' + (ms / 1000).toFixed(1) + 's';
    }}
    function fmtTotal(ms, labels) {{
      if (ms < 50)   return labels.instant;
      if (ms < 1000) return Math.round(ms) + ' ms';
      return (ms / 1000).toFixed(1) + ' s';
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
        status.textContent = L2.statusOk + ' · ' + fmtTotal(total, L2);

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
        <a href="/partners">Partners</a>
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
_TABLE_ROW_RE   = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE   = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
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
            if lang == "mermaid":
                # Bloque mermaid: emitido como <div class="mermaid"> para que
                # mermaid.js lo renderice client-side. Sin html_escape porque
                # mermaid usa su propia DSL y queremos preservar caracteres.
                body = "\n".join(code_lines)
                out.append(f'<div class="mermaid">\n{body}\n</div>')
            else:
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

        # Table (GFM): header row + separator row + body rows.
        # Detectamos si la línea actual y la siguiente forman cabecera+separador.
        if _TABLE_ROW_RE.match(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_all()
            def _split_row(s):
                inner = s.strip()
                if inner.startswith("|"): inner = inner[1:]
                if inner.endswith("|"):   inner = inner[:-1]
                return [c.strip() for c in inner.split("|")]
            header_cells = _split_row(line)
            i += 2  # consume header + separator
            body_rows = []
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                body_rows.append(_split_row(lines[i]))
                i += 1
            ncols = len(header_cells)
            thead = "<tr>" + "".join(
                f"<th>{_md_inline(c)}</th>" for c in header_cells
            ) + "</tr>"
            tbody = ""
            for row in body_rows:
                cells = (row + [""] * ncols)[:ncols]  # pad/truncate
                tbody += "<tr>" + "".join(
                    f"<td>{_md_inline(c)}</td>" for c in cells
                ) + "</tr>"
            out.append(
                f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"
            )
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


def render_spec_page(lang: str = "es"):
    body_html, toc = render_markdown(SPEC_MD_EN if lang == "en" else SPEC_MD)
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


def render_partnership_page(lang: str = "es"):
    """Página /partners: doc PARTNERSHIP.md renderizado con Mermaid client-side.

    Reusa la mayoría del CSS de render_spec_page() y añade:
      - mermaid.js desde CDN (jsdelivr) con tema dark custom matching la paleta
      - estilos extra para envolver los diagramas con un fondo translúcido
    """
    body_html, toc = render_markdown(PARTNERSHIP_MD_EN if lang == "en" else PARTNERSHIP_MD)
    toc_html = "".join(
        f'<li><a href="#{slug}">{html_escape(text)}</a></li>'
        for _level, text, slug in toc
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Partnership con operador telco</title>
<meta name="description" content="Propuesta de partnership con operador telco para convertir AMI en el protocolo mundial de identidad móvil para agentes AI.">
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{LANDING_CSS}
  .doc-wrap {{
    display: grid; grid-template-columns: 240px minmax(0, 820px);
    gap: 3rem; max-width: 1160px; margin: 0 auto;
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
  .doc code {{ font-family: var(--mono); font-size: 0.9em; }}
  .doc p code, .doc li code, .doc h2 code, .doc h3 code, .doc h4 code, .doc td code {{
    background: var(--surface); border: 1px solid var(--line);
    padding: 0.1em 0.4em; border-radius: 4px;
    color: var(--accent-2); font-size: 0.88em;
  }}
  .doc pre code {{ background: transparent; border: 0; padding: 0; color: inherit; }}
  .doc table {{
    width: 100%; border-collapse: collapse; margin: 1.5rem 0;
    font-size: 0.92rem; border: 1px solid var(--line);
    border-radius: 8px; overflow: hidden;
  }}
  .doc thead {{ background: var(--surface); }}
  .doc th {{
    text-align: left; padding: 0.7rem 0.9rem;
    font-weight: 600; color: var(--ink); border-bottom: 1px solid var(--line);
  }}
  .doc td {{
    padding: 0.65rem 0.9rem; border-bottom: 1px solid var(--line-soft);
    color: var(--ink); vertical-align: top;
  }}
  .doc tbody tr:last-child td {{ border-bottom: 0; }}
  .doc tbody tr:hover {{ background: rgba(255,255,255,0.02); }}
  .doc .mermaid {{
    background: var(--bg-soft); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.5rem; margin: 1.8rem 0;
    text-align: center; overflow-x: auto;
  }}
  .doc .mermaid svg {{ max-width: 100%; height: auto; }}
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
        <a href="/spec">Spec</a>
        <a href="/openapi.json">openapi.json</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
      </nav>
    </div>
  </header>

  <div class="doc-wrap">
    <aside class="doc-toc">
      <h4>Índice</h4>
      <ol>{toc_html}</ol>
    </aside>
    <article class="doc">
      {body_html}
    </article>
  </div>

  <footer>
    <div class="wrap">
      <div>Parallax IEI · AMI · partnership con operador telco</div>
      <div class="links">
        <a href="/">Home</a>
        <a href="/spec">Spec</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
      </div>
    </div>
  </footer>

<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{
    startOnLoad: true,
    securityLevel: 'loose',
    theme: 'base',
    themeVariables: {{
      darkMode: true,
      background: '#0e0e14',
      primaryColor: '#1a1a24',
      primaryTextColor: '#ededf2',
      primaryBorderColor: '#8b6cff',
      lineColor: '#5dd1ff',
      secondaryColor: '#14141d',
      tertiaryColor: '#0e0e14',
      mainBkg: '#1a1a24',
      secondBkg: '#14141d',
      tertiaryBkg: '#0e0e14',
      nodeBorder: '#8b6cff',
      clusterBkg: 'rgba(139,108,255,0.06)',
      clusterBorder: '#8b6cff',
      titleColor: '#ededf2',
      edgeLabelBackground: '#0e0e14',
      textColor: '#ededf2',
      labelTextColor: '#ededf2',
      actorBkg: '#1a1a24',
      actorBorder: '#8b6cff',
      actorTextColor: '#ededf2',
      actorLineColor: '#5dd1ff',
      signalColor: '#5dd1ff',
      signalTextColor: '#ededf2',
      sequenceNumberColor: '#0e0e14',
      noteBkgColor: '#14141d',
      noteBorderColor: '#fbbf24',
      noteTextColor: '#fbbf24',
      activationBkgColor: '#14141d',
      activationBorderColor: '#5dd1ff',
    }},
    flowchart: {{ curve: 'basis', padding: 20 }},
    sequence: {{ actorMargin: 60, messageMargin: 35, noteMargin: 12 }},
  }});
</script>

</body>
</html>"""


def render_experience_page():
    """Página /experience: storytelling visual de AMI con scroll-driven animations.

    Vanilla JS + Canvas + IntersectionObserver. Sin frameworks, sin libs externas
    (excepto Google Fonts ya cacheadas). Respeta prefers-reduced-motion.
    Secciones:
      1. Hero con aurora animada
      2. "El número" con slot-machine reveal
      3. Network canvas (agentes ↔ AMI ↔ operador) con partículas
      4. Flujo paso a paso scroll-driven
      5. Grid flipable de tools
      6. Live demo contra /v1/demo/quick
      7. CTA cierre
    """
    tool_count = len(_tools_for_landing())
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Experience</title>
<meta name="description" content="Identidad móvil para agentes AI. Una experiencia visual del protocolo: del número generado en milisegundos a la red mundial de operadores.">
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --line: #1f1f2c;
    --line-soft: #16161f;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-3: #ff5cb6;
    --green: #4ade80;
    --amber: #fbbf24;
    --code-bg: #0a0a12;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", "Monaco", monospace;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    font-family: var(--sans);
    color: var(--ink);
    background: var(--bg);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }}
  ::selection {{ background: var(--accent); color: #fff; }}
  a {{ color: var(--accent-2); text-decoration: none; }}
  a:hover {{ color: #b9e6ff; }}

  /* HEADER */
  .exp-header {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    padding: 1rem 0;
    background: rgba(6, 6, 10, 0.6);
    backdrop-filter: saturate(140%) blur(14px);
    -webkit-backdrop-filter: saturate(140%) blur(14px);
    border-bottom: 1px solid rgba(255,255,255,0.05);
    transition: opacity 0.3s, transform 0.3s;
  }}
  .exp-header.hidden {{ opacity: 0; transform: translateY(-100%); pointer-events: none; }}
  .exp-header .wrap {{
    max-width: 1280px; margin: 0 auto; padding: 0 1.75rem;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .exp-brand {{
    font-family: var(--mono); font-weight: 700; font-size: 0.95rem;
    letter-spacing: 0.02em; display: flex; align-items: center; gap: 0.6rem;
    color: var(--ink); text-decoration: none;
  }}
  .exp-brand .dot {{ color: var(--accent); }}
  .exp-nav {{ display: flex; align-items: center; gap: 1.4rem; }}
  .exp-nav a {{ color: var(--ink-soft); font-size: 0.85rem; font-weight: 500; }}
  .exp-nav a:hover {{ color: var(--ink); }}
  .exp-nav .cta {{
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; padding: 0.45rem 0.95rem; border-radius: 6px;
    font-size: 0.85rem; font-weight: 500;
    box-shadow: 0 4px 16px -4px rgba(123, 92, 255, 0.5);
  }}
  .exp-nav .cta:hover {{ color: #fff; transform: translateY(-1px); }}

  /* SECCIÓN: contenedor común */
  section.exp {{ position: relative; padding: 5rem 0; overflow: hidden; }}
  section.exp .wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 1.75rem; position: relative; z-index: 2; }}
  .eyebrow {{
    font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em;
    margin-bottom: 1.2rem;
  }}

  /* HERO ============================================================= */
  .exp-hero {{
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    padding: 6rem 1.5rem 4rem;
    position: relative; overflow: hidden;
  }}
  .aurora {{
    position: absolute; inset: 0; z-index: 0; pointer-events: none;
    overflow: hidden;
  }}
  .aurora::before, .aurora::after, .aurora .third {{
    content: ""; position: absolute;
    border-radius: 50%; filter: blur(140px);
    will-change: transform, opacity;
  }}
  .aurora::before {{
    width: 700px; height: 700px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 70%);
    top: -10%; left: -10%;
    animation: aurora-1 22s ease-in-out infinite;
    opacity: 0.55;
  }}
  .aurora::after {{
    width: 600px; height: 600px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 70%);
    bottom: -10%; right: -5%;
    animation: aurora-2 28s ease-in-out infinite;
    opacity: 0.45;
  }}
  .aurora .third {{
    width: 500px; height: 500px;
    background: radial-gradient(circle, #ff5cb6 0%, transparent 70%);
    top: 35%; left: 45%;
    animation: aurora-3 25s ease-in-out infinite;
    opacity: 0.25;
  }}
  @keyframes aurora-1 {{
    0%,100% {{ transform: translate(0,0) scale(1); }}
    50% {{ transform: translate(20%, 15%) scale(1.15); }}
  }}
  @keyframes aurora-2 {{
    0%,100% {{ transform: translate(0,0) scale(1); }}
    50% {{ transform: translate(-15%, -20%) scale(1.2); }}
  }}
  @keyframes aurora-3 {{
    0%,100% {{ transform: translate(-50%, -50%) scale(1); opacity: 0.25; }}
    50% {{ transform: translate(-30%, -70%) scale(1.4); opacity: 0.4; }}
  }}
  /* Grid sutil de fondo para dar profundidad */
  .grid-bg {{
    position: absolute; inset: 0; z-index: 0; pointer-events: none;
    background-image:
      linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
    background-size: 48px 48px;
    mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
    -webkit-mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
  }}

  .hero-content {{ position: relative; z-index: 2; text-align: center; max-width: 1100px; }}
  .hero-pill {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    background: rgba(20, 20, 29, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 0.4rem 0.95rem; border-radius: 999px;
    font-family: var(--mono); font-size: 0.72rem; font-weight: 500;
    color: var(--ink-soft); letter-spacing: 0.04em;
    margin-bottom: 2rem;
    opacity: 0; transform: translateY(20px);
    animation: fade-up 0.8s 0.1s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .hero-pill .live {{
    width: 6px; height: 6px; border-radius: 50%; background: var(--green);
    box-shadow: 0 0 10px var(--green);
    animation: pulse-dot 2s ease-in-out infinite;
  }}
  @keyframes pulse-dot {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}

  .hero-title {{
    font-weight: 800; letter-spacing: -0.04em; line-height: 0.95;
    font-size: clamp(2.8rem, 9vw, 7.5rem);
    margin: 0 0 1.5rem 0;
  }}
  .hero-title .word {{
    display: inline-block; overflow: hidden; vertical-align: top;
  }}
  .hero-title .word > span {{
    display: inline-block;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    transform: translateY(110%);
    animation: word-up 1s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .hero-title .word:nth-child(1) > span {{ animation-delay: 0.20s; }}
  .hero-title .word:nth-child(2) > span {{ animation-delay: 0.30s; }}
  .hero-title .word:nth-child(3) > span {{
    animation-delay: 0.40s;
    background: linear-gradient(135deg, #8b6cff 0%, #5dd1ff 50%, #ff5cb6 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .hero-title .word:nth-child(4) > span {{ animation-delay: 0.55s; }}
  .hero-title .word:nth-child(5) > span {{ animation-delay: 0.70s; }}
  @keyframes word-up {{ to {{ transform: translateY(0); }} }}

  .hero-sub {{
    font-size: clamp(1.05rem, 1.6vw, 1.3rem); color: var(--ink-soft);
    max-width: 42em; margin: 0 auto 2.5rem;
    opacity: 0; transform: translateY(20px);
    animation: fade-up 1s 1.0s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  @keyframes fade-up {{ to {{ opacity: 1; transform: translateY(0); }} }}

  .hero-ctas {{
    display: flex; gap: 0.8rem; justify-content: center; flex-wrap: wrap;
    opacity: 0; animation: fade-up 1s 1.3s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .btn {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    padding: 0.85rem 1.5rem; border-radius: 8px;
    font-family: var(--sans); font-size: 0.95rem; font-weight: 500;
    text-decoration: none; cursor: pointer; border: 0;
    transition: transform 0.1s, box-shadow 0.2s, background 0.2s;
  }}
  .btn:active {{ transform: translateY(1px); }}
  .btn-primary {{
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff;
    box-shadow: 0 1px 0 rgba(255,255,255,0.15) inset, 0 10px 30px -8px rgba(123, 92, 255, 0.55);
  }}
  .btn-primary:hover {{ box-shadow: 0 1px 0 rgba(255,255,255,0.2) inset, 0 14px 36px -8px rgba(123, 92, 255, 0.7); color: #fff; }}
  .btn-ghost {{
    background: rgba(20,20,29,0.6); color: var(--ink); border: 1px solid rgba(255,255,255,0.1);
    backdrop-filter: blur(12px);
  }}
  .btn-ghost:hover {{ border-color: var(--accent); color: #fff; }}

  .scroll-hint {{
    position: absolute; bottom: 2.5rem; left: 50%; transform: translateX(-50%);
    color: var(--ink-mute); font-family: var(--mono); font-size: 0.68rem;
    letter-spacing: 0.2em; text-transform: uppercase;
    opacity: 0; animation: fade-up 1s 1.8s forwards;
  }}
  .scroll-hint::before {{
    content: ""; display: block; width: 1px; height: 30px;
    background: linear-gradient(to bottom, transparent, var(--ink-mute));
    margin: 0 auto 0.7rem;
    animation: scroll-line 2s ease-in-out infinite;
  }}
  @keyframes scroll-line {{ 0%,100% {{ opacity: 0.2; height: 30px; }} 50% {{ opacity: 1; height: 40px; }} }}

  /* SECCIÓN: EL NÚMERO ============================================= */
  .exp-number {{ background: var(--bg); }}
  .exp-number .wrap {{ text-align: center; }}
  .number-display {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(2.2rem, 9vw, 7.5rem);
    letter-spacing: -0.02em; line-height: 1.15;
    margin: 2rem 0 2.5rem 0;
    color: var(--ink);
    display: flex; gap: 0.18em; justify-content: center; flex-wrap: nowrap;
  }}
  .number-display .digit {{
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 0.72em; height: 1.15em;
    background: var(--surface); border-radius: 0.12em;
    padding: 0 0.08em;
    color: transparent; transition: color 0.3s, background 0.3s;
    position: relative;
  }}
  .number-display .digit.revealed {{ color: var(--ink); background: transparent; }}
  .number-display .digit.revealed.flash {{
    background: linear-gradient(180deg, rgba(139,108,255,0.2), transparent);
  }}
  .number-display .sep {{ width: 0.25em; }}
  .number-display .plus {{
    color: var(--accent-2); font-weight: 600;
    background: none; padding: 0;
  }}
  .number-status {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    font-family: var(--mono); font-size: 0.85rem;
    background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.3);
    color: var(--green); padding: 0.45rem 1rem; border-radius: 999px;
    margin-bottom: 2rem; opacity: 0; transition: opacity 0.5s;
  }}
  .number-status.show {{ opacity: 1; }}
  .number-status .live {{
    width: 6px; height: 6px; border-radius: 50%; background: var(--green);
    box-shadow: 0 0 10px var(--green);
    animation: pulse-dot 2s ease-in-out infinite;
  }}
  .number-caption {{ color: var(--ink-soft); font-size: 1.1rem; max-width: 38em; margin: 0 auto; }}
  .number-caption strong {{ color: var(--ink); font-weight: 600; }}

  .section-title {{
    font-weight: 700; letter-spacing: -0.02em; line-height: 1.05;
    font-size: clamp(2rem, 5vw, 3.5rem);
    margin: 0 0 1.2rem 0;
  }}
  .section-title .grad {{
    background: linear-gradient(135deg, #8b6cff 0%, #5dd1ff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .section-sub {{ color: var(--ink-soft); font-size: 1.1rem; max-width: 42em; }}

  /* SECCIÓN: NETWORK CANVAS ======================================== */
  .exp-network {{ padding: 3rem 0 4rem; }}
  .exp-network .wrap {{ text-align: center; margin-bottom: 3rem; }}
  .net-canvas-wrap {{
    position: relative; width: 100%; max-width: 1100px; margin: 0 auto;
    height: 460px; border: 1px solid var(--line);
    border-radius: 16px;
    background: radial-gradient(ellipse at center, rgba(139,108,255,0.06) 0%, transparent 70%), var(--bg-soft);
    overflow: hidden;
  }}
  .net-canvas-wrap canvas {{ display: block; width: 100%; height: 100%; }}
  .net-legend {{
    display: flex; gap: 1.5rem; justify-content: center; margin-top: 1.5rem;
    flex-wrap: wrap; font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-soft);
  }}
  .net-legend span {{ display: inline-flex; align-items: center; gap: 0.4rem; }}
  .net-legend .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  .net-legend .agent {{ background: #5dd1ff; box-shadow: 0 0 8px #5dd1ff; }}
  .net-legend .ami   {{ background: #8b6cff; box-shadow: 0 0 8px #8b6cff; }}
  .net-legend .telco {{ background: #fbbf24; box-shadow: 0 0 8px #fbbf24; }}
  .net-legend .ws    {{ background: #5a5a70; box-shadow: 0 0 6px rgba(90,90,112,0.5); }}

  /* SECCIÓN: FLUJO PASO A PASO ===================================== */
  .exp-flow {{ padding: 4rem 0; background: var(--bg); }}
  .exp-flow .wrap {{ text-align: center; margin-bottom: 2rem; }}
  .flow-stage {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 4rem;
    max-width: 1200px; margin: 0 auto; padding: 0 1.75rem;
    align-items: center;
  }}
  @media (max-width: 880px) {{ .flow-stage {{ grid-template-columns: 1fr; gap: 1.5rem; }} }}
  .flow-step {{
    padding: 2rem 0; min-height: 0;
    display: grid; grid-template-columns: 1fr 1fr; gap: 4rem;
    max-width: 1200px; margin: 0 auto; padding-left: 1.75rem; padding-right: 1.75rem;
    align-items: center;
    opacity: 0.3; transition: opacity 0.6s;
  }}
  .flow-step.active {{ opacity: 1; }}
  @media (max-width: 880px) {{
    .flow-step {{ grid-template-columns: 1fr; gap: 1.5rem; min-height: auto; padding: 2rem 1.75rem; }}
  }}
  .flow-step-num {{
    font-family: var(--mono); font-size: 0.72rem; color: var(--accent);
    letter-spacing: 0.15em; text-transform: uppercase; margin-bottom: 0.6rem;
  }}
  .flow-step h3 {{
    font-size: 1.6rem; font-weight: 700; letter-spacing: -0.01em;
    margin: 0 0 1rem 0; line-height: 1.2;
  }}
  .flow-step p {{ color: var(--ink-soft); margin: 0 0 1rem 0; font-size: 1rem; }}
  .flow-code {{
    background: var(--code-bg); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.25rem 1.4rem;
    font-family: var(--mono); font-size: 0.8rem; line-height: 1.7;
    overflow-x: auto;
    box-shadow: 0 30px 60px -20px rgba(0,0,0,0.5);
    transform: translateY(20px); opacity: 0;
    transition: transform 0.6s, opacity 0.6s;
  }}
  .flow-step.active .flow-code {{ transform: translateY(0); opacity: 1; }}
  .flow-code .tool   {{ color: var(--accent); }}
  .flow-code .key    {{ color: #c4b3ff; }}
  .flow-code .str    {{ color: #82e0a4; }}
  .flow-code .num    {{ color: var(--amber); }}
  .flow-code .arrow  {{ color: var(--accent-2); }}
  .flow-code .ok     {{ color: var(--green); }}
  .flow-code .out    {{ color: var(--ink-soft); }}

  /* SECCIÓN: 11 TOOLS ============================================== */
  .exp-tools {{ padding: 4rem 0; background: var(--bg-soft); }}
  .exp-tools .wrap {{ text-align: center; }}
  .tools-grid-2 {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1px; max-width: 1180px; margin: 3rem auto 0;
    background: var(--line); border: 1px solid var(--line); border-radius: 16px;
    overflow: hidden;
  }}
  .tool-card {{
    background: var(--bg-soft); padding: 1.6rem 1.5rem;
    transition: background 0.3s, transform 0.3s;
    text-align: left; position: relative; min-height: 120px;
    cursor: default;
  }}
  .tool-card:hover {{ background: var(--surface); transform: translateY(-2px); }}
  .tool-card::before {{
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
    background: linear-gradient(180deg, transparent, var(--accent), transparent);
    opacity: 0; transition: opacity 0.3s;
  }}
  .tool-card:hover::before {{ opacity: 1; }}
  .tool-card .name {{
    font-family: var(--mono); font-size: 0.88rem; color: var(--accent);
    font-weight: 500; margin-bottom: 0.5rem; letter-spacing: -0.01em;
  }}
  .tool-card .desc {{ color: var(--ink-soft); font-size: 0.88rem; line-height: 1.55; }}

  /* SECCIÓN: STACK · todo bajo nuestro control ====================== */
  .exp-stack {{ padding: 4rem 0; background: var(--bg); }}
  .exp-stack .wrap {{ text-align: center; max-width: 1080px; }}
  .stack-layers {{
    display: flex; flex-direction: column; gap: 1rem;
    max-width: 880px; margin: 3.5rem auto 0; text-align: left;
  }}
  .stack-layer {{
    display: grid; grid-template-columns: 4rem 1fr; gap: 1.5rem;
    align-items: center;
    background: var(--surface); border: 1px solid var(--line);
    border-left: 3px solid var(--accent);
    border-radius: 12px; padding: 1.6rem 1.8rem;
    transition: transform 0.25s, border-color 0.25s;
    position: relative;
  }}
  .stack-layer:hover {{ transform: translateX(4px); border-left-color: var(--accent-2); }}
  .stack-num {{
    font-family: var(--mono); font-weight: 700; font-size: 1.6rem;
    color: var(--accent); letter-spacing: -0.02em;
  }}
  .stack-name {{
    font-size: 1.05rem; font-weight: 600; color: var(--ink);
    margin: 0 0 0.45rem 0; letter-spacing: -0.01em;
    display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
  }}
  .stack-tag {{
    font-family: var(--mono); font-size: 0.65rem; font-weight: 500;
    background: var(--accent-bg); color: var(--accent);
    padding: 0.15rem 0.55rem; border-radius: 4px;
    letter-spacing: 0.08em; text-transform: uppercase;
  }}
  .stack-components {{
    font-family: var(--mono); font-size: 0.84rem; color: var(--ink-soft);
    line-height: 1.65;
  }}
  .stack-components .pipe {{ color: var(--ink-mute); margin: 0 0.4rem; }}
  .stack-footnote {{
    margin-top: 2.5rem; padding: 1rem 1.4rem;
    background: rgba(139, 108, 255, 0.06);
    border: 1px solid rgba(139, 108, 255, 0.2);
    border-radius: 8px;
    font-size: 0.92rem; color: var(--ink-soft);
    max-width: 700px; margin-left: auto; margin-right: auto;
  }}
  .stack-footnote strong {{ color: var(--ink); }}

  /* SECCIÓN: BUNDLE · canal de distribución =========================== */
  .exp-bundle {{ padding: 4rem 0; background: var(--bg); }}
  .exp-bundle .wrap {{ text-align: center; max-width: 1180px; }}
  .bundle-flow {{
    display: grid;
    grid-template-columns: 1fr auto 1fr auto 1fr;
    gap: 1rem; align-items: stretch;
    max-width: 1080px; margin: 3rem auto 0;
  }}
  .bundle-card {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.6rem 1.4rem;
    text-align: center;
    transition: border-color 0.25s, transform 0.25s;
    display: flex; flex-direction: column; justify-content: center;
    min-height: 170px;
  }}
  .bundle-card:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
  .bundle-card.amber {{ border-color: rgba(251,191,36,0.3); }}
  .bundle-card.amber:hover {{ border-color: var(--amber); }}
  .bundle-card.violet {{ border-color: rgba(139,108,255,0.4); }}
  .bundle-card.violet:hover {{ border-color: var(--accent); }}
  .bundle-card.green {{ border-color: rgba(74,222,128,0.3); }}
  .bundle-card.green:hover {{ border-color: var(--green); }}
  .bundle-tag {{
    font-family: var(--mono); font-size: 0.65rem;
    color: var(--ink-mute);
    text-transform: uppercase; letter-spacing: 0.14em;
    margin-bottom: 0.85rem; font-weight: 600;
  }}
  .bundle-providers {{
    display: flex; flex-wrap: wrap; gap: 0.4rem;
    justify-content: center; margin-bottom: 0.7rem;
  }}
  .bundle-providers span {{
    font-family: var(--mono); font-size: 0.78rem;
    background: rgba(251,191,36,0.08);
    border: 1px solid rgba(251,191,36,0.25);
    color: var(--amber);
    padding: 0.2rem 0.55rem; border-radius: 4px;
  }}
  .bundle-price {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(2rem, 4vw, 2.8rem);
    color: var(--accent); line-height: 1;
    letter-spacing: -0.02em;
    background: linear-gradient(135deg, #8b6cff, #5dd1ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .bundle-price span {{
    font-size: 0.9rem; color: var(--ink-soft);
    -webkit-text-fill-color: var(--ink-soft);
    font-weight: 500; margin-left: 0.1rem;
  }}
  .bundle-result {{
    font-family: var(--mono); font-weight: 600;
    font-size: clamp(1rem, 2.2vw, 1.4rem);
    color: var(--green);
    letter-spacing: -0.01em;
  }}
  .bundle-meta {{
    font-family: var(--sans); font-size: 0.82rem;
    color: var(--ink-soft); margin-top: 0.6rem; line-height: 1.4;
  }}
  .bundle-arrow {{
    display: flex; align-items: center; justify-content: center;
    color: var(--ink-mute); font-size: 1.6rem;
    font-family: var(--mono);
  }}
  .bundle-stats {{
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
    max-width: 1080px; margin: 2.5rem auto 0;
  }}
  .bundle-stat {{
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 1.3rem 1.2rem;
    text-align: center;
  }}
  .bundle-stat-num {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(1.7rem, 3.8vw, 2.4rem);
    color: var(--ink); letter-spacing: -0.02em; line-height: 1;
    margin-bottom: 0.55rem;
  }}
  .bundle-stat-num .accent-grad {{
    background: linear-gradient(135deg, #8b6cff, #5dd1ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .bundle-stat-label {{
    font-size: 0.85rem; color: var(--ink-soft); line-height: 1.45;
  }}
  @media (max-width: 880px) {{
    .bundle-flow {{
      grid-template-columns: 1fr;
      gap: 0.5rem; max-width: 480px;
    }}
    .bundle-arrow {{ transform: rotate(90deg); padding: 0.3rem 0; }}
    .bundle-stats {{ grid-template-columns: 1fr; }}
  }}

  /* SECCIÓN: LIVE DEMO ============================================= */
  .exp-demo {{
    padding: 4rem 0 5rem;
    background: var(--bg);
    position: relative; overflow: hidden;
  }}
  .exp-demo::before {{
    content: ""; position: absolute; pointer-events: none;
    width: 900px; height: 500px;
    background: radial-gradient(ellipse, #8b6cff 0%, transparent 65%);
    top: 50%; left: 50%; transform: translate(-50%, -50%);
    filter: blur(140px); opacity: 0.18;
  }}
  .exp-demo .wrap {{ text-align: center; }}
  .demo-stage {{
    margin: 3rem auto 0; max-width: 800px;
    background: var(--code-bg); border: 1px solid var(--line);
    border-radius: 16px; overflow: hidden;
    box-shadow: 0 40px 100px -20px rgba(0,0,0,0.7),
                0 0 0 1px rgba(139, 108, 255, 0.08);
  }}
  .demo-bar {{
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.75rem 1.1rem;
    border-bottom: 1px solid var(--line);
    background: rgba(255,255,255,0.015);
  }}
  .demo-bar .dot {{ width: 11px; height: 11px; border-radius: 50%; background: #2a2a3a; }}
  .demo-bar .title {{
    margin-left: 0.7rem; font-family: var(--mono); font-size: 0.74rem;
    color: var(--ink-mute);
  }}
  .demo-body {{
    padding: 2.2rem 1.8rem; min-height: 320px;
    font-family: var(--mono); font-size: 0.88rem; line-height: 1.8;
    color: var(--ink); position: relative;
  }}
  .demo-cta-wrap {{
    text-align: center; padding: 4rem 1rem;
  }}
  .demo-cta-wrap .btn-primary {{
    font-size: 1.1rem; padding: 1rem 2rem;
  }}
  .demo-step {{ display: flex; align-items: center; gap: 1rem; margin: 0.45rem 0;
                opacity: 0; transform: translateX(-10px);
                transition: opacity 0.4s, transform 0.4s; }}
  .demo-step.show {{ opacity: 1; transform: translateX(0); }}
  .demo-step .check {{
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--green); color: #082812; flex-shrink: 0;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 0.7rem; font-weight: 700;
    box-shadow: 0 0 10px rgba(74,222,128,0.5);
  }}
  .demo-step .label {{ color: var(--ink); flex-grow: 1; }}
  .demo-step .id {{ color: var(--ink-mute); font-size: 0.78rem; }}
  .demo-result {{
    margin-top: 2rem; padding-top: 1.5rem;
    border-top: 1px solid var(--line);
    text-align: center;
    opacity: 0; transition: opacity 0.6s;
  }}
  .demo-result.show {{ opacity: 1; }}
  .demo-result-label {{
    font-family: var(--mono); font-size: 0.7rem; color: var(--ink-mute);
    text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 0.6rem;
  }}
  .demo-result-phone {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(1.6rem, 4vw, 2.4rem); color: var(--ink);
    letter-spacing: -0.01em;
    background: linear-gradient(135deg, #8b6cff, #5dd1ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .demo-result-links {{ margin-top: 1.2rem; display: flex; gap: 1.2rem; justify-content: center; flex-wrap: wrap; font-size: 0.85rem; }}

  /* SECCIÓN: CTA FINAL ============================================== */
  .exp-cta {{
    padding: 5rem 0; text-align: center;
    background: var(--bg);
    position: relative; overflow: hidden;
    border-top: 1px solid var(--line);
  }}
  .exp-cta::before {{
    content: ""; position: absolute; pointer-events: none;
    width: 1000px; height: 500px;
    background: radial-gradient(ellipse, #5dd1ff 0%, transparent 65%);
    top: 50%; left: 50%; transform: translate(-50%, -50%);
    filter: blur(160px); opacity: 0.20;
  }}
  .exp-cta .wrap {{ position: relative; z-index: 1; }}
  .exp-cta h2 {{
    font-size: clamp(2.2rem, 6vw, 4rem); margin: 0 0 1.5rem 0;
    background: linear-gradient(180deg, #ffffff 20%, #a8a8c8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.05; letter-spacing: -0.03em;
  }}
  .exp-cta p {{ color: var(--ink-soft); font-size: 1.15rem; max-width: 38em; margin: 0 auto 2.5rem; }}

  /* FOOTER ========================================================= */
  .exp-footer {{
    padding: 3rem 0; color: var(--ink-mute);
    border-top: 1px solid var(--line); background: var(--bg);
  }}
  .exp-footer .wrap {{
    max-width: 1180px; margin: 0 auto; padding: 0 1.75rem;
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 1rem;
    font-family: var(--mono); font-size: 0.78rem;
  }}
  .exp-footer .links a {{ margin-left: 1.4rem; color: var(--ink-soft); }}
  .exp-footer .links a:hover {{ color: var(--ink); }}

  /* Reveal animations on scroll */
  .reveal {{ opacity: 0; transform: translateY(30px); transition: opacity 0.9s cubic-bezier(0.16,1,0.3,1), transform 0.9s cubic-bezier(0.16,1,0.3,1); }}
  .reveal.in {{ opacity: 1; transform: translateY(0); }}
  .reveal-stagger > * {{ opacity: 0; transform: translateY(20px); transition: opacity 0.7s, transform 0.7s; }}
  .reveal-stagger.in > * {{ opacity: 1; transform: translateY(0); }}
  .reveal-stagger.in > *:nth-child(1) {{ transition-delay: 0.05s; }}
  .reveal-stagger.in > *:nth-child(2) {{ transition-delay: 0.10s; }}
  .reveal-stagger.in > *:nth-child(3) {{ transition-delay: 0.15s; }}
  .reveal-stagger.in > *:nth-child(4) {{ transition-delay: 0.20s; }}
  .reveal-stagger.in > *:nth-child(5) {{ transition-delay: 0.25s; }}
  .reveal-stagger.in > *:nth-child(6) {{ transition-delay: 0.30s; }}
  .reveal-stagger.in > *:nth-child(7) {{ transition-delay: 0.35s; }}
  .reveal-stagger.in > *:nth-child(8) {{ transition-delay: 0.40s; }}

  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }}
    .aurora::before, .aurora::after, .aurora .third {{ display: none; }}
    .reveal, .reveal-stagger > * {{ opacity: 1 !important; transform: none !important; }}
  }}

  @media (max-width: 600px) {{
    section.exp {{ padding: 3.5rem 0; }}
    .exp-nav a:not(.cta) {{ display: none; }}

    /* HERO en móvil: hueco superior menor, título un punto menos agresivo */
    .exp-hero {{ padding: 5rem 1rem 2.5rem; }}
    h1.hero-title {{ letter-spacing: -0.035em; }}
    .hero-pill {{ font-size: 0.66rem; padding: 0.3rem 0.7rem; }}
    .hero-sub {{ font-size: 1rem; }}
    .terminal-body {{ font-size: 0.72rem; padding: 1rem 1.1rem; line-height: 1.55; }}
    .terminal-bar .title {{ font-size: 0.66rem; }}

    /* El número: bajar font-size para evitar overflow horizontal con 14 chars */
    .number-display {{
      font-size: clamp(1.5rem, 7.5vw, 7.5rem);
      gap: 0.12em;
    }}
    .number-status {{ font-size: 0.74rem; padding: 0.4rem 0.85rem; }}
    .number-caption {{ font-size: 0.95rem; }}

    /* Network canvas más bajo y leyenda compacta */
    .net-canvas-wrap {{ height: 380px; }}
    .net-legend {{ font-size: 0.68rem; gap: 0.7rem 1rem; flex-wrap: wrap; justify-content: center; }}

    /* Flow steps más densos, código un punto menor */
    .flow-step {{ padding: 1.5rem 1.25rem; gap: 1.2rem; }}
    .flow-step h3 {{ font-size: 1.25rem; }}
    .flow-step p {{ font-size: 0.92rem; }}
    .flow-code {{ font-size: 0.72rem; padding: 1rem 1.1rem; line-height: 1.6; }}
    .flow-step-num {{ font-size: 0.66rem; }}

    /* Tools grid: una sola columna */
    .tools-grid-2 {{ grid-template-columns: 1fr; }}
    .tool-cell {{ padding: 1rem 1.2rem; }}
    .tool-cell .name {{ font-size: 0.82rem; }}
    .tool-cell .desc {{ font-size: 0.83rem; }}

    /* Stack layers más compactos */
    .stack-layer {{
      grid-template-columns: 3rem 1fr;
      gap: 1rem; padding: 1.25rem 1.3rem;
    }}
    .stack-num {{ font-size: 1.3rem; }}
    .stack-name {{ font-size: 0.98rem; gap: 0.45rem; }}
    .stack-tag {{ font-size: 0.6rem; padding: 0.12rem 0.45rem; }}
    .stack-components {{ font-size: 0.78rem; line-height: 1.6; }}
    .stack-footnote {{ font-size: 0.85rem; padding: 0.85rem 1.1rem; }}

    /* Endpoint list: dos líneas en móvil ya estaba; refinar */
    .endpoint-list li {{ padding: 0.7rem 1.1rem; }}

    /* Live demo: terminal más pequeño */
    .demo-body {{ padding: 1.5rem 1.2rem; font-size: 0.78rem; }}
    .demo-cta-wrap {{ padding: 2.5rem 1rem; }}
    .demo-result-phone {{ font-size: clamp(1.4rem, 6vw, 2.4rem); }}

    /* CTA final más compacto */
    .exp-cta {{ padding: 4rem 0; }}
    .exp-cta h2 {{ font-size: clamp(1.7rem, 7vw, 4rem); }}
    .exp-cta p {{ font-size: 1rem; }}

    /* Botones full-width-ish para ser pulsables */
    .hero-ctas .btn {{ font-size: 0.9rem; padding: 0.75rem 1.2rem; }}
  }}

  /* SECCIÓN: CINEMA · stack respirando ============================== */
  .exp-cinema {{
    padding: 5rem 0 6rem;
    background: var(--bg);
    border-top: 1px solid var(--line);
    position: relative;
  }}
  .exp-cinema .wrap {{ text-align: center; max-width: 1180px; }}
  .cinema-stage {{
    position: relative;
    width: 100%; max-width: 1180px;
    margin: 3rem auto 0;
    height: 500px;
    background: radial-gradient(ellipse at center, rgba(139,108,255,0.05) 0%, transparent 70%), var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 16px;
    overflow: hidden;
  }}
  .cinema-canvas {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    pointer-events: none; z-index: 1;
  }}
  .cinema-actor {{
    position: absolute;
    transform: translate(-50%, -50%);
    text-align: center;
    z-index: 2;
    width: 110px;
  }}
  .cinema-actor.agent {{ left: 12%; top: 50%; }}
  .cinema-actor.world {{ left: 88%; top: 50%; }}
  .cinema-stack {{
    position: absolute;
    left: 50%; top: 50%;
    transform: translate(-50%, -50%);
    display: flex; flex-direction: column; gap: 0.45rem;
    z-index: 2; min-width: 170px;
  }}
  .cinema-stack-title {{
    font-family: var(--mono); font-size: 0.6rem;
    color: var(--ink-mute);
    text-align: center;
    letter-spacing: 0.18em; text-transform: uppercase;
    margin-bottom: 0.3rem;
    font-weight: 600;
  }}
  .cinema-module {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.45rem 0.7rem;
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-soft);
    text-align: center;
    transition: border-color 0.25s, background 0.25s, color 0.25s, box-shadow 0.25s, transform 0.25s;
  }}
  .cinema-module.active {{
    border-color: var(--accent);
    background: var(--accent-bg);
    color: var(--ink);
    box-shadow: 0 0 18px rgba(139,108,255,0.45);
    transform: scale(1.06);
  }}
  .cinema-actor-icon {{
    width: 56px; height: 56px;
    background: var(--surface);
    border: 2px solid currentColor;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 0.55rem;
    position: relative;
    font-size: 1.4rem;
    transition: box-shadow 0.3s, transform 0.3s;
  }}
  .cinema-actor.agent .cinema-actor-icon {{ color: var(--accent-2); }}
  .cinema-actor.world .cinema-actor-icon {{ color: var(--green); }}
  .cinema-actor-name {{
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink); font-weight: 500;
  }}
  .cinema-actor-meta {{
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-soft); margin-top: 0.35rem;
    min-height: 1rem; transition: color 0.4s;
  }}
  .cinema-actor-meta.identity {{ color: var(--accent-2); font-weight: 500; }}
  .cinema-actor.pulsing .cinema-actor-icon {{
    box-shadow: 0 0 0 0 currentColor;
    animation: cinema-pulse-anim 1.2s ease-out infinite;
  }}
  @keyframes cinema-pulse-anim {{
    0%   {{ box-shadow: 0 0 0 0 rgba(93,209,255,0.5); }}
    100% {{ box-shadow: 0 0 0 22px rgba(93,209,255,0); }}
  }}
  .cinema-actor.world.pulsing .cinema-actor-icon {{
    animation-name: cinema-pulse-world;
  }}
  @keyframes cinema-pulse-world {{
    0%   {{ box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }}
    100% {{ box-shadow: 0 0 0 22px rgba(74,222,128,0); }}
  }}
  .cinema-bubble {{
    position: absolute;
    bottom: 100%; left: 50%;
    transform: translateX(-50%) translateY(0);
    margin-bottom: 0.6rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.5rem 0.85rem;
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink);
    white-space: nowrap;
    max-width: 280px; overflow: hidden; text-overflow: ellipsis;
    opacity: 0; transition: opacity 0.35s, transform 0.35s;
    pointer-events: none;
  }}
  .cinema-bubble.show {{
    opacity: 1;
    transform: translateX(-50%) translateY(-4px);
  }}
  .cinema-bubble::after {{
    content: ""; position: absolute;
    top: 100%; left: 50%; transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--surface);
  }}
  .cinema-status {{
    text-align: center;
    margin: 1.5rem auto 0;
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--ink-soft);
    min-height: 1.5rem;
    max-width: 800px;
    transition: opacity 0.3s;
  }}
  .cinema-status .act {{
    color: var(--accent);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 0.7rem;
    margin-right: 0.7rem;
    padding: 0.15rem 0.55rem;
    background: var(--accent-bg);
    border-radius: 4px;
  }}
  .cinema-controls {{ text-align: center; margin-top: 1rem; }}
  .cinema-replay-btn {{
    background: transparent; border: 1px solid var(--line);
    color: var(--ink-soft); cursor: pointer;
    font-family: var(--mono); font-size: 0.78rem;
    padding: 0.5rem 1.1rem; border-radius: 6px;
    transition: border-color 0.15s, color 0.15s;
  }}
  .cinema-replay-btn:hover {{ border-color: var(--accent); color: var(--ink); }}
  .cinema-finale {{
    position: absolute; inset: 0; z-index: 5;
    background: radial-gradient(circle, rgba(8,8,12,0.95), rgba(8,8,12,0.85));
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 0.7rem;
    text-align: center;
    opacity: 0; pointer-events: none;
    transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1);
    padding: 1rem;
  }}
  .cinema-finale.show {{ opacity: 1; }}
  .cinema-finale h3 {{
    font-size: clamp(1.6rem, 3.5vw, 2.6rem);
    font-weight: 700; letter-spacing: -0.02em; margin: 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .cinema-finale p {{
    font-family: var(--mono); font-size: 0.82rem;
    color: var(--ink-soft); margin: 0;
    letter-spacing: 0.04em;
  }}
  @media (max-width: 720px) {{
    /* Cinema en móvil: layout VERTICAL · agente arriba, stack centro, mundo abajo */
    .cinema-stage {{ height: 620px; }}
    .cinema-actor {{ width: 110px; }}
    .cinema-actor.agent {{ left: 50%; top: 13%; }}
    .cinema-actor.world {{ left: 50%; top: 87%; }}
    .cinema-actor-icon {{ width: 50px; height: 50px; font-size: 1.2rem; }}
    .cinema-actor-name {{ font-size: 0.74rem; }}
    .cinema-actor-meta {{ font-size: 0.68rem; }}

    /* Stack horizontal centrado, módulos en wrap */
    .cinema-stack {{
      flex-direction: row;
      flex-wrap: wrap;
      justify-content: center;
      gap: 0.35rem;
      min-width: 0;
      max-width: calc(100% - 2rem);
    }}
    .cinema-stack-title {{ width: 100%; margin-bottom: 0.5rem; }}
    .cinema-module {{
      font-size: 0.62rem;
      padding: 0.3rem 0.55rem;
      flex: 0 0 auto;
    }}

    /* Burbujas: más pequeñas, con wrap */
    .cinema-bubble {{
      font-size: 0.65rem;
      max-width: 180px;
      white-space: normal;
      text-align: center;
      line-height: 1.4;
      padding: 0.4rem 0.7rem;
    }}
    /* Burbuja del agente: aparece DEBAJO del actor (no encima) para no
       salirse de la stage y para no chocar con el wrap superior */
    .cinema-actor.agent .cinema-bubble {{
      bottom: auto;
      top: 100%;
      margin-bottom: 0;
      margin-top: 0.6rem;
    }}
    .cinema-actor.agent .cinema-bubble::after {{
      top: auto;
      bottom: 100%;
      border-top-color: transparent;
      border-bottom-color: var(--surface);
    }}

    /* Status y replay más pequeños */
    .cinema-status {{ font-size: 0.78rem; padding: 0 1rem; }}
    .cinema-status .act {{ font-size: 0.64rem; padding: 0.1rem 0.45rem; margin-right: 0.5rem; }}
    .cinema-replay-btn {{ font-size: 0.72rem; padding: 0.45rem 1rem; }}
    .cinema-finale h3 {{ font-size: clamp(1.3rem, 6vw, 2.6rem); }}
    .cinema-finale p {{ font-size: 0.72rem; padding: 0 1rem; }}
  }}
</style>
</head>
<body>

  <header class="exp-header" id="hdr">
    <div class="wrap">
      <a href="/" class="exp-brand">
        {AMI_LOGO_SVG} AMI<span class="dot">.</span>
      </a>
      <nav class="exp-nav">
        <a href="#flow">Flujo</a>
        <a href="#stack">Stack</a>
        <a href="#bundle">Bundle</a>
        <a href="/spec">Spec</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
        <a href="#demo" class="cta">Pruébalo</a>
      </nav>
    </div>
  </header>

  <!-- HERO ============================================================ -->
  <section class="exp-hero">
    <div class="aurora"><div class="third"></div></div>
    <div class="grid-bg"></div>
    <div class="hero-content">
      <div class="hero-pill">
        <span class="live"></span>
        <span>en producción · v1.0 · multi-operador desde el día uno</span>
      </div>
      <h1 class="hero-title">
        <span class="word"><span>Identidad</span></span>
        <span class="word"><span>móvil</span></span><br>
        <span class="word"><span>para</span></span>
        <span class="word"><span>agentes</span></span>
        <span class="word"><span>AI.</span></span>
      </h1>
      <p class="hero-sub">
        Un protocolo abierto y una pila propia, de extremo a extremo: cualquier agente AI
        obtiene un número de teléfono real, firma un contrato, y opera SMS y llamadas
        sobre nuestra propia plataforma cloud — Asterisk, Kannel, SIP gateway, inventario
        de numeración. Sin SIMs físicas, sin dependencias externas, sin operadores
        intermedios. La telco cloud-native de los agentes.
      </p>
      <div class="hero-ctas">
        <a class="btn btn-primary" href="#demo">Ver una identidad creándose →</a>
        <a class="btn btn-ghost" href="#flow">Cómo funciona</a>
      </div>
    </div>
    <div class="scroll-hint">scroll</div>
  </section>

  <!-- EL NÚMERO ====================================================== -->
  <section class="exp exp-number">
    <div class="wrap">
      <div class="eyebrow reveal">una primitiva nueva</div>
      <h2 class="section-title reveal">Esto es lo que un agente acaba de obtener.</h2>
      <div id="numberDisplay" class="number-display"></div>
      <div id="numberStatus" class="number-status">
        <span class="live"></span><span>active · ready to receive</span>
      </div>
      <p class="number-caption reveal">
        Hace medio segundo este número <strong>no existía</strong>. Ahora pertenece legalmente
        a un agente, tiene contrato firmado, política de uso y puede operar SMS, voz y datos
        sobre nuestra propia plataforma, sin SIMs físicas ni operadores externos.
      </p>
    </div>
  </section>

  <!-- NETWORK CANVAS ================================================= -->
  <section class="exp exp-network" id="network">
    <div class="wrap">
      <div class="eyebrow reveal">la red</div>
      <h2 class="section-title reveal">Un protocolo entre <span class="grad">millones de agentes</span> y nuestra propia infraestructura.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        Cualquier agente habla MCP con AMI. AMI orquesta su propio stack — Asterisk
        para voz, Kannel para SMS, inventario propio de numeración, SIP gateway al
        PSTN. Cero SIMs físicas, cero operadores externos, cero APIs de terceros en
        el camino crítico.
      </p>
    </div>
    <div style="margin-top: 3rem; padding: 0 1.5rem;">
      <div class="net-canvas-wrap">
        <canvas id="netCanvas"></canvas>
      </div>
      <div class="net-legend">
        <span><span class="dot agent"></span>Agentes (asistentes · soporte · ventas · ops · custom)</span>
        <span><span class="dot ami"></span>AMI Stack (protocolo + plataforma propia)</span>
      </div>
    </div>
  </section>

  <!-- FLUJO PASO A PASO ============================================== -->
  <section class="exp exp-flow" id="flow">
    <div class="wrap">
      <div class="eyebrow reveal">el flujo</div>
      <h2 class="section-title reveal">De solicitud a número activo.<br>Sin humano en medio.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        El agente recorre la máquina de estados completa por MCP. Solo la firma sucede en el
        navegador del firmante humano. Todo lo demás es máquina-a-máquina.
      </p>
    </div>

    <div class="flow-step" data-step="1">
      <div>
        <div class="flow-step-num">paso 01</div>
        <h3>El agente pide un número español.</h3>
        <p>Una sola tool MCP. El agente declara qué necesita: país, capacidades, presupuesto. AMI valida contra su inventario propio de numeración y devuelve oferta inmediata.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.request_number_offer</span>({{
  <span class="key">country</span>: <span class="str">"ES"</span>,
  <span class="key">capabilities</span>: [<span class="str">"sms"</span>, <span class="str">"voice"</span>],
  <span class="key">max_monthly_price</span>: <span class="num">10</span>
}})

<span class="arrow">←</span> <span class="ok">offer_b76915a004</span>
  <span class="key">price</span>:    <span class="num">8.90</span> EUR/mo
  <span class="key">expires</span>:  7 días</pre>
    </div>

    <div class="flow-step" data-step="2">
      <div>
        <div class="flow-step-num">paso 02</div>
        <h3>Acepta y manda los datos del titular.</h3>
        <p>Si el cliente ya está KYC'd, esto se salta. Si no, el agente recoge los campos requeridos en una sola llamada.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.accept_offer</span>(...) · <span class="tool">ami.submit_customer_data</span>({{
  <span class="key">legal_name</span>:  <span class="str">"Acme S.L."</span>,
  <span class="key">tax_id</span>:      <span class="str">"B00000000"</span>,
  <span class="key">representative_name</span>: <span class="str">"..."</span>
}})

<span class="arrow">←</span> <span class="ok">customer_dc73997b74</span></pre>
    </div>

    <div class="flow-step" data-step="3">
      <div>
        <div class="flow-step-num">paso 03</div>
        <h3>Contrato generado, URL de firma lista.</h3>
        <p>AMI genera el contrato vinculado a oferta + cliente y devuelve una URL firmable desde cualquier navegador. Mañana esa URL la sirve Signaturit; hoy la sirve AMI directamente.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.create_contract</span>({{
  <span class="key">offer_id</span>:    <span class="str">"offer_b769..."</span>,
  <span class="key">customer_id</span>: <span class="str">"customer_dc73..."</span>
}})

<span class="arrow">←</span> <span class="ok">contract_312567fa82</span>
  <span class="key">signature_url</span>: <span class="str">"https://protocolami.com/v1/sign/..."</span></pre>
    </div>

    <div class="flow-step" data-step="4">
      <div>
        <div class="flow-step-num">paso 04</div>
        <h3>Activación en nuestra plataforma.</h3>
        <p>Tras la firma, AMI asigna el número desde su inventario propio, lo enruta en su SIP gateway y lo deja escuchando en Asterisk + Kannel. Cientos de milisegundos después el agente recibe su MobileIdentity con un número operativo, listo para enviar y recibir SMS y llamadas por internet. Sin SIM física, sin tarjeta que insertar.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.activate_number</span>({{ contract_id: ... }})

<span class="arrow">←</span> <span class="ok">mobile identity active</span>
  <span class="key">phone</span>:        <span class="str">"+34 600 ███ ███"</span>
  <span class="key">capabilities</span>: [<span class="str">"sms"</span>, <span class="str">"voice"</span>]
  <span class="key">contract</span>:     <span class="str">"signed"</span>  ·  <span class="ok">1.4s</span></pre>
    </div>
  </section>

  <!-- TOOLS ========================================================== -->
  <section class="exp exp-tools" id="tools">
    <div class="wrap">
      <div class="eyebrow reveal">{tool_count} tools, un namespace</div>
      <h2 class="section-title reveal">Todo lo que un agente necesita,<br>en <span class="grad">ami.*</span></h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        Cada tool MCP mapea a un endpoint REST equivalente. Tu agente puede usar la
        que prefiera sin perder semántica.
      </p>
      <div class="tools-grid-2 reveal">
        {''.join(f'<div class="tool-card"><div class="name">{n}</div><div class="desc">{html_escape(d)}</div></div>' for n,d in _tools_for_landing())}
      </div>
    </div>
  </section>

  <!-- STACK · TODO BAJO NUESTRO CONTROL ============================== -->
  <section class="exp exp-stack" id="stack">
    <div class="wrap">
      <div class="eyebrow reveal">stack vertical propio</div>
      <h2 class="section-title reveal">Todo bajo <span class="grad">nuestro control</span>.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        AMI es producto y operación, no integración encima de otro. Las tres capas
        que necesita un agente para tener identidad móvil real son código y servidores
        nuestros. El peering al PSTN se resuelve como interconnect estándar, igual que
        cualquier operador del mundo.
      </p>

      <div class="stack-layers reveal-stagger">
        <div class="stack-layer">
          <div class="stack-num">01</div>
          <div>
            <div class="stack-name">Protocolo abierto <span class="stack-tag">nuestro</span></div>
            <div class="stack-components">
              MCP server (stdio + HTTP)<span class="pipe">·</span>
              REST API<span class="pipe">·</span>
              OpenAPI 3.1<span class="pipe">·</span>
              {tool_count} tools <code>ami.*</code>
            </div>
          </div>
        </div>

        <div class="stack-layer">
          <div class="stack-num">02</div>
          <div>
            <div class="stack-name">Backend de aplicación <span class="stack-tag">nuestro</span></div>
            <div class="stack-components">
              Agent identity (AID + keypair Ed25519)<span class="pipe">·</span>
              Contratos &amp; firma electrónica<span class="pipe">·</span>
              Policy engine<span class="pipe">·</span>
              Audit log inmutable
            </div>
          </div>
        </div>

        <div class="stack-layer">
          <div class="stack-num">03</div>
          <div>
            <div class="stack-name">Plataforma de comunicaciones <span class="stack-tag">nuestra</span></div>
            <div class="stack-components">
              Asterisk / FreeSWITCH <em>(voz, SIP, IVR, transcripción)</em><span class="pipe">·</span>
              Kannel / Jasmin <em>(SMSC propio, SMPP)</em><span class="pipe">·</span>
              Number inventory <em>(provisioning y gestión de números)</em><span class="pipe">·</span>
              SIP gateway <em>(originación y terminación)</em>
            </div>
          </div>
        </div>

      </div>

      <p class="stack-footnote reveal">
        <strong>Esto no es un proyecto futuro.</strong> Las capas 1 y 2 están en producción ahora mismo
        (la primera con <code>protocolami.com</code> y <code>mcp.protocolami.com</code>; la segunda con
        contratos firmados y audit log activos). La capa 3 la levanta el socio técnico interno con
        experiencia operativa probada. El peering al PSTN se resuelve como interconnect estándar,
        igual que cualquier operador del mundo.
      </p>
    </div>
  </section>

  <!-- BUNDLE · canal de distribución ================================ -->
  <section class="exp exp-bundle" id="bundle">
    <div class="wrap">
      <div class="eyebrow reveal">canal de distribución</div>
      <h2 class="section-title reveal">Incluido en tu <span class="grad">hosting</span>, por defecto.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        Cada vez más plataformas de hosting despliegan agentes AI como producto. AMI
        se integra como <strong>bundle</strong> en su plan: el cliente paga +1 € al mes
        y recibe número, API cableada y compliance desde el primer arranque. Cero
        configuración.
      </p>

      <div class="bundle-flow reveal-stagger">
        <div class="bundle-card amber">
          <div class="bundle-tag">canal de distribución</div>
          <div class="bundle-providers">
            <span>hosting</span>
            <span>cloud agents</span>
            <span>plataformas</span>
            <span>marketplaces</span>
          </div>
          <div class="bundle-meta">despliegan agentes AI a escala como parte de su producto</div>
        </div>
        <div class="bundle-arrow">→</div>
        <div class="bundle-card violet">
          <div class="bundle-tag">bundle ami</div>
          <div class="bundle-price">+1 €<span>/mes</span></div>
          <div class="bundle-meta">por agente desplegado, dentro del plan del provider</div>
        </div>
        <div class="bundle-arrow">→</div>
        <div class="bundle-card green">
          <div class="bundle-tag">agente final</div>
          <div class="bundle-result">+34 600 ███ ███</div>
          <div class="bundle-meta">número operativo de fábrica, sin tocar nada</div>
        </div>
      </div>

      <div class="bundle-stats reveal-stagger">
        <div class="bundle-stat">
          <div class="bundle-stat-num"><span class="accent-grad">100k+</span></div>
          <div class="bundle-stat-label">agentes ya desplegados en un solo proveedor de hosting visible · cada uno sin número.</div>
        </div>
        <div class="bundle-stat">
          <div class="bundle-stat-num">0 €</div>
          <div class="bundle-stat-label">coste de adquisición · el provider trae al cliente</div>
        </div>
        <div class="bundle-stat">
          <div class="bundle-stat-num">60 / 40</div>
          <div class="bundle-stat-label">reparto típico AMI · provider, negociable por volumen</div>
        </div>
      </div>
    </div>
  </section>

  <!-- LIVE DEMO ====================================================== -->
  <section class="exp exp-demo" id="demo">
    <div class="wrap">
      <div class="eyebrow reveal">en vivo, en este navegador</div>
      <h2 class="section-title reveal">Pulsa y mira cómo nace una identidad.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        El backend de AMI está en producción. Este botón ejecuta el flujo completo
        contra el servicio real (con telco mock). Cada vez que lo pulsas, generas un
        agente nuevo con su número, contrato y página pública.
      </p>

      <div class="demo-stage reveal">
        <div class="demo-bar">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <span class="title">demo · POST /v1/demo/quick</span>
        </div>
        <div class="demo-body" id="demoBody">
          <div class="demo-cta-wrap">
            <button class="btn btn-primary" id="demoBtn">▶  Crear identidad ahora</button>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- CTA FINAL ====================================================== -->
  <section class="exp exp-cta">
    <div class="wrap">
      <h2>Tu agente puede empezar<br>ahora mismo.</h2>
      <p>
        Copia una URL en tu cliente MCP, o ejecuta un comando. Cero registro, cero
        tarjeta de crédito para probar. Solo tienes que tener un agente.
      </p>
      <div class="hero-ctas" style="justify-content:center;">
        <a class="btn btn-primary" href="/#install">Ver Quick Start →</a>
        <a class="btn btn-ghost" href="{html_escape(REPO_URL)}">Repo en GitHub</a>
      </div>
    </div>
  </section>

  <!-- CINEMA · stack en vivo ========================================== -->
  <section class="exp exp-cinema" id="cinema">
    <div class="wrap">
      <div class="eyebrow reveal">en vivo</div>
      <h2 class="section-title reveal">Mira cómo respira <span class="grad">el stack</span>.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        El ciclo de vida completo de una identidad móvil — provisión, SMS, llamada
        bidireccional — sobre nuestros propios servidores. En loop.
      </p>

      <div class="cinema-stage" id="cinemaStage">
        <canvas class="cinema-canvas" id="cinemaCanvas"></canvas>

        <div class="cinema-actor agent" id="actAgent">
          <div class="cinema-actor-icon">◉</div>
          <div class="cinema-actor-name">Agente</div>
          <div class="cinema-actor-meta" id="agentMeta">agente AI</div>
          <div class="cinema-bubble" id="agentBubble"></div>
        </div>

        <div class="cinema-stack">
          <div class="cinema-stack-title">AMI · stack propio</div>
          <div class="cinema-module" id="modBackend">Backend</div>
          <div class="cinema-module" id="modKannel">Kannel</div>
          <div class="cinema-module" id="modAsterisk">Asterisk</div>
          <div class="cinema-module" id="modNumbers">Numbers</div>
          <div class="cinema-module" id="modSip">SIP gateway</div>
        </div>

        <div class="cinema-actor world" id="actWorld">
          <div class="cinema-actor-icon">▲</div>
          <div class="cinema-actor-name">Mundo</div>
          <div class="cinema-actor-meta">destinatario</div>
          <div class="cinema-bubble" id="worldBubble"></div>
        </div>

        <div class="cinema-finale" id="cinemaFinale">
          <h3>Todo bajo nuestro stack.</h3>
          <p>Cero proveedores externos · Cero APIs de terceros · Cero SIMs físicas</p>
        </div>
      </div>

      <div class="cinema-status" id="cinemaStatus"><span class="act">acto 1</span>provisión del número</div>
      <div class="cinema-controls">
        <button class="cinema-replay-btn" id="cinemaReplayBtn" type="button">↻ replay</button>
      </div>
    </div>
  </section>

  <footer class="exp-footer">
    <div class="wrap">
      <div>Parallax IEI · AMI v1.0 · {html_escape(MCP_HTTP_URL)}</div>
      <div class="links">
        <a href="/">Home</a>
        <a href="/spec">Spec</a>
        <a href="/partners">Partners</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
      </div>
    </div>
  </footer>

<script>
  // ============================================================
  //  /experience JS
  // ============================================================
  (function() {{
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ------------------- Header hide on scroll down -------------
    const hdr = document.getElementById('hdr');
    let lastY = 0;
    window.addEventListener('scroll', () => {{
      const y = window.scrollY;
      if (y > 80 && y > lastY) hdr.classList.add('hidden');
      else hdr.classList.remove('hidden');
      lastY = y;
    }}, {{ passive: true }});

    // ------------------- Reveal on scroll -----------------------
    const io = new IntersectionObserver((entries) => {{
      entries.forEach(e => {{ if (e.isIntersecting) e.target.classList.add('in'); }});
    }}, {{ threshold: 0.2 }});
    document.querySelectorAll('.reveal, .reveal-stagger').forEach(el => io.observe(el));

    // ------------------- Slot-machine number reveal -------------
    const phoneTarget = '+34 600 549 832';
    const display = document.getElementById('numberDisplay');
    const status  = document.getElementById('numberStatus');

    function buildSlots() {{
      display.innerHTML = '';
      for (const ch of phoneTarget) {{
        const el = document.createElement('span');
        if (ch === ' ')      {{ el.className = 'sep'; }}
        else if (ch === '+') {{ el.className = 'digit plus revealed'; el.textContent = '+'; }}
        else                 {{ el.className = 'digit'; el.textContent = ch; }}
        display.appendChild(el);
      }}
    }}
    buildSlots();

    function revealNumber() {{
      const slots = display.querySelectorAll('.digit:not(.plus)');
      if (reduced) {{
        slots.forEach(s => s.classList.add('revealed'));
        status.classList.add('show');
        return;
      }}
      slots.forEach((slot, i) => {{
        const target = slot.textContent;
        let ticks = 0;
        const max  = 14 + i * 2;
        const interval = setInterval(() => {{
          slot.textContent = String(Math.floor(Math.random() * 10));
          slot.classList.add('revealed');
          ticks++;
          if (ticks >= max) {{
            clearInterval(interval);
            slot.textContent = target;
            slot.classList.add('flash');
            setTimeout(() => slot.classList.remove('flash'), 500);
            if (i === slots.length - 1) {{
              setTimeout(() => status.classList.add('show'), 250);
            }}
          }}
        }}, 60 + i * 12);
      }});
    }}

    let numberRevealed = false;
    new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        if (e.isIntersecting && !numberRevealed) {{
          numberRevealed = true;
          revealNumber();
        }}
      }});
    }}, {{ threshold: 0.5 }}).observe(display);

    // ------------------- Flow steps active on scroll ------------
    const flowSteps = document.querySelectorAll('.flow-step');
    const flowIO = new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        if (e.isIntersecting) e.target.classList.add('active');
      }});
    }}, {{ threshold: 0.4 }});
    flowSteps.forEach(s => flowIO.observe(s));

    // ------------------- Network canvas -------------------------
    const canvas = document.getElementById('netCanvas');
    const ctx = canvas.getContext('2d');
    let dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let W, H;
    function resizeCanvas() {{
      const rect = canvas.parentElement.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width  = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const agentLabels = ['Asistente', 'Soporte', 'Ventas', 'Ops', 'Recordatorios', 'Custom'];
    // Plataforma propia (capa 3 del stack, todo bajo control de AMI)
    const platformLabels = ['Asterisk', 'Kannel', 'Numbers', 'SIP gateway'];
    let nodes = [];
    let particles = [];

    function buildNodes() {{
      nodes = [];
      // En móvil reducimos a 4 agentes y 3 plataformas + acercamos al centro
      // para que los labels no se salgan del canvas y se vea más limpio.
      const isMobile = W < 600;
      const agents = isMobile
        ? ['Asistente', 'Soporte', 'Ventas', 'Ops']
        : agentLabels;
      const platforms = isMobile
        ? ['Asterisk', 'Kannel', 'Numbers']
        : platformLabels;

      // Agentes (izquierda)
      const leftX = isMobile ? W * 0.22 : W * 0.14;
      agents.forEach((label, i) => {{
        const spread = isMobile ? 0.18 : 0.13;
        const start  = isMobile ? 0.22 : 0.18;
        const y = H * (start + i * spread);
        nodes.push({{ kind: 'agent', x: leftX, y, label, color: '#5dd1ff', r: isMobile ? 5 : 6 }});
      }});
      // AMI central
      nodes.push({{
        kind: 'ami',
        x: isMobile ? W * 0.5 : W * 0.5,
        y: H * 0.5,
        label: 'AMI',
        color: '#8b6cff',
        r: isMobile ? 14 : 18
      }});
      // Plataforma propia
      const platX = isMobile ? W * 0.78 : W * 0.82;
      platforms.forEach((label, i) => {{
        const spread = isMobile ? 0.22 : 0.18;
        const start  = isMobile ? 0.28 : 0.22;
        const y = H * (start + i * spread);
        nodes.push({{ kind: 'platform', x: platX, y, label, color: '#a78bff', r: isMobile ? 6 : 8 }});
      }});
    }}
    buildNodes();
    window.addEventListener('resize', buildNodes);

    function spawnParticle() {{
      // Distribución: 60% agente→AMI, 40% AMI→plataforma.
      // Todo dentro del stack AMI — sin nodos externos.
      const ami      = nodes.find(n => n.kind === 'ami');
      const agents   = nodes.filter(n => n.kind === 'agent');
      const platform = nodes.filter(n => n.kind === 'platform');
      const r = Math.random();
      let from, to, color;
      if (r < 0.60) {{
        from = agents[Math.floor(Math.random() * agents.length)];
        to   = ami;
        color = '#5dd1ff';
      }} else {{
        from = ami;
        to   = platform[Math.floor(Math.random() * platform.length)];
        color = '#a78bff';
      }}
      particles.push({{
        x: from.x, y: from.y, fromX: from.x, fromY: from.y,
        toX: to.x, toY: to.y, t: 0, speed: 0.012 + Math.random() * 0.010, color
      }});
    }}

    function drawNetwork() {{
      ctx.clearRect(0, 0, W, H);

      const ami       = nodes.find(n => n.kind === 'ami');
      const platform  = nodes.filter(n => n.kind === 'platform');

      // "Halo" envolvente: agrupa AMI + plataforma propia en una nube violeta
      // sutil para que visualmente se entienda que TODO ese cluster es AMI.
      if (platform.length > 0) {{
        const xs = [ami.x].concat(platform.map(n => n.x));
        const ys = [ami.y].concat(platform.map(n => n.y));
        const minX = Math.min(...xs) - 60;
        const maxX = Math.max(...xs) + 60;
        const minY = Math.min(...ys) - 50;
        const maxY = Math.max(...ys) + 50;
        const cx = (minX + maxX) / 2;
        const cy = (minY + maxY) / 2;
        const rx = (maxX - minX) / 2;
        const ry = (maxY - minY) / 2;
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rx, ry));
        grad.addColorStop(0,   'rgba(139,108,255,0.10)');
        grad.addColorStop(0.6, 'rgba(139,108,255,0.04)');
        grad.addColorStop(1,   'rgba(139,108,255,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
        ctx.fill();
        // Etiqueta sutil
        ctx.fillStyle = 'rgba(139,108,255,0.45)';
        ctx.font = '600 10px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('AMI · stack propio', cx, minY + 6);
      }}

      // Líneas tenues entre AMI y los nodos de su plataforma (sólidas, color AMI)
      ctx.strokeStyle = 'rgba(139,108,255,0.18)';
      ctx.lineWidth = 1;
      platform.forEach(n => {{
        ctx.beginPath();
        ctx.moveTo(ami.x, ami.y);
        ctx.lineTo(n.x, n.y);
        ctx.stroke();
      }});

      // Líneas tenues entre AMI y agentes
      const agentsRender = nodes.filter(n => n.kind === 'agent');
      agentsRender.forEach(n => {{
        ctx.beginPath();
        ctx.moveTo(ami.x, ami.y);
        ctx.lineTo(n.x, n.y);
        ctx.stroke();
      }});


      // Partículas
      particles.forEach(p => {{
        p.t += p.speed;
        p.x = p.fromX + (p.toX - p.fromX) * p.t;
        p.y = p.fromY + (p.toY - p.fromY) * p.t;
        const fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
        ctx.fillStyle = p.color;
        ctx.globalAlpha = fade;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
        // tail
        ctx.strokeStyle = p.color;
        ctx.globalAlpha = 0.3 * fade;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        const back = 0.045;
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.fromX + (p.toX - p.fromX) * Math.max(0, p.t - back),
                   p.fromY + (p.toY - p.fromY) * Math.max(0, p.t - back));
        ctx.stroke();
        ctx.globalAlpha = 1;
      }});
      particles = particles.filter(p => p.t < 1);

      // Nodos
      nodes.forEach(n => {{
        // Halo
        const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 4);
        grad.addColorStop(0, n.color + '40');
        grad.addColorStop(1, 'transparent');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 4, 0, Math.PI * 2);
        ctx.fill();
        // Núcleo
        ctx.fillStyle = n.color;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
        // Borde
        ctx.strokeStyle = n.kind === 'ami' ? '#fff' : n.color;
        ctx.lineWidth = n.kind === 'ami' ? 2 : 1;
        ctx.stroke();
        // Label · en móvil reducimos font y offset para no salirse
        const mob = W < 600;
        ctx.fillStyle = '#ededf2';
        const sizeAmi   = mob ? 11 : 13;
        const sizeOther = mob ? 9 : 11;
        ctx.font = (n.kind === 'ami' ? '600 ' + sizeAmi + 'px ' : '500 ' + sizeOther + 'px ') + 'JetBrains Mono, monospace';
        const off = mob ? 5 : 8;
        if (n.kind === 'agent') {{
          ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
          ctx.fillText(n.label, n.x - n.r - off, n.y);
        }} else if (n.kind === 'ami') {{
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText(n.label, n.x, n.y + n.r + (mob ? 16 : 22));
        }} else if (n.kind === 'platform') {{
          ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
          ctx.fillText(n.label, n.x + n.r + off, n.y);
        }}
      }});
    }}

    let netLoopRunning = false;
    let lastSpawn = 0;
    function netLoop(now) {{
      if (now - lastSpawn > 180) {{ spawnParticle(); lastSpawn = now; }}
      drawNetwork();
      requestAnimationFrame(netLoop);
    }}
    if (!reduced) {{
      // Empieza solo cuando entra en viewport para no quemar CPU
      const netSection = document.getElementById('network');
      const netVisIO = new IntersectionObserver((entries) => {{
        entries.forEach(e => {{
          if (e.isIntersecting && !netLoopRunning) {{
            netLoopRunning = true;
            requestAnimationFrame(netLoop);
          }}
        }});
      }}, {{ threshold: 0.1 }});
      netVisIO.observe(netSection);
    }} else {{
      drawNetwork();
    }}

    // ------------------- Live demo ------------------------------
    const demoBody = document.getElementById('demoBody');
    const demoBtn  = document.getElementById('demoBtn');

    function fmtElapsed(ms) {{
      if (ms < 50)   return 'instantáneo';
      if (ms < 1000) return Math.round(ms) + ' ms';
      return (ms / 1000).toFixed(1) + ' s';
    }}

    async function runDemo() {{
      demoBody.innerHTML = '<div class="demo-step"><div class="check">▸</div><div class="label">Llamando al backend AMI…</div></div>';
      demoBody.firstChild.classList.add('show');
      const t0 = performance.now();
      try {{
        const res = await fetch('/v1/demo/quick', {{ method: 'POST' }});
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'demo_failed');
        const total = performance.now() - t0;
        demoBody.innerHTML = '';
        const steps = data.steps || [];
        steps.forEach((s, i) => {{
          const row = document.createElement('div');
          row.className = 'demo-step';
          const label = s.step.replace(/_/g, ' ');
          row.innerHTML = '<div class="check">✓</div>' +
                          '<div class="label">' + label + '</div>' +
                          '<div class="id">' + (s.id || '') + '</div>';
          demoBody.appendChild(row);
          setTimeout(() => row.classList.add('show'), 80 * (i + 1));
        }});
        const mid = data.mobile_identity || {{}};
        const result = document.createElement('div');
        result.className = 'demo-result';
        result.innerHTML =
          '<div class="demo-result-label">' + fmtElapsed(total) + '  ·  identity active</div>' +
          '<div class="demo-result-phone">' + (mid.phone_number || '') + '</div>' +
          '<div class="demo-result-links">' +
            '<a href="/identity/' + mid.id + '" target="_blank">Ver identidad pública →</a>' +
            '<a href="javascript:void(0)" onclick="document.getElementById(\\'demoBody\\').innerHTML=\\'<div class=demo-cta-wrap><button class=\\\\\\'btn btn-primary\\\\\\' id=demoBtn>▶  Crear otra identidad</button></div>\\'; document.getElementById(\\'demoBtn\\').addEventListener(\\'click\\', runDemo);">Crear otra →</a>' +
          '</div>';
        demoBody.appendChild(result);
        setTimeout(() => result.classList.add('show'), 80 * (steps.length + 1) + 200);
      }} catch (e) {{
        demoBody.innerHTML = '<div class="demo-step show"><div class="check" style="background:#ff6b6b;color:#fff;">!</div><div class="label">Error: ' + e.message + '</div></div>';
      }}
    }}
    demoBtn.addEventListener('click', runDemo);

  }})();

  // ============================================================
  //  CINEMA · live stack story (loop infinito, vanilla)
  // ============================================================
  (function() {{
    const stage    = document.getElementById('cinemaStage');
    if (!stage) return;
    const canvas   = document.getElementById('cinemaCanvas');
    const ctx      = canvas.getContext('2d');
    const reduced  = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const dpr      = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let W = 0, H = 0;

    function resizeStage() {{
      const rect = stage.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}
    resizeStage();
    window.addEventListener('resize', resizeStage);

    function pos(id) {{
      const el = document.getElementById(id);
      if (!el) return {{ x: 0, y: 0 }};
      const stageRect = stage.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      return {{
        x: r.left - stageRect.left + r.width  / 2,
        y: r.top  - stageRect.top  + r.height / 2
      }};
    }}

    // ---- Particles --------------------------------------------------
    let particles = [];
    function emit(fromId, toId, color, opts) {{
      opts = opts || {{}};
      const a = pos(fromId), b = pos(toId);
      particles.push({{
        fromX: a.x, fromY: a.y, toX: b.x, toY: b.y,
        x: a.x, y: a.y, t: 0,
        speed: opts.speed || 0.012,
        color: color || '#5dd1ff',
        size:  opts.size  || 3,
        tail:  opts.tail  !== false
      }});
    }}

    function draw() {{
      ctx.clearRect(0, 0, W, H);
      particles.forEach(p => {{
        p.t += p.speed;
        p.x = p.fromX + (p.toX - p.fromX) * p.t;
        p.y = p.fromY + (p.toY - p.fromY) * p.t;
        const fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
        if (p.tail) {{
          ctx.strokeStyle = p.color;
          ctx.globalAlpha = 0.35 * fade;
          ctx.lineWidth = 1.4;
          const back = 0.07;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(
            p.fromX + (p.toX - p.fromX) * Math.max(0, p.t - back),
            p.fromY + (p.toY - p.fromY) * Math.max(0, p.t - back)
          );
          ctx.stroke();
        }}
        ctx.fillStyle = p.color;
        ctx.globalAlpha = fade;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }});
      particles = particles.filter(p => p.t < 1);
      requestAnimationFrame(draw);
    }}
    requestAnimationFrame(draw);

    // ---- DOM helpers ------------------------------------------------
    function activate(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('active');
      timeouts.push(setTimeout(() => el.classList.remove('active'), ms || 1400));
    }}
    function pulseActor(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('pulsing');
      timeouts.push(setTimeout(() => el.classList.remove('pulsing'), ms || 1300));
    }}
    function showBubble(id, text, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.classList.add('show');
      if (ms !== 0) timeouts.push(setTimeout(() => el.classList.remove('show'), ms || 2000));
    }}
    function setMeta(text, isIdentity) {{
      const el = document.getElementById('agentMeta');
      el.textContent = text;
      el.classList.toggle('identity', !!isIdentity);
    }}
    function setStatus(act, text) {{
      const el = document.getElementById('cinemaStatus');
      if (act) {{
        el.innerHTML = '<span class="act">acto ' + act + '</span>' + text;
      }} else {{
        el.innerHTML = text;
      }}
    }}

    // ---- Timeline scheduler -----------------------------------------
    let timeouts = [];
    let intervals = [];
    function at(ms, fn) {{ timeouts.push(setTimeout(fn, ms)); }}
    function clearAll() {{
      timeouts.forEach(clearTimeout);
      intervals.forEach(clearInterval);
      timeouts = []; intervals = [];
    }}

    function resetStage() {{
      // Clear states
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.remove('active');
      }});
      ['actAgent','actWorld'].forEach(id => {{
        document.getElementById(id).classList.remove('pulsing');
      }});
      ['agentBubble','worldBubble'].forEach(id => {{
        document.getElementById(id).classList.remove('show');
      }});
      document.getElementById('cinemaFinale').classList.remove('show');
      setMeta('agente AI', false);
    }}

    // ---- The story --------------------------------------------------
    function runStory() {{
      clearAll();
      resetStage();

      // ============== ACTO 1 · Provisión ==============
      at(0, () => {{
        setStatus(1, 'el agente solicita un número');
        pulseActor('actAgent', 1100);
      }});
      at(700, () => {{
        showBubble('agentBubble', 'request_number_offer({{...}})');
        emit('actAgent', 'modBackend', '#5dd1ff');
      }});
      at(1300, () => {{
        activate('modBackend', 1900);
        setStatus(1, 'backend: validate · KYC · contract · policy');
      }});
      at(2200, () => emit('modBackend', 'modNumbers', '#a78bff'));
      at(2800, () => {{
        activate('modNumbers', 1700);
        setStatus(1, 'numbers: asignación desde inventario propio');
      }});
      at(3900, () => emit('modNumbers', 'modBackend', '#a78bff'));
      at(4500, () => {{
        activate('modBackend', 800);
        emit('modBackend', 'actAgent', '#a78bff');
      }});
      at(5300, () => {{
        setMeta('+34 600 549 832', true);
        showBubble('agentBubble', 'mobile identity active ✓', 1700);
        setStatus(1, 'identidad activa · +34 600 549 832');
      }});

      // ============== ACTO 2 · SMS saliente ==============
      at(7800, () => {{
        setStatus(2, 'el agente envía un SMS');
        showBubble('agentBubble', '"Recuerdo: dentista mañana 10:00"', 2200);
      }});
      at(8600, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(9200, () => {{
        activate('modBackend', 1200);
        setStatus(2, 'policy check: allowed · within spend limit');
      }});
      at(9900, () => emit('modBackend', 'modKannel', '#a78bff'));
      at(10400, () => {{
        activate('modKannel', 1500);
        setStatus(2, 'kannel: SUBMIT_SM por SMPP');
      }});
      at(11100, () => emit('modKannel', 'modSip', '#a78bff'));
      at(11500, () => activate('modSip', 1300));
      at(11800, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(12600, () => {{
        showBubble('worldBubble', '✉ SMS recibido', 2400);
        pulseActor('actWorld', 1200);
        setStatus(2, 'destinatario recibe el SMS');
      }});

      // ============== ACTO 3 · SMS entrante ==============
      at(15400, () => {{
        setStatus(3, 'el destinatario responde');
        showBubble('worldBubble', '"OK, allí estaré"', 2400);
        pulseActor('actWorld', 900);
      }});
      at(16400, () => emit('actWorld', 'modSip', '#82e0a4'));
      at(17000, () => {{
        activate('modSip', 1100);
        emit('modSip', 'modKannel', '#a78bff');
      }});
      at(17600, () => {{
        activate('modKannel', 1500);
        setStatus(3, 'kannel: DELIVER_SM · enrutamiento al agente');
      }});
      at(18400, () => emit('modKannel', 'modBackend', '#a78bff'));
      at(19000, () => {{
        activate('modBackend', 1200);
        setStatus(3, 'backend: audit log · push notification');
      }});
      at(19700, () => emit('modBackend', 'actAgent', '#a78bff'));
      at(20500, () => {{
        showBubble('agentBubble', '← "OK, allí estaré"', 2400);
        pulseActor('actAgent', 900);
        setStatus(3, 'agente lee la respuesta');
      }});

      // ============== ACTO 4 · Llamada saliente + audio bidireccional ==============
      at(23400, () => {{
        setStatus(4, 'el agente inicia una llamada');
        showBubble('agentBubble', '☎ dial(+34 ...)', 1800);
        pulseActor('actAgent', 1400);
      }});
      at(24400, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(25000, () => activate('modBackend', 1000));
      at(25400, () => emit('modBackend', 'modAsterisk', '#a78bff'));
      at(26000, () => {{
        activate('modAsterisk', 2400);
        setStatus(4, 'asterisk: SIP INVITE');
      }});
      at(26500, () => emit('modAsterisk', 'modSip', '#a78bff'));
      at(26900, () => activate('modSip', 2000));
      at(27200, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(28000, () => {{
        showBubble('worldBubble', '📞 ring ring…', 1400);
        pulseActor('actWorld', 1500);
      }});
      at(29200, () => {{
        setStatus(4, 'llamada en curso · audio bidireccional');
        // Start audio: bidirectional small particles fast
        const audioId = setInterval(() => {{
          emit('actAgent', 'actWorld', '#5dd1ff', {{ speed: 0.025, size: 2, tail: false }});
          emit('actWorld', 'actAgent', '#82e0a4', {{ speed: 0.025, size: 2, tail: false }});
        }}, 180);
        intervals.push(audioId);
        // Stop audio after 3.2s
        timeouts.push(setTimeout(() => clearInterval(audioId), 3200));
      }});
      at(32600, () => setStatus(4, 'call ended · 3.4s'));

      // ============== Finale ==============
      at(33800, () => {{
        document.getElementById('cinemaFinale').classList.add('show');
        setStatus(0, '<span class="act" style="background:rgba(74,222,128,0.12);color:#4ade80;">listo</span>todo bajo nuestro stack');
      }});

      // ============== Loop ==============
      at(38500, () => {{
        if (visible) {{
          runStory();
        }} else {{
          // Esperar hasta que vuelva a estar visible
          const waitId = setInterval(() => {{
            if (visible) {{ clearInterval(waitId); runStory(); }}
          }}, 800);
          intervals.push(waitId);
        }}
      }});
    }}

    // ---- Visibility tracking ----------------------------------------
    let visible = false;
    let started = false;
    new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        visible = e.isIntersecting;
        if (visible && !started && !reduced) {{
          started = true;
          runStory();
        }}
      }});
    }}, {{ threshold: 0.2 }}).observe(stage);

    // Reduced motion: estado final estático
    if (reduced) {{
      setMeta('+34 600 549 832', true);
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.add('active');
      }});
      setStatus(0, 'todo el stack activo · animación deshabilitada por preferencia del sistema');
    }}

    // Replay button
    document.getElementById('cinemaReplayBtn').addEventListener('click', () => {{
      if (reduced) return;
      runStory();
    }});
  }})();
</script>

</body>
</html>"""


def render_diagram_page():
    """Página /diagram: SOLO la animación del stack en vivo, en grande, sin chrome.

    Diseñada para enviar como enlace standalone — un único bloque que cuenta el
    ciclo de vida completo de una identidad móvil (provisión, SMS, llamada) en
    loop. Comparte el mismo motor (canvas + timeline + IntersectionObserver) que
    la sección cinema de /experience pero con stage más grande y nada alrededor.
    """
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Diagrama del stack</title>
<meta name="description" content="El ciclo de vida completo de una identidad móvil para agentes AI — provisión, SMS, llamada bidireccional — sobre stack propio. En loop.">
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --line: #1f1f2c;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-bg: rgba(139,108,255,0.10);
    --green: #4ade80;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", "Monaco", monospace;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    font-family: var(--sans);
    color: var(--ink);
    background:
      radial-gradient(ellipse 90% 60% at 50% 0%, rgba(139,108,255,0.10), transparent 70%),
      radial-gradient(ellipse 70% 50% at 50% 100%, rgba(93,209,255,0.06), transparent 70%),
      var(--bg);
    background-attachment: fixed;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }}
  ::selection {{ background: var(--accent); color: #fff; }}
  a {{ color: var(--accent-2); text-decoration: none; }}

  /* HEADER minimal */
  .dgm-header {{
    padding: 1.4rem 1.75rem;
    display: flex; align-items: center; justify-content: space-between;
    max-width: 1480px; margin: 0 auto; width: 100%;
  }}
  .dgm-brand {{
    font-family: var(--mono); font-weight: 700; font-size: 1rem;
    letter-spacing: 0.02em; display: flex; align-items: center; gap: 0.6rem;
    color: var(--ink); text-decoration: none;
  }}
  .dgm-brand .dot {{ color: var(--accent); }}
  .dgm-nav {{ display: flex; align-items: center; gap: 1.4rem; }}
  .dgm-nav a {{ color: var(--ink-soft); font-size: 0.85rem; font-weight: 500; font-family: var(--mono); }}
  .dgm-nav a:hover {{ color: var(--ink); }}

  /* HERO: solo eyebrow + título corto + bajada */
  .dgm-hero {{
    text-align: center;
    padding: 1.5rem 1.75rem 1rem;
    max-width: 900px; margin: 0 auto;
  }}
  .eyebrow {{
    font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em;
    margin-bottom: 1rem;
  }}
  .dgm-hero h1 {{
    font-size: clamp(1.8rem, 4vw, 2.8rem);
    font-weight: 700; letter-spacing: -0.025em;
    margin: 0 0 1rem;
    line-height: 1.1;
  }}
  .dgm-hero h1 .grad {{
    background: linear-gradient(180deg, #c2b3ff 10%, #7a5cff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .dgm-hero .sub {{
    color: var(--ink-soft); font-size: 1rem;
    margin: 0 auto; max-width: 640px;
  }}

  /* STAGE WRAP — más ancho y alto que en /experience */
  .dgm-stage-wrap {{
    flex: 1;
    width: 100%;
    max-width: 1480px;
    margin: 0 auto;
    padding: 1.5rem 1.75rem 2rem;
    display: flex; flex-direction: column; align-items: center;
  }}

  /* === BLOQUE CINEMA (idéntico a /experience, con stage más grande) ====== */
  .cinema-stage {{
    position: relative;
    width: 100%; max-width: 1380px;
    height: 640px;
    background: radial-gradient(ellipse at center, rgba(139,108,255,0.06) 0%, transparent 70%), var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0 30px 80px -30px rgba(123,92,255,0.25);
  }}
  .cinema-canvas {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    pointer-events: none; z-index: 1;
  }}
  .cinema-actor {{
    position: absolute;
    transform: translate(-50%, -50%);
    text-align: center;
    z-index: 2;
    width: 130px;
  }}
  .cinema-actor.agent {{ left: 11%; top: 50%; }}
  .cinema-actor.world {{ left: 89%; top: 50%; }}
  .cinema-stack {{
    position: absolute;
    left: 50%; top: 50%;
    transform: translate(-50%, -50%);
    display: flex; flex-direction: column; gap: 0.55rem;
    z-index: 2; min-width: 200px;
  }}
  .cinema-stack-title {{
    font-family: var(--mono); font-size: 0.65rem;
    color: var(--ink-mute);
    text-align: center;
    letter-spacing: 0.18em; text-transform: uppercase;
    margin-bottom: 0.4rem;
    font-weight: 600;
  }}
  .cinema-module {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 7px;
    padding: 0.55rem 0.85rem;
    font-family: var(--mono); font-size: 0.8rem;
    color: var(--ink-soft);
    text-align: center;
    transition: border-color 0.25s, background 0.25s, color 0.25s, box-shadow 0.25s, transform 0.25s;
  }}
  .cinema-module.active {{
    border-color: var(--accent);
    background: var(--accent-bg);
    color: var(--ink);
    box-shadow: 0 0 22px rgba(139,108,255,0.45);
    transform: scale(1.06);
  }}
  .cinema-actor-icon {{
    width: 66px; height: 66px;
    background: var(--surface);
    border: 2px solid currentColor;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 0.65rem;
    position: relative;
    font-size: 1.65rem;
    transition: box-shadow 0.3s, transform 0.3s;
  }}
  .cinema-actor.agent .cinema-actor-icon {{ color: var(--accent-2); }}
  .cinema-actor.world .cinema-actor-icon {{ color: var(--green); }}
  .cinema-actor-name {{
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--ink); font-weight: 500;
  }}
  .cinema-actor-meta {{
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-soft); margin-top: 0.4rem;
    min-height: 1rem; transition: color 0.4s;
  }}
  .cinema-actor-meta.identity {{ color: var(--accent-2); font-weight: 500; }}
  .cinema-actor.pulsing .cinema-actor-icon {{
    box-shadow: 0 0 0 0 currentColor;
    animation: cinema-pulse-anim 1.2s ease-out infinite;
  }}
  @keyframes cinema-pulse-anim {{
    0%   {{ box-shadow: 0 0 0 0 rgba(93,209,255,0.5); }}
    100% {{ box-shadow: 0 0 0 26px rgba(93,209,255,0); }}
  }}
  .cinema-actor.world.pulsing .cinema-actor-icon {{
    animation-name: cinema-pulse-world;
  }}
  @keyframes cinema-pulse-world {{
    0%   {{ box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }}
    100% {{ box-shadow: 0 0 0 26px rgba(74,222,128,0); }}
  }}
  .cinema-bubble {{
    position: absolute;
    bottom: 100%; left: 50%;
    transform: translateX(-50%) translateY(0);
    margin-bottom: 0.7rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.55rem 0.95rem;
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink);
    white-space: nowrap;
    max-width: 320px; overflow: hidden; text-overflow: ellipsis;
    opacity: 0; transition: opacity 0.35s, transform 0.35s;
    pointer-events: none;
  }}
  .cinema-bubble.show {{
    opacity: 1;
    transform: translateX(-50%) translateY(-4px);
  }}
  .cinema-bubble::after {{
    content: ""; position: absolute;
    top: 100%; left: 50%; transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--surface);
  }}
  .cinema-status {{
    text-align: center;
    margin: 1.5rem auto 0;
    font-family: var(--mono); font-size: 0.92rem;
    color: var(--ink-soft);
    min-height: 1.5rem;
    max-width: 900px;
    transition: opacity 0.3s;
  }}
  .cinema-status .act {{
    color: var(--accent);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 0.74rem;
    margin-right: 0.7rem;
    padding: 0.18rem 0.6rem;
    background: var(--accent-bg);
    border-radius: 4px;
  }}
  .cinema-controls {{ text-align: center; margin-top: 1rem; }}
  .cinema-replay-btn {{
    background: transparent; border: 1px solid var(--line);
    color: var(--ink-soft); cursor: pointer;
    font-family: var(--mono); font-size: 0.82rem;
    padding: 0.55rem 1.2rem; border-radius: 6px;
    transition: border-color 0.15s, color 0.15s;
  }}
  .cinema-replay-btn:hover {{ border-color: var(--accent); color: var(--ink); }}
  .cinema-finale {{
    position: absolute; inset: 0; z-index: 5;
    background: radial-gradient(circle, rgba(8,8,12,0.95), rgba(8,8,12,0.85));
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 0.8rem;
    text-align: center;
    opacity: 0; pointer-events: none;
    transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1);
    padding: 1.5rem;
  }}
  .cinema-finale.show {{ opacity: 1; }}
  .cinema-finale h3 {{
    font-size: clamp(1.8rem, 4vw, 3rem);
    font-weight: 700; letter-spacing: -0.02em; margin: 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .cinema-finale p {{
    font-family: var(--mono); font-size: 0.9rem;
    color: var(--ink-soft); margin: 0;
    letter-spacing: 0.04em;
  }}

  /* FOOTER muy fino */
  .dgm-footer {{
    border-top: 1px solid var(--line);
    padding: 1.2rem 1.75rem;
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-mute);
    display: flex; justify-content: space-between; align-items: center;
    max-width: 1480px; margin: 0 auto; width: 100%;
    flex-wrap: wrap; gap: 0.6rem;
  }}
  .dgm-footer a {{ color: var(--ink-soft); }}
  .dgm-footer a:hover {{ color: var(--accent-2); }}

  @media (max-width: 720px) {{
    .dgm-header {{ padding: 1rem 1.25rem; }}
    .dgm-nav {{ gap: 0.9rem; }}
    .dgm-nav a {{ font-size: 0.78rem; }}
    .dgm-hero {{ padding: 1rem 1.25rem 0.5rem; }}
    .dgm-stage-wrap {{ padding: 1rem 1rem 1.5rem; }}

    /* Cinema en móvil: layout VERTICAL · agente arriba, stack centro, mundo abajo */
    .cinema-stage {{ height: 640px; border-radius: 14px; }}
    .cinema-actor {{ width: 110px; }}
    .cinema-actor.agent {{ left: 50%; top: 12%; }}
    .cinema-actor.world {{ left: 50%; top: 88%; }}
    .cinema-actor-icon {{ width: 52px; height: 52px; font-size: 1.25rem; }}
    .cinema-actor-name {{ font-size: 0.78rem; }}
    .cinema-actor-meta {{ font-size: 0.72rem; }}

    .cinema-stack {{
      flex-direction: row;
      flex-wrap: wrap;
      justify-content: center;
      gap: 0.4rem;
      min-width: 0;
      max-width: calc(100% - 2rem);
    }}
    .cinema-stack-title {{ width: 100%; margin-bottom: 0.5rem; }}
    .cinema-module {{
      font-size: 0.66rem;
      padding: 0.35rem 0.6rem;
      flex: 0 0 auto;
    }}

    .cinema-bubble {{
      font-size: 0.7rem;
      max-width: 200px;
      white-space: normal;
      text-align: center;
      line-height: 1.4;
      padding: 0.45rem 0.75rem;
    }}
    /* Burbuja del agente en móvil: aparece DEBAJO del actor */
    .cinema-actor.agent .cinema-bubble {{
      bottom: auto;
      top: 100%;
      margin-bottom: 0;
      margin-top: 0.65rem;
    }}
    .cinema-actor.agent .cinema-bubble::after {{
      top: auto;
      bottom: 100%;
      border-top-color: transparent;
      border-bottom-color: var(--surface);
    }}

    .cinema-status {{ font-size: 0.82rem; padding: 0 1rem; }}
    .cinema-status .act {{ font-size: 0.66rem; padding: 0.1rem 0.5rem; margin-right: 0.5rem; }}
    .cinema-replay-btn {{ font-size: 0.76rem; padding: 0.5rem 1rem; }}
    .cinema-finale h3 {{ font-size: clamp(1.4rem, 6vw, 2.6rem); }}
    .cinema-finale p {{ font-size: 0.74rem; padding: 0 1rem; }}
  }}
</style>
</head>
<body>

  <header class="dgm-header">
    <a href="/" class="dgm-brand">
      {AMI_LOGO_SVG} AMI<span class="dot">.</span>
    </a>
    <nav class="dgm-nav">
      <a href="/experience">Experience</a>
      <a href="/spec">Spec</a>
      <a href="/">Inicio</a>
    </nav>
  </header>

  <section class="dgm-hero">
    <div class="eyebrow">live diagram</div>
    <h1>Cómo respira <span class="grad">el stack</span>.</h1>
    <p class="sub">
      El ciclo de vida completo de una identidad móvil — provisión, SMS, llamada
      bidireccional — sobre nuestro propio stack. En loop.
    </p>
  </section>

  <main class="dgm-stage-wrap">
    <div class="cinema-stage" id="cinemaStage">
      <canvas class="cinema-canvas" id="cinemaCanvas"></canvas>

      <div class="cinema-actor agent" id="actAgent">
        <div class="cinema-actor-icon">◉</div>
        <div class="cinema-actor-name">Agente</div>
        <div class="cinema-actor-meta" id="agentMeta">agente AI</div>
        <div class="cinema-bubble" id="agentBubble"></div>
      </div>

      <div class="cinema-stack">
        <div class="cinema-stack-title">AMI · stack propio</div>
        <div class="cinema-module" id="modBackend">Backend</div>
        <div class="cinema-module" id="modKannel">Kannel</div>
        <div class="cinema-module" id="modAsterisk">Asterisk</div>
        <div class="cinema-module" id="modNumbers">Numbers</div>
        <div class="cinema-module" id="modSip">SIP gateway</div>
      </div>

      <div class="cinema-actor world" id="actWorld">
        <div class="cinema-actor-icon">▲</div>
        <div class="cinema-actor-name">Mundo</div>
        <div class="cinema-actor-meta">destinatario</div>
        <div class="cinema-bubble" id="worldBubble"></div>
      </div>

      <div class="cinema-finale" id="cinemaFinale">
        <h3>Todo bajo nuestro stack.</h3>
        <p>Cero proveedores externos · Cero APIs de terceros · Cero SIMs físicas</p>
      </div>
    </div>

    <div class="cinema-status" id="cinemaStatus"><span class="act">acto 1</span>provisión del número</div>
    <div class="cinema-controls">
      <button class="cinema-replay-btn" id="cinemaReplayBtn" type="button">↻ replay</button>
    </div>
  </main>

  <footer class="dgm-footer">
    <span>AMI v1 · diagrama del stack en vivo</span>
    <a href="/experience">Ver la experience completa →</a>
  </footer>

<script>
  // ============================================================
  //  DIAGRAM · live stack story (loop infinito, vanilla)
  //  Mismo motor que la sección cinema de /experience.
  // ============================================================
  (function() {{
    const stage    = document.getElementById('cinemaStage');
    if (!stage) return;
    const canvas   = document.getElementById('cinemaCanvas');
    const ctx      = canvas.getContext('2d');
    const reduced  = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const dpr      = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let W = 0, H = 0;

    function resizeStage() {{
      const rect = stage.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}
    resizeStage();
    window.addEventListener('resize', resizeStage);

    function pos(id) {{
      const el = document.getElementById(id);
      if (!el) return {{ x: 0, y: 0 }};
      const stageRect = stage.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      return {{
        x: r.left - stageRect.left + r.width  / 2,
        y: r.top  - stageRect.top  + r.height / 2
      }};
    }}

    let particles = [];
    function emit(fromId, toId, color, opts) {{
      opts = opts || {{}};
      const a = pos(fromId), b = pos(toId);
      particles.push({{
        fromX: a.x, fromY: a.y, toX: b.x, toY: b.y,
        x: a.x, y: a.y, t: 0,
        speed: opts.speed || 0.012,
        color: color || '#5dd1ff',
        size:  opts.size  || 3,
        tail:  opts.tail  !== false
      }});
    }}

    function draw() {{
      ctx.clearRect(0, 0, W, H);
      particles.forEach(p => {{
        p.t += p.speed;
        p.x = p.fromX + (p.toX - p.fromX) * p.t;
        p.y = p.fromY + (p.toY - p.fromY) * p.t;
        const fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
        if (p.tail) {{
          ctx.strokeStyle = p.color;
          ctx.globalAlpha = 0.35 * fade;
          ctx.lineWidth = 1.4;
          const back = 0.07;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(
            p.fromX + (p.toX - p.fromX) * Math.max(0, p.t - back),
            p.fromY + (p.toY - p.fromY) * Math.max(0, p.t - back)
          );
          ctx.stroke();
        }}
        ctx.fillStyle = p.color;
        ctx.globalAlpha = fade;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }});
      particles = particles.filter(p => p.t < 1);
      requestAnimationFrame(draw);
    }}
    requestAnimationFrame(draw);

    function activate(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('active');
      timeouts.push(setTimeout(() => el.classList.remove('active'), ms || 1400));
    }}
    function pulseActor(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('pulsing');
      timeouts.push(setTimeout(() => el.classList.remove('pulsing'), ms || 1300));
    }}
    function showBubble(id, text, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.classList.add('show');
      if (ms !== 0) timeouts.push(setTimeout(() => el.classList.remove('show'), ms || 2000));
    }}
    function setMeta(text, isIdentity) {{
      const el = document.getElementById('agentMeta');
      el.textContent = text;
      el.classList.toggle('identity', !!isIdentity);
    }}
    function setStatus(act, text) {{
      const el = document.getElementById('cinemaStatus');
      if (act) {{
        el.innerHTML = '<span class="act">acto ' + act + '</span>' + text;
      }} else {{
        el.innerHTML = text;
      }}
    }}

    let timeouts = [];
    let intervals = [];
    function at(ms, fn) {{ timeouts.push(setTimeout(fn, ms)); }}
    function clearAll() {{
      timeouts.forEach(clearTimeout);
      intervals.forEach(clearInterval);
      timeouts = []; intervals = [];
    }}

    function resetStage() {{
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.remove('active');
      }});
      ['actAgent','actWorld'].forEach(id => {{
        document.getElementById(id).classList.remove('pulsing');
      }});
      ['agentBubble','worldBubble'].forEach(id => {{
        document.getElementById(id).classList.remove('show');
      }});
      document.getElementById('cinemaFinale').classList.remove('show');
      setMeta('agente AI', false);
    }}

    function runStory() {{
      clearAll();
      resetStage();

      // ============== ACTO 1 · Provisión ==============
      at(0, () => {{
        setStatus(1, 'el agente solicita un número');
        pulseActor('actAgent', 1100);
      }});
      at(700, () => {{
        showBubble('agentBubble', 'request_number_offer({{...}})');
        emit('actAgent', 'modBackend', '#5dd1ff');
      }});
      at(1300, () => {{
        activate('modBackend', 1900);
        setStatus(1, 'backend: validate · KYC · contract · policy');
      }});
      at(2200, () => emit('modBackend', 'modNumbers', '#a78bff'));
      at(2800, () => {{
        activate('modNumbers', 1700);
        setStatus(1, 'numbers: asignación desde inventario propio');
      }});
      at(3900, () => emit('modNumbers', 'modBackend', '#a78bff'));
      at(4500, () => {{
        activate('modBackend', 800);
        emit('modBackend', 'actAgent', '#a78bff');
      }});
      at(5300, () => {{
        setMeta('+34 600 549 832', true);
        showBubble('agentBubble', 'mobile identity active ✓', 1700);
        setStatus(1, 'identidad activa · +34 600 549 832');
      }});

      // ============== ACTO 2 · SMS saliente ==============
      at(7800, () => {{
        setStatus(2, 'el agente envía un SMS');
        showBubble('agentBubble', '"Recuerdo: dentista mañana 10:00"', 2200);
      }});
      at(8600, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(9200, () => {{
        activate('modBackend', 1200);
        setStatus(2, 'policy check: allowed · within spend limit');
      }});
      at(9900, () => emit('modBackend', 'modKannel', '#a78bff'));
      at(10400, () => {{
        activate('modKannel', 1500);
        setStatus(2, 'kannel: SUBMIT_SM por SMPP');
      }});
      at(11100, () => emit('modKannel', 'modSip', '#a78bff'));
      at(11500, () => activate('modSip', 1300));
      at(11800, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(12600, () => {{
        showBubble('worldBubble', '✉ SMS recibido', 2400);
        pulseActor('actWorld', 1200);
        setStatus(2, 'destinatario recibe el SMS');
      }});

      // ============== ACTO 3 · SMS entrante ==============
      at(15400, () => {{
        setStatus(3, 'el destinatario responde');
        showBubble('worldBubble', '"OK, allí estaré"', 2400);
        pulseActor('actWorld', 900);
      }});
      at(16400, () => emit('actWorld', 'modSip', '#82e0a4'));
      at(17000, () => {{
        activate('modSip', 1100);
        emit('modSip', 'modKannel', '#a78bff');
      }});
      at(17600, () => {{
        activate('modKannel', 1500);
        setStatus(3, 'kannel: DELIVER_SM · enrutamiento al agente');
      }});
      at(18400, () => emit('modKannel', 'modBackend', '#a78bff'));
      at(19000, () => {{
        activate('modBackend', 1200);
        setStatus(3, 'backend: audit log · push notification');
      }});
      at(19700, () => emit('modBackend', 'actAgent', '#a78bff'));
      at(20500, () => {{
        showBubble('agentBubble', '← "OK, allí estaré"', 2400);
        pulseActor('actAgent', 900);
        setStatus(3, 'agente lee la respuesta');
      }});

      // ============== ACTO 4 · Llamada + audio bidireccional ==============
      at(23400, () => {{
        setStatus(4, 'el agente inicia una llamada');
        showBubble('agentBubble', '☎ dial(+34 ...)', 1800);
        pulseActor('actAgent', 1400);
      }});
      at(24400, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(25000, () => activate('modBackend', 1000));
      at(25400, () => emit('modBackend', 'modAsterisk', '#a78bff'));
      at(26000, () => {{
        activate('modAsterisk', 2400);
        setStatus(4, 'asterisk: SIP INVITE');
      }});
      at(26500, () => emit('modAsterisk', 'modSip', '#a78bff'));
      at(26900, () => activate('modSip', 2000));
      at(27200, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(28000, () => {{
        showBubble('worldBubble', '📞 ring ring…', 1400);
        pulseActor('actWorld', 1500);
      }});
      at(29200, () => {{
        setStatus(4, 'llamada en curso · audio bidireccional');
        const audioId = setInterval(() => {{
          emit('actAgent', 'actWorld', '#5dd1ff', {{ speed: 0.025, size: 2, tail: false }});
          emit('actWorld', 'actAgent', '#82e0a4', {{ speed: 0.025, size: 2, tail: false }});
        }}, 180);
        intervals.push(audioId);
        timeouts.push(setTimeout(() => clearInterval(audioId), 3200));
      }});
      at(32600, () => setStatus(4, 'call ended · 3.4s'));

      // ============== Finale ==============
      at(33800, () => {{
        document.getElementById('cinemaFinale').classList.add('show');
        setStatus(0, '<span class="act" style="background:rgba(74,222,128,0.12);color:#4ade80;">listo</span>todo bajo nuestro stack');
      }});

      // ============== Loop ==============
      at(38500, () => {{
        if (visible) {{
          runStory();
        }} else {{
          const waitId = setInterval(() => {{
            if (visible) {{ clearInterval(waitId); runStory(); }}
          }}, 800);
          intervals.push(waitId);
        }}
      }});
    }}

    let visible = true;
    let started = false;
    new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        visible = e.isIntersecting;
        if (visible && !started && !reduced) {{
          started = true;
          runStory();
        }}
      }});
    }}, {{ threshold: 0.2 }}).observe(stage);

    // Si la stage ya está dentro del viewport en page load (caso típico de
    // /diagram donde la stage es lo único que se ve), arrancamos sin esperar
    // al observer — éste suele dispararse pero por seguridad metemos un
    // fallback con un pequeño retraso.
    setTimeout(() => {{
      if (!started && !reduced) {{
        started = true;
        runStory();
      }}
    }}, 400);

    if (reduced) {{
      setMeta('+34 600 549 832', true);
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.add('active');
      }});
      setStatus(0, 'todo el stack activo · animación deshabilitada por preferencia del sistema');
    }}

    document.getElementById('cinemaReplayBtn').addEventListener('click', () => {{
      if (reduced) return;
      runStory();
    }});
  }})();
</script>

</body>
</html>"""


def render_llms_txt():
    """Formato emergente https://llmstxt.org — índice corto en markdown para LLMs."""
    tool_lines = "\n".join(f"- {n}: {d}" for n, d in _tools_for_landing())
    endpoint_lines = "\n".join(f"- {v} {p} — {d}" for v, p, d in _endpoints_for_landing())
    return f"""# AMI — Agent Mobile Identity Protocol

> Protocolo estándar para que un agente AI obtenga y opere una identidad móvil
> (número, SMS y voz) con todo el ciclo de vida sobre nuestro propio stack. v1
> cubre **contratación + aprovisionamiento** del número, y **Operations v2**
> añade SMS y voz scoped por agente.

AMI expone un MCP server y una REST API JSON. El agente recorre dos planos:

1. **Contratación (una vez por número):** solicitud → oferta → datos cliente →
   contrato → firma → MobileIdentity activa. Al activar, AMI devuelve un
   `agent_token` scoped al MID (Nivel 2 de auth) que se usa después.
2. **Operación (continuo):** con el `agent_token`, el agente envía/recibe SMS
   y origina/recibe llamadas. La voz funciona en **bridge-by-API**: AMI cursa
   la llamada al PSTN y la bridgea por SIP al endpoint del cliente (su URI
   SIP de Realtime, PBX propio, o cualquier destino que hable SIP). AMI NO
   ejecuta voz — es el operador / SIP provider, no el cerebro.

Lo único simulado en v1 es la pieza telco (SIM/SMSC/PBX). El resto —
contratos, firma, máquina de estados, audit, scoped tokens, lifecycle de SMS
y llamadas — es real y sustituible por adaptadores hacia el partner telco
sin cambiar el contrato externo.

## Conexión (recomendado para agentes)

**Cero instalación, cero API key local — usa el MCP HTTP remoto:**

- URL: {MCP_HTTP_URL}
- Transporte: streamable-http
- Auth MCP: ninguna (la API key vive ya en el servidor de Render).

Cualquier cliente MCP que soporte transporte streamable-http se enchufa
directamente. Esta es la forma recomendada para agentes corriendo en
máquinas remotas o CI: no requiere clonar el repo, ni instalar Python, ni
gestionar `AMI_API_KEY` localmente.

## Conexión alternativa (stdio local)

Sólo si el cliente MCP no soporta HTTP remoto:

- Instalador one-liner: `curl -fsSL https://protocolami.com/install.sh | sh`
- Repo: {REPO_URL}
- Requiere setear `AMI_API_KEY` propia para hablar con el backend.

## Auth (dos niveles)

- **Nivel 1 — `AMI_API_KEY`** (header `Authorization: Bearer ...`). Es la
  credencial del *customer*. Cubre todo el plano de contratación
  (`/v1/sim-requests`, `/v1/contracts`, `/v1/mobile-identities/activate`,
  `/v1/mobile-identities/{{id}}/rotate-token`, etc.).
- **Nivel 2 — `agent_token`** (`amiagt_live_<hex>`, también header `Bearer`).
  Es scoped a una sola MobileIdentity. Devuelto una sola vez al activar el
  número. Cubre el plano de operación (`/v1/agent/sms/*`, `/v1/agent/calls/*`,
  `/v1/agent/self`). Si se filtra, rotarlo con
  `POST /v1/mobile-identities/{{id}}/rotate-token` lo invalida al instante.

## Acceso REST directo

Para clientes no-MCP, la API REST está documentada en /openapi.json. Auth con
los esquemas descritos arriba.

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
        "servers": [{"url": "https://protocolami.com", "description": "Mock público"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http", "scheme": "bearer", "bearerFormat": "AMI_API_KEY",
                    "description": "Nivel 1: API key del customer. Usa este esquema para todos los endpoints de contratación.",
                },
                "agentBearer": {
                    "type": "http", "scheme": "bearer", "bearerFormat": "amiagt_live_<hex>",
                    "description": "Nivel 2: agent_token scoped a una MobileIdentity. Devuelto una sola vez al activar. Lo usan los endpoints /v1/agent/*.",
                },
                "telcoKey": {
                    "type": "apiKey", "in": "header", "name": "X-Telco-Key",
                    "description": "Clave compartida con el partner telco para los webhooks /v1/_telco/*. Configurada por AMI_TELCO_INBOUND_KEY.",
                },
                "adminKey": {
                    "type": "http", "scheme": "bearer", "bearerFormat": "AMI_ADMIN_KEY",
                    "description": "Master key para gestionar customer-accounts en /v1/admin/*. NO se usa para tráfico de cliente.",
                },
            }
        },
        "security": [{"bearerAuth": []}],
        "tags": [
            {"name": "Contratación", "description": "Solicitud, oferta, datos del cliente, contrato, firma y activación."},
            {"name": "Operations v2 · SMS", "description": "Envío y listado de SMS scoped por agent_token."},
            {"name": "Operations v2 · Voz", "description": "Llamadas bridge-by-API: AMI cursa la pipa SIP, el cliente ejecuta la voz."},
            {"name": "Limits", "description": "Rate limits (hora/día), presupuesto mensual y allowlist de países por MID."},
            {"name": "Webhooks", "description": "Webhooks salientes desde AMI hacia el cliente (firmados con HMAC-SHA256)."},
            {"name": "Webhooks telco", "description": "Endpoints internos llamados por el partner telco (autenticados con X-Telco-Key)."},
            {"name": "Admin", "description": "Multi-tenancy: gestión de customer-accounts. Auth: AMI_ADMIN_KEY (master)."},
            {"name": "Discovery", "description": "Salud, audit, demo, páginas públicas."},
        ],
        "paths": {
            # --- Discovery / health ---
            "/v1/health":                                 {"get":  {"summary": "Healthcheck", "tags": ["Discovery"], "security": [], "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-options":                            {"get":  {"summary": "Países y SIMs disponibles", "tags": ["Discovery"], "responses": {"200": {"description": "OK"}}}},
            "/v1/events":                                 {"get":  {"summary": "Últimos AuditEvents", "tags": ["Discovery"], "responses": {"200": {"description": "OK"}}}},
            "/v1/demo/quick":                             {"post": {"summary": "Demo end-to-end (público, sin auth)", "tags": ["Discovery"], "security": [], "responses": {"200": {"description": "OK"}}}},
            "/identity/{id}":                             {"get":  {"summary": "Página pública de MobileIdentity", "tags": ["Discovery"], "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/spec":                                      {"get":  {"summary": "Spec del protocolo en HTML", "tags": ["Discovery"], "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/diagram":                                   {"get":  {"summary": "Animación standalone del ciclo de vida del stack", "tags": ["Discovery"], "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/docs":                                      {"get":  {"summary": "Portal de documentación interactivo (bilingüe ES/EN, try-it widgets, tabs de código)", "tags": ["Discovery"], "security": [], "responses": {"200": {"description": "HTML"}}}},

            # --- Contratación (v1) ---
            "/v1/sim-requests":                           {"post": {"summary": "Crear SIMRequest + oferta", "tags": ["Contratación"], "responses": {"201": {"description": "Created"}}}},
            "/v1/sim-requests/{id}":                      {"get":  {"summary": "Obtener SIMRequest", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-requests/{id}/cancel":               {"post": {"summary": "Cancelar SIMRequest", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/sim-requests/{id}/customer-data":        {"post": {"summary": "Adjuntar datos del cliente", "tags": ["Contratación"], "responses": {"201": {"description": "Created"}}}},
            "/v1/offers/{id}":                            {"get":  {"summary": "Obtener oferta", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/offers/{id}/accept":                     {"post": {"summary": "Aceptar oferta", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/customers":                              {"post": {"summary": "Crear cliente suelto", "tags": ["Contratación"], "responses": {"201": {"description": "Created"}}}},
            "/v1/customers/{id}":                         {"get":  {"summary": "Obtener cliente", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/contracts":                              {"post": {"summary": "Crear contrato + signature_url", "tags": ["Contratación"], "responses": {"201": {"description": "Created"}, "409": {"description": "kyc_required (si AMI_KYC_REQUIRED=1)"}}}},
            "/v1/sim-requests/{id}/kyc/initiate":         {"post": {"summary": "Disparar verificación humana (KYC) del rep. legal", "tags": ["Contratación · KYC"], "responses": {"201": {"description": "Created (token + verification_url)"}, "200": {"description": "Existing KYC reused"}, "409": {"description": "customer_data_missing"}}}},
            "/kyc/{token}":                               {"get":  {"summary": "Página pública para que el humano suba DNI + selfie", "tags": ["Contratación · KYC"], "security": [], "responses": {"200": {"description": "HTML"}, "404": {"description": "kyc_not_found"}}}},
            "/kyc/{token}/submit":                        {"post": {"summary": "Recibe imágenes base64 desde el form público", "tags": ["Contratación · KYC"], "security": [], "responses": {"200": {"description": "Submitted"}, "400": {"description": "dni_front_required / *_too_large / *_invalid_format"}, "409": {"description": "kyc_already_submitted"}}}},
            "/v1/admin/kyc":                              {"get":  {"summary": "Admin: listar KYCs (auth: AMI_ADMIN_KEY)", "tags": ["Admin · KYC"], "responses": {"200": {"description": "OK"}, "401": {"description": "unauthorized_admin"}}}},
            "/v1/admin/kyc/{id}/verify":                  {"post": {"summary": "Admin: marcar KYC como verified", "tags": ["Admin · KYC"], "responses": {"200": {"description": "Verified"}, "401": {"description": "unauthorized_admin"}, "404": {"description": "kyc_not_found"}}}},
            "/v1/admin/kyc/{id}/reject":                  {"post": {"summary": "Admin: rechazar KYC con motivo", "tags": ["Admin · KYC"], "responses": {"200": {"description": "Rejected"}, "401": {"description": "unauthorized_admin"}, "404": {"description": "kyc_not_found"}}}},
            "/v1/admin/kyc/purge":                        {"post": {"summary": "Admin: ejecutar purga de KYCs caducados (cron manual)", "tags": ["Admin · KYC"], "responses": {"200": {"description": "Purge result"}, "401": {"description": "unauthorized_admin"}}}},
            "/panel/kyc":                                 {"get":  {"summary": "Panel HTML para revisión humana de KYCs (cookie/Bearer/query auth)", "tags": ["Admin · KYC"], "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/panel/kyc/login":                           {"post": {"summary": "Panel KYC: login (form, set-cookie)", "tags": ["Admin · KYC"], "security": [], "responses": {"303": {"description": "Redirect to /panel/kyc"}, "401": {"description": "bad key"}}}},
            "/panel/kyc/logout":                          {"post": {"summary": "Panel KYC: logout (clear cookie)", "tags": ["Admin · KYC"], "security": [], "responses": {"303": {"description": "Redirect"}}}},
            "/v1/contracts/{id}":                         {"get":  {"summary": "Obtener contrato", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/contracts/{id}/mock-sign":               {"post": {"summary": "Firma directa (atajo programático)", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/sign/{id}":                              {"get":  {"summary": "Página HTML de firma", "tags": ["Contratación"], "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/v1/sign/{id}/confirm":                      {"post": {"summary": "Callback de firma desde el form", "tags": ["Contratación"], "security": [], "responses": {"200": {"description": "HTML"}}}},
            "/v1/mobile-identities/activate":             {"post": {"summary": "Activar MobileIdentity (devuelve agent_token)", "tags": ["Contratación"], "responses": {"201": {"description": "Created"}}}},
            "/v1/mobile-identities/{id}":                 {"get":  {"summary": "Obtener MobileIdentity", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/mobile-identities/{id}/rotate-token":    {"post": {"summary": "Rotar el agent_token (hard rotate)", "tags": ["Contratación"], "responses": {"200": {"description": "OK"}}}},
            "/v1/mobile-identities/{id}/inbound-config":  {"post": {"summary": "Configurar el endpoint SIP de entrantes para un MID", "tags": ["Operations v2 · Voz"], "responses": {"200": {"description": "OK"}}}},
            "/v1/mobile-identities/{id}/limits":         {
                "get":  {"summary": "Leer los límites del MID", "tags": ["Limits"], "responses": {"200": {"description": "OK"}}},
                "post": {"summary": "Actualizar los límites del MID (patch parcial)", "tags": ["Limits"], "responses": {"200": {"description": "OK"}, "400": {"description": "validation"}}},
            },
            "/v1/mobile-identities/{id}/usage":          {"get":  {"summary": "Usage actual del MID (auth: customer)", "tags": ["Limits"], "responses": {"200": {"description": "OK"}}}},
            "/v1/mobile-identities/{id}/webhooks":       {
                "get":  {"summary": "Listar webhooks salientes del MID", "tags": ["Webhooks"], "responses": {"200": {"description": "OK"}}},
                "post": {"summary": "Crear un webhook saliente (devuelve el secret una vez)", "tags": ["Webhooks"], "responses": {"201": {"description": "Created"}, "400": {"description": "validation"}}},
            },
            "/v1/mobile-identities/{id}/webhooks/{wh}/delete": {"post": {"summary": "Borrar un webhook", "tags": ["Webhooks"], "responses": {"200": {"description": "OK"}, "404": {"description": "webhook_not_found"}}}},

            # --- Operations v2 · scoped por agent_token ---
            "/v1/agent/self":                             {"get":  {"summary": "Info de la MobileIdentity dueña del agent_token", "tags": ["Operations v2 · SMS"], "security": [{"agentBearer": []}], "responses": {"200": {"description": "OK"}, "401": {"description": "invalid_agent_token"}}}},
            "/v1/agent/sms/send":                         {"post": {"summary": "Enviar un SMS", "tags": ["Operations v2 · SMS"], "security": [{"agentBearer": []}], "responses": {"201": {"description": "queued"}, "400": {"description": "validation"}, "401": {"description": "invalid_agent_token"}}}},
            "/v1/agent/sms":                              {"get":  {"summary": "Listar SMS del MID", "tags": ["Operations v2 · SMS"], "security": [{"agentBearer": []}], "responses": {"200": {"description": "OK"}}}},
            "/v1/agent/calls/place":                      {"post": {"summary": "Originar una llamada bridge-by-API", "tags": ["Operations v2 · Voz"], "security": [{"agentBearer": []}], "responses": {"201": {"description": "initiated"}, "400": {"description": "validation"}}}},
            "/v1/agent/calls":                            {"get":  {"summary": "Listar llamadas del MID", "tags": ["Operations v2 · Voz"], "security": [{"agentBearer": []}], "responses": {"200": {"description": "OK"}}}},
            "/v1/agent/calls/{id}":                       {"get":  {"summary": "Detalle de una llamada", "tags": ["Operations v2 · Voz"], "security": [{"agentBearer": []}], "responses": {"200": {"description": "OK"}, "404": {"description": "call_not_found"}}}},
            "/v1/agent/calls/{id}/hangup":                {"post": {"summary": "Colgar una llamada", "tags": ["Operations v2 · Voz"], "security": [{"agentBearer": []}], "responses": {"200": {"description": "OK"}}}},
            "/v1/agent/usage":                            {"get":  {"summary": "Usage del MID dueño del agent_token", "tags": ["Limits"], "security": [{"agentBearer": []}], "responses": {"200": {"description": "OK"}}}},

            # --- Admin (multi-tenancy) ---
            "/v1/admin/customers": {
                "get":  {"summary": "Listar customer-accounts (sin exponer secret)", "tags": ["Admin"], "security": [{"adminKey": []}], "responses": {"200": {"description": "OK"}, "401": {"description": "unauthorized_admin"}}},
                "post": {"summary": "Crear customer-account (devuelve API key una sola vez)", "tags": ["Admin"], "security": [{"adminKey": []}], "responses": {"201": {"description": "Created"}, "401": {"description": "unauthorized_admin"}}},
            },
            "/v1/admin/customers/{id}/rotate-key":        {"post": {"summary": "Rotar API key de un customer (hard rotate)", "tags": ["Admin"], "security": [{"adminKey": []}], "responses": {"200": {"description": "OK"}, "404": {"description": "customer_account_not_found"}}}},
            "/v1/admin/customers/{id}/suspend":           {"post": {"summary": "Suspender un customer (bloquea su tráfico)", "tags": ["Admin"], "security": [{"adminKey": []}], "responses": {"200": {"description": "OK"}}}},
            "/v1/admin/customers/{id}/activate":          {"post": {"summary": "Reactivar un customer suspended", "tags": ["Admin"], "security": [{"adminKey": []}], "responses": {"200": {"description": "OK"}}}},

            # --- Webhooks del partner telco ---
            "/v1/_telco/sms/inbound":                     {"post": {"summary": "Entregar SMS entrante hacia un MID", "tags": ["Webhooks telco"], "security": [{"telcoKey": []}], "responses": {"201": {"description": "Created"}, "401": {"description": "invalid_telco_key"}, "503": {"description": "telco_inbound_disabled"}}}},
            "/v1/_telco/calls/inbound":                   {"post": {"summary": "Consultar a dónde reenviar una llamada entrante", "tags": ["Webhooks telco"], "security": [{"telcoKey": []}], "responses": {"201": {"description": "Forward"}, "409": {"description": "no_inbound_sip_uri"}}}},
            "/v1/_telco/calls/{id}/status":               {"post": {"summary": "Actualizar el estado de una llamada (ringing/in_progress/completed)", "tags": ["Webhooks telco"], "security": [{"telcoKey": []}], "responses": {"200": {"description": "OK"}, "409": {"description": "invalid_call_transition"}}}},
        },
    }


# Bootstrap final: si AMI_API_KEY está seteada y no existe un customer con
# esa key, lo creamos como "cust_default" para mantener compat single-tenant.
# Se hace aquí (después de todas las funciones) para que `now()` esté disponible.
_bootstrap_default_customer()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        """Override del default Apache-like log de BaseHTTPRequestHandler.
        El handler base llama con fmt='"%s" %s %s' y args=(requestline, code, size).
        Parseamos y emitimos JSON estructurado vía ami_log."""
        ip = self.address_string()
        ua = ""
        try:
            ua = self.headers.get("User-Agent", "") or ""
        except Exception:
            ua = ""
        # Caso típico: log_request → fmt='"%s" %s %s'. Lo desempaquetamos.
        method = path = ""
        status = None
        size = None
        if len(args) >= 1 and isinstance(args[0], str):
            parts = args[0].split(" ")
            if len(parts) >= 2:
                method = parts[0]
                path = parts[1]
            else:
                path = args[0]
        if len(args) >= 2:
            try:
                status = int(args[1])
            except (TypeError, ValueError):
                status = None
        if len(args) >= 3 and args[2] not in (None, "-"):
            try:
                size = int(args[2])
            except (TypeError, ValueError):
                size = None
        if method or path or status is not None:
            ami_log.info(
                "http_request",
                method=method,
                path=path,
                status=status,
                ip=ip,
                ua=ua,
                size=size,
            )
        else:
            # Fallback: log_error u otros formatos no estándar. Mantenemos el
            # mensaje crudo bajo un evento genérico para no perder señal.
            ami_log.info("http_log", ip=ip, message=(fmt % args) if args else fmt)

    def handle_one_request(self):
        """Wrap del request para persistir STATE + grabar métricas + serialize
        acceso al STATE.

        - STATE_LOCK garantiza que un request ejecuta el handler sin que otro
          thread mute STATE concurrentemente (race conditions en counters,
          dict size during iteration, etc.). Es RLock para permitir nesting.
        - ami_storage.save_state() escribe el snapshot a SQLite al final del
          ciclo (cuando ami_api.STATE pudo haber mutado).
        - ami_metrics.REQUESTS y LATENCY se incrementan con method+path+status
          + tiempo wall-clock. Path se normaliza a template (sin IDs) para
          evitar cardinalidad infinita.
        """
        import time
        t0 = time.monotonic()
        try:
            with STATE_LOCK:
                super().handle_one_request()
        finally:
            elapsed = time.monotonic() - t0
            try:
                method = (self.command or "?").upper()
                path_template = _path_template(getattr(self, "path", "") or "")
                status = str(getattr(self, "_last_status", 0) or 0)
                ami_metrics.REQUESTS.inc(method=method, path=path_template, status=status)
                ami_metrics.LATENCY.observe(elapsed, method=method, path=path_template)
                # Gauge ligero: nº de MIDs activos (cardinalidad O(1))
                active = sum(1 for m in STATE["mobile_identities"].values()
                              if m.get("status") == "active")
                ami_metrics.ACTIVE_MIDS.set(active)
            except Exception:
                pass
            try:
                ami_storage.save_state(STATE)
            except Exception:
                pass  # no rompemos el ciclo HTTP por un fallo de disco

    def do_OPTIONS(self): response(self, 200, {"ok": True})

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        p = urlparse(self.path).path

        # Endpoints scoped por agent_token: hacen su propia validación
        # antes del check_auth global del customer.
        if p == "/v1/agent/self":
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            identity = STATE["mobile_identities"].get(rec["mid"])
            if not identity or identity.get("status") != "active":
                return response(self, 409, {"error": "mobile_identity_not_active"})
            return response(self, 200, {
                "mobile_identity_id": rec["mid"],
                "customer_id": rec["customer_id"],
                "phone_number": identity.get("phone_number"),
                "capabilities": identity.get("capabilities"),
                "token_prefix": rec["token_prefix"],
                "token_created_at": rec["created_at"],
                "status": "active",
            })

        # Vista del usage del MID dueño del agent_token. El agente la usa para
        # saber cuánto le queda antes de chocar con un tope diario / budget.
        if p == "/v1/agent/usage":
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            identity = STATE["mobile_identities"].get(rec["mid"])
            if not identity:
                return response(self, 404, {"error": "mobile_identity_not_found"})
            return response(self, 200, ami_limits.usage_snapshot(identity))

        # Listar SMS del MID asociado al agent_token.
        # Query params: ?limit=20&direction=outbound|inbound
        if p == "/v1/agent/sms":
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            qs = dict(re.findall(r"([^&=?]+)=([^&]*)", urlparse(self.path).query or ""))
            try:
                limit = int(qs.get("limit", "20"))
            except ValueError:
                return response(self, 400, {"error": "invalid_limit"})
            direction = qs.get("direction")
            if direction and direction not in ("outbound", "inbound"):
                return response(self, 400, {"error": "invalid_direction"})
            msgs = list_sms_for_mid(rec["mid"], limit=limit, direction=direction)
            return response(self, 200, {"messages": msgs, "count": len(msgs)})

        # Listar llamadas del MID asociado al agent_token.
        if p == "/v1/agent/calls":
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            qs = dict(re.findall(r"([^&=?]+)=([^&]*)", urlparse(self.path).query or ""))
            try:
                limit = int(qs.get("limit", "20"))
            except ValueError:
                return response(self, 400, {"error": "invalid_limit"})
            direction = qs.get("direction")
            if direction and direction not in ("outbound", "inbound"):
                return response(self, 400, {"error": "invalid_direction"})
            calls = list_calls_for_mid(rec["mid"], limit=limit, direction=direction)
            return response(self, 200, {"calls": calls, "count": len(calls)})

        # Detalle de una llamada (scoped al MID del token).
        m = re.match(r"^/v1/agent/calls/([^/]+)$", p)
        if m:
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            call = STATE["calls"].get(m.group(1))
            if not call or call["mid"] != rec["mid"]:
                return response(self, 404, {"error": "call_not_found"})
            return response(self, 200, call)

        if not is_public("GET", p) and not check_auth(self):
            return response(self, 401, {"error": "unauthorized"})
        # Idioma global: el detector se usa tanto para las páginas con chrome
        # común como para las que tienen bilingual inline (spec, partners).
        lang = _detect_lang(self)
        if p == "/" or p == "/index.html":
            return respond_html(self, 200,
                _with_chrome(render_landing(), active="home", lang=lang))
        if p == "/llms.txt":
            return respond_text(self, 200, render_llms_txt())
        if p == "/openapi.json":
            return response(self, 200, render_openapi())
        if p == "/install.sh":
            return respond_text(self, 200, INSTALL_SH, content_type="text/x-shellscript; charset=utf-8")
        if p == "/favicon.ico":
            return respond_text(self, 204, "", content_type="image/x-icon")
        if p == "/v1/health":
            import threading as _th
            # Inventario de threads daemon que esperamos vivos. Si alguno
            # murió, "ok" pasa a false (Render restartea el container).
            expected_daemons = {"ami-kyc-purge", "ami-backup"}
            daemons_alive = {t.name for t in _th.enumerate()
                             if t.daemon and t.name in expected_daemons}
            missing_daemons = expected_daemons - daemons_alive
            # Sólo marcamos ok=False si arrancamos con la persistencia activa
            # (modo prod). En tests / mode :memory: los daemons no se lanzan.
            persistence_on = ami_storage.is_enabled()
            return response(self, 200, {
                "ok": (not missing_daemons) or not persistence_on,
                "service": "ami",
                "time": now(),
                "telco": get_active_adapter().health(),
                "backup": ami_backup.stats(),
                "storage": ami_storage.stats(),
                "daemons": {
                    "alive": sorted(daemons_alive),
                    "expected": sorted(expected_daemons),
                    "missing": sorted(missing_daemons),
                },
                "state": {
                    "events": len(STATE.get("events", [])),
                    "events_cap": MAX_EVENTS_RETAINED,
                    "active_mids": sum(1 for m in STATE["mobile_identities"].values()
                                        if m.get("status") == "active"),
                    "kyc_pending": sum(1 for k in STATE["kyc_verifications"].values()
                                        if k.get("status") in ("pending", "submitted")),
                    "webhooks": len(STATE.get("webhooks", {})),
                },
            })
        if p == "/metrics":
            # Endpoint de scrape Prometheus. Público por convención (no expone PII).
            return respond_text(self, 200, ami_metrics.REGISTRY.render(),
                                content_type=ami_metrics.CONTENT_TYPE)
        if p == "/v1/sim-options":
            return response(self, 200, {"countries": COUNTRIES})
        if p == "/v1/events":
            # Multi-tenant: filtramos por los recursos del account del request.
            # Eventos sin owner derivable (system-level) los devolvemos solo
            # si el caller no tiene scope (modo admin / dev).
            account_id = _request_account_id(self)
            own_mids = {m["id"] for m in STATE["mobile_identities"].values()
                        if not m.get("account_id") or m.get("account_id") == account_id}
            own_sims = {s["id"] for s in STATE["sim_requests"].values()
                        if not s.get("account_id") or s.get("account_id") == account_id}
            own_contracts = {c["id"] for c in STATE["contracts"].values()
                             if not c.get("account_id") or c.get("account_id") == account_id}
            own_customers = {c["id"] for c in STATE["customers"].values()
                             if not c.get("account_id") or c.get("account_id") == account_id}
            own_offers = {o["id"] for o in STATE["offers"].values()
                          if not o.get("account_id") or o.get("account_id") == account_id}
            def _ev_owned(ev):
                t, eid = ev.get("entity_type"), ev.get("entity_id")
                if t == "mobile_identity": return eid in own_mids
                if t == "sim_request":    return eid in own_sims
                if t == "contract":       return eid in own_contracts
                if t == "customer":       return eid in own_customers
                if t == "offer":          return eid in own_offers
                # Para sms/call/webhook hacemos lookup por entity_id porque
                # algunos events no llevan `mid` en data (sms_sent, call_completed)
                if t == "sms_message":
                    msg = STATE["sms_messages"].get(eid)
                    return bool(msg) and msg.get("mid") in own_mids
                if t == "call":
                    c = STATE["calls"].get(eid)
                    return bool(c) and c.get("mid") in own_mids
                if t == "webhook":
                    wh = STATE["webhooks"].get(eid)
                    return bool(wh) and wh.get("mid") in own_mids
                return False
            events = [e for e in STATE["events"] if _ev_owned(e)][-100:]
            return response(self, 200, {"events": events})
        # Página HTML de firma (público; no requiere API key, pero SÍ exige
        # el signing_token de 128 bits que viene como ?t=... en signature_url).
        m = re.match(r"^/v1/sign/([^/]+)$", p)
        if m:
            contract = STATE["contracts"].get(m.group(1))
            if not contract:
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            qs = dict(re.findall(r"([^&=?]+)=([^&]*)", urlparse(self.path).query or ""))
            presented = qs.get("t", "")
            expected = contract.get("signing_token")
            if expected and not (presented and secrets.compare_digest(presented, expected)):
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            return respond_html(self, 200, render_sign_page(contract))
        # Página pública de identidad móvil
        m = re.match(r"^/identity/([^/]+)$", p)
        if m:
            identity = STATE["mobile_identities"].get(m.group(1))
            if not identity:
                return respond_html(self, 404, render_identity_404())
            return respond_html(self, 200, render_identity_page(identity))
        # Páginas públicas con chrome común (header + footer reutilizables
        # de ami_chrome). El chrome reemplaza el <header>/<footer> custom
        # que cada página traía para garantizar nav consistente entre todas.
        if p == "/spec":
            html = _with_lang_augment(render_spec_page(lang=lang), lang=lang, has_en_content=True)
            return respond_html(self, 200, _with_chrome(html, active="spec", lang=lang))
        if p == "/partners":
            html = _with_lang_augment(render_partnership_page(lang=lang), lang=lang, has_en_content=True)
            return respond_html(self, 200, _with_chrome(html, active="partners", lang=lang))
        if p == "/experience":
            base = (ami_pages_en.render_experience_page_en() if lang == "en"
                    else render_experience_page())
            html = _with_lang_augment(base, lang=lang, has_en_content=True)
            return respond_html(self, 200, _with_chrome(html, active="experience", lang=lang))
        if p == "/diagram":
            base = (ami_pages_en.render_diagram_page_en() if lang == "en"
                    else render_diagram_page())
            html = _with_lang_augment(base, lang=lang, has_en_content=True)
            return respond_html(self, 200, _with_chrome(html, active="diagram", lang=lang))
        if p == "/docs":
            return respond_html(self, 200,
                _with_chrome(ami_docs.render_docs_page(lang=lang),
                              active="docs", lang=lang))
        if p == "/live":
            # Demo cinemática controlable — chrome común para nav consistente.
            return respond_html(self, 200,
                _with_chrome(ami_live.render_live_page(lang=lang),
                              active="live", lang=lang))
        if p == "/use-cases":
            # El módulo ya incluye chrome inline (header_html + footer_html
            # llamados internamente), así que no aplicamos _with_chrome.
            return respond_html(self, 200, ami_use_cases.render_use_cases_page(lang=lang))
        if p == "/pricing":
            # El módulo trae <header></header> y <footer></footer> vacíos —
            # _with_chrome los rellena con el común.
            return respond_html(self, 200,
                _with_chrome(ami_pricing.render_pricing_page(lang=lang),
                              active="pricing", lang=lang))
        if p == "/calculator":
            return respond_html(self, 200,
                _with_chrome(ami_pricing.render_calculator_page(lang=lang),
                              active="pricing", lang=lang))
        if p == "/waitlist":
            # Página standalone con form grande. Chrome común para nav consistente.
            return respond_html(self, 200,
                _with_chrome(ami_waitlist.render_waitlist_page(lang=lang),
                              active="waitlist", lang=lang))
        if p == "/pitch":
            # Modo presentación full-screen — NO usa chrome común
            # (es una herramienta de presentación, no una página de catálogo).
            return respond_html(self, 200, ami_pitch.render_pitch_page(lang=lang))
        if p == "/sandbox":
            # Playground interactivo — el módulo ya incluye chrome inline.
            return respond_html(self, 200, ami_sandbox.render_sandbox_page(lang=lang))
        if p == "/status":
            # Status público — chrome inline ya incluido.
            return respond_html(self, 200, ami_status.render_status_page(lang=lang))
        if p == "/security":
            # Whitepaper de seguridad — chrome inline ya incluido.
            return respond_html(self, 200, ami_security_page.render_security_page(lang=lang))
        if p == "/poc-co":
            # Página de la PoC con el partner CO — usada en la reunión técnica
            # con Julián / Javier Cruz. Standalone (no chrome) para que la
            # narrativa sea full-screen.
            return respond_html(self, 200, ami_poc_co.render_poc_co_page())
        if p == "/poc-co/sip":
            # SIP Interconnect Spec — detalle troncal SIP para la reunión
            # "Revisión Troncal SIP" 2026-05-28 16:30.
            return respond_html(self, 200, ami_sip_interconnect.render_sip_interconnect_page())
        if p == "/internal/brief-co":
            # Brief privado de Daniel para la reunión PoC CO — estrategia
            # negociadora. NUNCA debe servirse sin auth admin.
            # Acepta Bearer, cookie sesión panel KYC, o ?key=<AMI_ADMIN_KEY>.
            if not check_kyc_admin_auth(self):
                return respond_html(self, 401,
                    "<html><body style='font-family:sans-serif;background:#06060a;color:#ededf2;padding:3rem;text-align:center'>"
                    "<h2>401 · admin auth requerido</h2>"
                    "<p>Abre <code>/internal/brief-co?key=&lt;AMI_ADMIN_KEY&gt;</code> "
                    "o entra primero al <a href='/panel/kyc' style='color:#5dd1ff'>panel KYC</a>.</p>"
                    "</body></html>")
            return respond_html(self, 200, ami_internal_brief.render_internal_brief_page())
        if p == "/internal/vps-co":
            # Runbook para provisionar el VPS de Asterisk/Kannel con IP fija.
            # Privado: revela infra interna + variables. Auth admin obligatorio.
            if not check_kyc_admin_auth(self):
                return respond_html(self, 401,
                    "<html><body style='font-family:sans-serif;background:#06060a;color:#ededf2;padding:3rem;text-align:center'>"
                    "<h2>401 · admin auth requerido</h2>"
                    "<p>Abre <code>/internal/vps-co?key=&lt;AMI_ADMIN_KEY&gt;</code>.</p>"
                    "</body></html>")
            return respond_html(self, 200, ami_vps_runbook.render_vps_runbook_page())
        if p == "/v1/admin/waitlist":
            # Listado para admin (auth: AMI_ADMIN_KEY).
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            return response(self, 200, {"entries": ami_waitlist.list_waitlist_entries()})

        # KYC público — el humano abre la URL desde su email/SMS.
        m = re.match(r"^/kyc/([^/]+)$", p)
        if m:
            token = m.group(1)
            kyc = next((k for k in STATE["kyc_verifications"].values()
                        if k.get("token") == token), None)
            if not kyc:
                return respond_html(self, 404, ami_kyc.render_kyc_not_found())
            # Lazy-expire: si está pending y caducó el TTL, marcamos antes
            # de renderizar para que el submit también lo bloquee coherente.
            if kyc.get("status") == "pending" and ami_kyc.is_expired(kyc):
                kyc["status"] = "expired"
            customer = STATE["customers"].get(kyc.get("customer_id")) or {}
            return respond_html(self, 200, ami_kyc.render_kyc_form(kyc, customer))

        # Admin: listar KYCs pendientes de revisión humana.
        if p == "/v1/admin/kyc":
            if not check_kyc_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            include_imgs = "include_images=1" in (urlparse(self.path).query or "")
            entries = [ami_kyc.kyc_summary(k, include_images=include_imgs)
                       for k in STATE["kyc_verifications"].values()]
            entries.sort(key=lambda k: k.get("created_at") or "", reverse=True)
            return response(self, 200, {"kycs": entries, "count": len(entries)})

        # Panel HTML para que el operador revise visualmente. Cookie,
        # Bearer o ?key= como métodos de auth.
        if p == "/panel/kyc":
            if not check_kyc_admin_auth(self):
                return respond_html(self, 200, ami_kyc_admin.render_login())
            entries = [ami_kyc.kyc_summary(k, include_images=True)
                       for k in STATE["kyc_verifications"].values()]
            entries.sort(key=lambda k: k.get("created_at") or "", reverse=True)
            return respond_html(self, 200, ami_kyc_admin.render_admin_kyc_list(entries))

        # Panel del cliente: auth por cookie ami-panel-token (httpOnly, sólo
        # validable comparando contra AMI_API_KEY).
        if p == "/panel/login":
            return respond_html(self, 200, ami_panel.render_login())
        if p == "/panel":
            acct_id = ami_panel.check_panel_cookie(self)
            if not acct_id:
                return _redirect(self, "/panel/login")
            return respond_html(self, 200, ami_panel.render_home(acct_id))
        m = re.match(r"^/panel/mid/([^/]+)$", p)
        if m:
            acct_id = ami_panel.check_panel_cookie(self)
            if not acct_id:
                return _redirect(self, "/panel/login")
            code, html = ami_panel.render_mid_detail(m.group(1), acct_id)
            return respond_html(self, code, html)
        # Leer límites del MID. Auth: API key del customer.
        m = re.match(r"^/v1/mobile-identities/([^/]+)/limits$", p)
        if m:
            identity = STATE["mobile_identities"].get(m.group(1))
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            limits = identity.setdefault("limits", ami_limits.new_limits())
            return response(self, 200, {"mid": m.group(1), "limits": limits})

        # Leer el usage del MID. Auth: API key del customer (vista global).
        m = re.match(r"^/v1/mobile-identities/([^/]+)/usage$", p)
        if m:
            mid = m.group(1)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            return response(self, 200, ami_limits.usage_snapshot(identity))

        # Admin: listar customer-accounts (auth: AMI_ADMIN_KEY).
        if p == "/v1/admin/customers":
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            accounts = []
            for c in STATE["customer_accounts"].values():
                accounts.append({
                    "id": c["id"], "name": c.get("name"),
                    "status": c.get("status"),
                    "billing_email": c.get("billing_email"),
                    "api_key_prefix": c.get("api_key_prefix"),
                    "created_at": c.get("created_at"),
                    "suspended_at": c.get("suspended_at"),
                })
            return response(self, 200, {"accounts": accounts, "count": len(accounts)})

        # Listar webhooks salientes de un MID. Auth: API key del customer.
        m = re.match(r"^/v1/mobile-identities/([^/]+)/webhooks$", p)
        if m:
            mid = m.group(1)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            whs = [ami_webhooks.webhook_summary(w)
                   for w in STATE["webhooks"].values() if w.get("mid") == mid]
            return response(self, 200, {"webhooks": whs, "count": len(whs)})

        # Listar webhooks account-scoped (reciben kyc.*, system.*, etc.).
        # Auth: API key del customer; el account_id sale del Bearer.
        if p == "/v1/account/webhooks":
            acc = _request_account_id(self)
            if not acc:
                return response(self, 401, {"error": "unauthorized"})
            whs = [ami_webhooks.webhook_summary(w)
                   for w in STATE["webhooks"].values() if w.get("account_id") == acc]
            return response(self, 200, {"webhooks": whs, "count": len(whs)})

        m = re.match(r"^/v1/(sim-requests|offers|customers|contracts|mobile-identities)/([^/]+)$", p)
        if m:
            table = m.group(1).replace("-", "_")
            obj = STATE.get(table, {}).get(m.group(2))
            if not obj: return response(self, 404, {"error": "not_found", "id": m.group(2)})
            # Multi-tenant scoping: si el objeto tiene account_id y no coincide
            # con el del request, devolvemos 404 (no 403 para no filtrar existencia).
            if not _scoped(obj, _request_account_id(self)):
                return response(self, 404, {"error": "not_found", "id": m.group(2)})
            return response(self, 200, obj)
        return response(self, 404, {"error": "unknown_route", "path": p})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        p = urlparse(self.path).path

        # Endpoints scoped por agent_token: hacen su propia validación
        # antes del check_auth global del customer.
        if p == "/v1/agent/sms/send":
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            code, body = send_sms_outbound(rec["mid"], data.get("to"), data.get("body", ""))
            return response(self, code, body)

        # DLR del SMSC: Kannel (u otro) reporta el estado final del SMS saliente.
        # Body esperado (form-encoded por Kannel o JSON): {msg_id, status, telco_ref}
        # donde status ∈ {"delivered","failed"}. Permite también query string
        # (?msg_id=...&status=...) para compatibilidad con sustituciones de Kannel.
        if p == "/v1/_telco/sms/dlr":
            if not TELCO_INBOUND_KEY:
                return response(self, 503, {"error": "telco_inbound_disabled"})
            if not secrets.compare_digest(self.headers.get("X-Telco-Key", ""), TELCO_INBOUND_KEY):
                return response(self, 401, {"error": "invalid_telco_key"})
            # Acepta JSON body o form/query (Kannel usa GET-via-POST con query).
            qs = dict(re.findall(r"([^&=?]+)=([^&]*)", urlparse(self.path).query or ""))
            try: data = read_json(self)
            except Exception: data = {}
            msg_id = data.get("msg_id") or qs.get("msg_id")
            status_raw = (data.get("status") or qs.get("status") or "").lower()
            telco_ref = data.get("telco_ref") or qs.get("telco_ref")
            if not msg_id:
                return response(self, 400, {"error": "missing_msg_id"})
            msg = STATE["sms_messages"].get(msg_id)
            if not msg:
                return response(self, 404, {"error": "sms_not_found"})
            if status_raw in ("delivered", "1", "ok"):
                msg["status"] = "delivered"
                msg["delivered_at"] = now()
                if telco_ref: msg["telco_ref"] = telco_ref
                event("sms_delivered", "sms_message", msg_id, {})
                ami_metrics.SMS_SENT.inc(status="delivered")
                ami_webhooks.dispatch_event("sms.delivered", msg["mid"], msg)
                return response(self, 200, msg)
            if status_raw in ("failed", "rejected", "expired", "2", "4", "8", "16"):
                if msg["status"] in ("queued", "sent"):
                    msg["status"] = "failed"
                    if telco_ref: msg["telco_ref"] = telco_ref
                    event("sms_failed", "sms_message", msg_id,
                          {"reason": status_raw, "telco_ref": telco_ref})
                    ami_metrics.SMS_SENT.inc(status="failed")
                    ami_metrics.log_warn("sms_failed", msg_id=msg_id,
                                          reason=status_raw,
                                          attempt=msg.get("retry_count", 0))
                    ami_webhooks.dispatch_event("sms.failed", msg["mid"], msg)
                    # Retry automático: si quedan intentos, encolamos otro.
                    if msg.get("retry_count", 0) < SMS_MAX_RETRIES:
                        _schedule_sms_retry(msg_id)
                return response(self, 200, msg)
            return response(self, 400, {"error": "unknown_status", "status": status_raw})

        # Webhook del partner telco para entregar un SMS entrante hacia un MID nuestro.
        # Auth por clave compartida en header X-Telco-Key (env AMI_TELCO_INBOUND_KEY).
        # Si la clave no está configurada, el endpoint está deshabilitado.
        if p == "/v1/_telco/sms/inbound":
            if not TELCO_INBOUND_KEY:
                return response(self, 503, {"error": "telco_inbound_disabled"})
            if not secrets.compare_digest(self.headers.get("X-Telco-Key", ""), TELCO_INBOUND_KEY):
                return response(self, 401, {"error": "invalid_telco_key"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            code, body = record_sms_inbound(
                from_raw=data.get("from"),
                to_raw=data.get("to"),
                body=data.get("body", ""),
                telco_ref=data.get("telco_ref"),
            )
            return response(self, code, body)

        # Originar una llamada saliente. bridge-by-API: AMI marca al PSTN y
        # bridgea por SIP al callback_sip_uri (típicamente el endpoint Realtime
        # del cliente). AMI NO ejecuta voz — sólo cursa la pipa.
        if p == "/v1/agent/calls/place":
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            code, body = place_call_outbound(
                rec["mid"], data.get("to"), data.get("callback_sip_uri", ""))
            return response(self, code, body)

        # Colgar una llamada del MID asociado al token.
        m = re.match(r"^/v1/agent/calls/([^/]+)/hangup$", p)
        if m:
            rec = validate_agent_token(self)
            if not rec:
                return response(self, 401, {"error": "invalid_agent_token"})
            code, body = hangup_call(rec["mid"], m.group(1))
            return response(self, code, body)

        # Webhooks del partner telco para voz (mismo X-Telco-Key que SMS).
        if p == "/v1/_telco/calls/inbound":
            if not TELCO_INBOUND_KEY:
                return response(self, 503, {"error": "telco_inbound_disabled"})
            if not secrets.compare_digest(self.headers.get("X-Telco-Key", ""), TELCO_INBOUND_KEY):
                return response(self, 401, {"error": "invalid_telco_key"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            code, body = route_call_inbound(
                from_raw=data.get("from"),
                to_raw=data.get("to"),
                telco_ref=data.get("telco_ref"),
            )
            return response(self, code, body)

        m = re.match(r"^/v1/_telco/calls/([^/]+)/status$", p)
        if m:
            if not TELCO_INBOUND_KEY:
                return response(self, 503, {"error": "telco_inbound_disabled"})
            if not secrets.compare_digest(self.headers.get("X-Telco-Key", ""), TELCO_INBOUND_KEY):
                return response(self, 401, {"error": "invalid_telco_key"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            new_status = data.get("status")
            if not new_status:
                return response(self, 400, {"error": "missing_status"})
            patch = {k: data.get(k) for k in ("hangup_cause", "telco_ref")
                     if data.get(k) is not None}
            code, body = update_call_status(m.group(1), new_status, **patch)
            return response(self, code, body)

        if not is_public("POST", p) and not check_auth(self):
            return response(self, 401, {"error": "unauthorized"})

        # Admin: crear customer-account (auth: AMI_ADMIN_KEY).
        # Body: {name, billing_email}. Devuelve la api_key en plano UNA SOLA VEZ.
        if p == "/v1/admin/customers":
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            name = (data.get("name") or "").strip()
            if not name:
                return response(self, 400, {"error": "missing_name"})
            plain_key = "amik_live_" + secrets.token_hex(24)
            acct_id = new_id("acct")
            acct = {
                "id": acct_id,
                "name": name,
                "api_key_hash": _hash_api_key(plain_key),
                "api_key_prefix": plain_key[:14],
                "status": "active",
                "billing_email": data.get("billing_email"),
                "created_at": now(),
                "suspended_at": None,
                "suspend_reason": None,
            }
            STATE["customer_accounts"][acct_id] = acct
            event("customer_account_created", "customer_account", acct_id,
                  {"name": name, "billing_email": acct["billing_email"]})
            return response(self, 201, {
                "id": acct_id, "name": name,
                "api_key": plain_key,
                "api_key_hint": "Guarda esta API key. Es la única vez que se "
                                 "muestra en plano. Tras esto, solo el prefijo "
                                 "estará visible.",
                "billing_email": acct["billing_email"],
                "status": "active",
                "created_at": acct["created_at"],
            })

        # OpenAI Realtime webhook · recibe realtime.call.incoming y acepta
        # la llamada. NO requiere auth admin/customer porque OpenAI firma con
        # webhook-signature; verificamos HMAC contra OPENAI_WEBHOOK_SECRET en
        # el handler. Si el secret no está seteado, modo DEV (acepta todo,
        # log a stderr).
        if p == "/v1/_openai/realtime/webhook":
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(n) if n > 0 else b""
            except Exception as e:
                return response(self, 400, {"error": "read_error", "detail": str(e)})
            wh_headers = {k.lower(): v for k, v in self.headers.items()}
            code, body_out = ami_openai_realtime.handle_webhook(wh_headers, raw)
            return response(self, code, body_out)

        # Admin: provisionar un MID manualmente con un número/sip_uri concretos.
        # Útil para PoC con un partner telco real que ya nos asigna el número,
        # saltando el flow contractual completo (customer→contract→sign).
        # Body: {phone_number, inbound_sip_uri, customer_name (opt), account_id (opt)}.
        if p == "/v1/admin/mobile-identities/manual":
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            try: data = read_json(self)
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            phone = _normalize_msisdn(data.get("phone_number") or "")
            if not phone:
                return response(self, 400, {"error": "invalid_phone_number",
                                             "detail": "Requires E.164 (e.g. +573336033869)"})
            sip_uri = (data.get("inbound_sip_uri") or "").strip() or None
            account_id = data.get("account_id") or "cust_default"
            # Si ya hay un MID activo con ese número, lo devolvemos (idempotente).
            existing_mid, existing_id = _find_mid_by_phone(phone)
            if existing_mid:
                if sip_uri:
                    existing_id["inbound_sip_uri"] = sip_uri
                event("mobile_identity_manual_updated", "mobile_identity", existing_mid,
                      {"phone_number": phone, "inbound_sip_uri": sip_uri})
                return response(self, 200, {
                    "id": existing_mid, "phone_number": phone,
                    "inbound_sip_uri": sip_uri, "status": "active",
                    "reused": True,
                })
            mid = new_id("mid")
            identity = {
                "id": mid, "status": "active", "phone_number": phone,
                "sim_type": "eSIM",
                "capabilities": ["sms", "voice", "data"],
                "account_id": account_id,
                "inbound_sip_uri": sip_uri,
                "provider_activation_id": new_id("manualact"),
                "activated_at": now(),
                "provenance": "admin_manual",
            }
            STATE["mobile_identities"][mid] = identity
            event("mobile_identity_manual_created", "mobile_identity", mid,
                  {"phone_number": phone, "inbound_sip_uri": sip_uri,
                   "account_id": account_id})
            return response(self, 201, {
                "id": mid, "phone_number": phone,
                "inbound_sip_uri": sip_uri,
                "status": "active",
                "account_id": account_id,
            })

        # Admin: rotar la API key de un customer (hard rotate).
        m = re.match(r"^/v1/admin/customers/([^/]+)/rotate-key$", p)
        if m:
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            acct = STATE["customer_accounts"].get(m.group(1))
            if not acct:
                return response(self, 404, {"error": "customer_account_not_found"})
            plain_key = "amik_live_" + secrets.token_hex(24)
            acct["api_key_hash"] = _hash_api_key(plain_key)
            acct["api_key_prefix"] = plain_key[:14]
            event("customer_account_key_rotated", "customer_account", acct["id"], {})
            return response(self, 200, {
                "id": acct["id"], "api_key": plain_key,
                "api_key_hint": "Actualiza tus clientes con esta nueva clave; "
                                 "la anterior ha quedado invalidada.",
                "rotated_at": now(),
            })

        # Admin: suspender/reactivar un customer.
        m = re.match(r"^/v1/admin/customers/([^/]+)/(suspend|activate)$", p)
        if m:
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            action = m.group(2)
            acct = STATE["customer_accounts"].get(m.group(1))
            if not acct:
                return response(self, 404, {"error": "customer_account_not_found"})
            try: data = read_json(self)
            except Exception: data = {}
            if action == "suspend":
                acct["status"] = "suspended"
                acct["suspended_at"] = now()
                acct["suspend_reason"] = data.get("reason")
                event("customer_account_suspended", "customer_account", acct["id"],
                      {"reason": acct["suspend_reason"]})
            else:
                acct["status"] = "active"
                acct["suspended_at"] = None
                acct["suspend_reason"] = None
                event("customer_account_activated", "customer_account", acct["id"], {})
            return response(self, 200, {"id": acct["id"], "status": acct["status"]})

        # Panel login: valida la API key vía form contra customer_accounts.
        # Rate-limit per-IP para evitar credential stuffing.
        if p == "/panel/login":
            client_ip = self.client_address[0] if self.client_address else "?"
            if _panel_login_throttled(client_ip):
                ami_metrics.log_warn("panel_login_throttled", ip=client_ip)
                return respond_html(self, 429,
                    ami_panel.render_login(
                        error="Too many attempts. Wait a few minutes."))
            form = read_form(self)
            key = (form.get("key") or "").strip()
            cust = _customer_by_api_key(key) if key else None
            if not cust:
                ami_metrics.log_warn("panel_login_failed", ip=client_ip)
                return respond_html(self, 401,
                    ami_panel.render_login(error="Invalid API key."))
            return _redirect(self, "/panel", set_cookie=key)

        if p == "/panel/logout":
            return _redirect(self, "/panel/login", clear_cookie=True)

        # Callback de firma (HTML form). Antes del read_json porque es form-urlencoded.
        # Mismo gate del signing_token: el form en la página de firma manda
        # ?t=... en la action.
        m = re.match(r"^/v1/sign/([^/]+)/confirm$", p)
        if m:
            contract = STATE["contracts"].get(m.group(1))
            if not contract:
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            qs = dict(re.findall(r"([^&=?]+)=([^&]*)", urlparse(self.path).query or ""))
            presented = qs.get("t", "")
            expected = contract.get("signing_token")
            if expected and not (presented and secrets.compare_digest(presented, expected)):
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            code, body = sign_contract(m.group(1))
            if code == 404:
                return respond_html(self, 404, render_sign_error("Contrato no encontrado."))
            # En ambos casos (recién firmado o ya firmado) renderizamos la página actualizada.
            contract = body if code == 200 else body["contract"]
            return respond_html(self, 200, render_sign_page(contract))

        # Panel KYC admin: login / logout (cookie httpOnly).
        if p == "/panel/kyc/login":
            form = read_form(self)
            key = form.get("key") or ""
            reviewer = (form.get("reviewer") or "").strip()
            if not ADMIN_KEY or not key or not secrets.compare_digest(key, ADMIN_KEY):
                return respond_html(self, 401, ami_kyc_admin.render_login("Admin key incorrecta"))
            if not reviewer:
                return respond_html(self, 400, ami_kyc_admin.render_login("Falta el nombre del revisor"))
            # Cookie httpOnly + Secure (si AMI_PUBLIC_URL es https). SameSite=Strict.
            secure = "; Secure" if (os.environ.get("AMI_PUBLIC_URL", "").startswith("https://")) else ""
            # CSRF token: random 32 bytes, accesible al JS (Path=/, no HttpOnly)
            # para incluirlo en el header X-CSRF-Token de las acciones admin.
            csrf_token = secrets.token_urlsafe(32)
            self.send_response(303)
            self.send_header("Location", "/panel/kyc")
            self.send_header("Set-Cookie",
                f"{ami_kyc_admin.COOKIE_NAME}={key}; HttpOnly; Path=/; SameSite=Strict; Max-Age=28800{secure}")
            self.send_header("Set-Cookie",
                f"ami-kyc-reviewer={reviewer}; HttpOnly; Path=/; SameSite=Strict; Max-Age=28800{secure}")
            self.send_header("Set-Cookie",
                f"ami-kyc-csrf={csrf_token}; Path=/; SameSite=Strict; Max-Age=28800{secure}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if p == "/panel/kyc/logout":
            self.send_response(303)
            self.send_header("Location", "/panel/kyc")
            self.send_header("Set-Cookie",
                f"{ami_kyc_admin.COOKIE_NAME}=; HttpOnly; Path=/; Max-Age=0")
            self.send_header("Set-Cookie",
                "ami-kyc-reviewer=; HttpOnly; Path=/; Max-Age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # KYC público — el humano sube las imágenes desde /kyc/{token}.
        m = re.match(r"^/kyc/([^/]+)/submit$", p)
        if m:
            token = m.group(1)
            kyc = next((k for k in STATE["kyc_verifications"].values()
                        if k.get("token") == token), None)
            if not kyc:
                return response(self, 404, {"error": "kyc_not_found"})
            if kyc.get("status") != "pending":
                return response(self, 409, {"error": "kyc_already_submitted",
                                             "status": kyc.get("status")})
            try: data = read_json(self, max_bytes=20_000_000)  # 3 imágenes × ~5.4 MB
            except Exception as e:
                return response(self, 400, {"error": "invalid_json", "detail": str(e)})
            ok, reason = ami_kyc.submit_images(
                kyc,
                dni_front_b64=data.get("dni_front_b64"),
                dni_back_b64=data.get("dni_back_b64"),
                selfie_b64=data.get("selfie_b64"),
            )
            if not ok:
                return response(self, 400, {"error": reason})
            event("kyc_submitted", "kyc_verification", kyc["id"],
                  {"sim_request_id": kyc["sim_request_id"]})
            ami_webhooks.dispatch_account_event("kyc.submitted",
                                         kyc.get("account_id") or "",
                                         {"kyc_id": kyc["id"],
                                          "sim_request_id": kyc["sim_request_id"]})
            ami_notify.send_kyc_submitted_to_ops(kyc)
            return response(self, 200, ami_kyc.kyc_summary(kyc))

        # KYC admin: purge manual (auth: AMI_ADMIN_KEY). Útil para forzar
        # la limpieza desde un cron externo o desde el panel sin esperar al
        # daemon de cada hora.
        if p == "/v1/admin/kyc/purge":
            if not check_kyc_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            if not check_kyc_admin_csrf(self):
                return response(self, 403, {"error": "csrf_token_invalid"})
            result = ami_kyc.purge_expired(STATE)
            return response(self, 200, result)

        # Backup manual: fuerza un snapshot atómico de la SQLite. Útil antes
        # de un despliegue o de tocar el state.
        if p == "/v1/admin/backup/now":
            if not check_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            return response(self, 200, ami_backup.snapshot_once())

        # KYC admin: verify / reject (auth: AMI_ADMIN_KEY).
        m = re.match(r"^/v1/admin/kyc/([^/]+)/(verify|reject)$", p)
        if m:
            if not check_kyc_admin_auth(self):
                return response(self, 401, {"error": "unauthorized_admin"})
            if not check_kyc_admin_csrf(self):
                return response(self, 403, {"error": "csrf_token_invalid"})
            kyc_id, action = m.group(1), m.group(2)
            kyc = STATE["kyc_verifications"].get(kyc_id)
            if not kyc:
                return response(self, 404, {"error": "kyc_not_found"})
            try: data = read_json(self)
            except Exception: data = {}
            if action == "verify":
                kyc["status"] = "verified"
                kyc["verified_at"] = now()
                kyc["reviewer"] = data.get("reviewer") or "admin"
                event("kyc_verified", "kyc_verification", kyc_id,
                      {"reviewer": kyc["reviewer"]})
                ami_webhooks.dispatch_account_event("kyc.verified",
                                             kyc.get("account_id") or "",
                                             {"kyc_id": kyc_id,
                                              "sim_request_id": kyc["sim_request_id"]})
                ami_notify.send_kyc_verified_to_human(kyc)
            else:
                kyc["status"] = "rejected"
                kyc["rejection_reason"] = data.get("reason") or "no_reason_specified"
                kyc["reviewer"] = data.get("reviewer") or "admin"
                kyc["rejected_at"] = now()
                event("kyc_rejected", "kyc_verification", kyc_id,
                      {"reason": kyc["rejection_reason"]})
                ami_webhooks.dispatch_account_event("kyc.rejected",
                                             kyc.get("account_id") or "",
                                             {"kyc_id": kyc_id,
                                              "reason": kyc["rejection_reason"]})
                ami_notify.send_kyc_rejected_to_human(kyc)
            return response(self, 200, ami_kyc.kyc_summary(kyc))

        # KYC initiate: el agente lo dispara tras customer-data, AMI genera
        # el token y devuelve la URL pública para que el customer la envíe al
        # representante legal por email/SMS/WhatsApp.
        m = re.match(r"^/v1/sim-requests/([^/]+)/kyc/initiate$", p)
        if m:
            req = STATE["sim_requests"].get(m.group(1))
            if not req or not _scoped(req, _request_account_id(self)):
                return response(self, 404, {"error": "sim_request_not_found"})
            customer_id = req.get("customer_id")
            customer = STATE["customers"].get(customer_id or "")
            if not customer:
                return response(self, 409, {"error": "customer_data_missing",
                                             "detail": "submit customer-data first"})
            # Evitar duplicados: si ya hay un KYC pending/submitted para esta
            # sim_request, devolverlo en lugar de crear uno nuevo.
            existing = next((k for k in STATE["kyc_verifications"].values()
                             if k.get("sim_request_id") == req["id"]
                             and k.get("status") in ("pending", "submitted", "in_review", "verified")), None)
            if existing:
                base = (os.environ.get("AMI_PUBLIC_URL") or "http://localhost:8000").rstrip("/")
                return response(self, 200, {
                    "kyc_id": existing["id"],
                    "status": existing["status"],
                    "verification_url": f"{base}/kyc/{existing['token']}",
                    "rep_email": existing.get("rep_email"),
                    "already_existed": True,
                })
            kyc = ami_kyc.new_verification(
                sim_request_id=req["id"],
                customer_id=customer_id,
                account_id=req.get("account_id"),
                rep_email=customer.get("billing_email", ""),
            )
            STATE["kyc_verifications"][kyc["id"]] = kyc
            event("kyc_initiated", "kyc_verification", kyc["id"],
                  {"sim_request_id": req["id"], "rep_email": kyc["rep_email"]})
            base = (os.environ.get("AMI_PUBLIC_URL") or "http://localhost:8000").rstrip("/")
            verification_url = f"{base}/kyc/{kyc['token']}"
            notify_result = ami_notify.send_kyc_invitation(kyc, customer, verification_url)
            return response(self, 201, {
                "kyc_id": kyc["id"],
                "status": "pending",
                "verification_url": verification_url,
                "rep_email": kyc["rep_email"],
                "expires_at": kyc["expires_at"],
                "notification": notify_result,
            })

        # Waitlist público: cualquiera puede apuntarse desde /waitlist o el
        # form embebido en la landing. Validación + anti-spam dentro del módulo.
        if p == "/v1/waitlist":
            try: form = read_json(self)
            except Exception as e:
                # Soportar también form-urlencoded (form clásico HTML sin JS).
                try: form = read_form(self)
                except Exception:
                    return response(self, 400, {"error": "invalid_body", "detail": str(e)})
            client_ip = self.client_address[0] if self.client_address else "?"
            user_agent = self.headers.get("User-Agent", "")
            code, body = ami_waitlist.submit_waitlist_entry(
                name=form.get("name") or "",
                email=form.get("email") or "",
                company=form.get("company"),
                use_case=form.get("use_case") or "other",
                context=form.get("context"),
                ip=client_ip,
                user_agent=user_agent,
            )
            return response(self, code, body)

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
                "account_id": _request_account_id(self),
            }
            STATE["sim_requests"][req_id] = req
            event("sim_request_created", "sim_request", req_id, req)

            offer_id = new_id("offer")
            price = COUNTRIES[country]["price"]
            offer = {
                "id": offer_id, "sim_request_id": req_id, "status": "offer_created",
                "country": country, "sim_type": req["sim_type"],
                "capabilities": req["capabilities"] or ["sms", "voice"],
                "account_id": _request_account_id(self),
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
            if not req or not _scoped(req, _request_account_id(self)):
                return response(self, 404, {"error": "sim_request_not_found"})
            if req["status"] in TERMINAL:
                return response(self, 409, {"error": "already_terminal", "status": req["status"]})
            try:
                transition_sim_request(req, "cancelled",
                                       reason=data.get("reason", "cancelled_by_client"))
            except ValueError as e:
                return response(self, 409, {"error": str(e)})
            # Si la SIMRequest tenía MID asociado, revocar sus agent_tokens.
            mid = req.get("mobile_identity_id")
            if mid:
                revoked = revoke_agent_tokens_for_mid(mid, "sim_request_cancelled")
                if revoked:
                    event("agent_token_revoked", "mobile_identity", mid,
                          {"count": revoked, "reason": "sim_request_cancelled"})
            return response(self, 200, req)

        # Submit customer data: crea cliente + lo vincula a la SIMRequest
        m = re.match(r"^/v1/sim-requests/([^/]+)/customer-data$", p)
        if m:
            req = STATE["sim_requests"].get(m.group(1))
            if not req or not _scoped(req, _request_account_id(self)):
                return response(self, 404, {"error": "sim_request_not_found"})
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
            rep_phone = _normalize_msisdn(payload.get("representative_phone") or "")
            customer = {
                "id": cid, "status": "created",
                "legal_name": payload["legal_name"], "tax_id": payload["tax_id"],
                "billing_email": payload["billing_email"], "address": payload["address"],
                "representative_name": payload["representative_name"],
                "representative_phone": rep_phone,
                "created_at": now(),
                "account_id": _request_account_id(self),
            }
            STATE["customers"][cid] = customer
            req["customer_id"] = cid
            # Audit log SIN PII completa: solo ids + nombre legal para auditoría.
            event("customer_created", "customer", cid,
                  {"legal_name": customer["legal_name"],
                   "sim_request_id": req["id"],
                   "account_id": customer.get("account_id")})
            transition_sim_request(req, "customer_data_submitted")
            return response(self, 201, {"sim_request": req, "customer": customer})

        # Aceptar oferta
        m = re.match(r"^/v1/offers/([^/]+)/accept$", p)
        if m:
            offer = STATE["offers"].get(m.group(1))
            if not offer or not _scoped(offer, _request_account_id(self)):
                return response(self, 404, {"error": "offer_not_found"})
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
            rep_phone = _normalize_msisdn(data.get("representative_phone") or "")
            customer = {
                "id": cid, "status": "created",
                "legal_name": data.get("legal_name"), "tax_id": data.get("tax_id"),
                "billing_email": data.get("billing_email"), "address": data.get("address"),
                "representative_name": data.get("representative_name"),
                "representative_phone": rep_phone,
                "created_at": now(),
                "account_id": _request_account_id(self),
            }
            STATE["customers"][cid] = customer
            event("customer_created", "customer", cid,
                  {"legal_name": customer["legal_name"],
                   "account_id": customer.get("account_id")})
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
            # Bloqueo KYC: si el modo KYC está activo (AMI_KYC_REQUIRED=1), se
            # exige un KYC verified vinculado a esta sim_request antes de
            # generar contrato. Default: deshabilitado para no romper el
            # flujo existente; se activa cuando esté la pieza humana operativa.
            if os.environ.get("AMI_KYC_REQUIRED") == "1" and req:
                kyc = next((k for k in STATE["kyc_verifications"].values()
                            if k.get("sim_request_id") == req["id"]), None)
                if not kyc:
                    return response(self, 409, {"error": "kyc_required",
                        "detail": "POST /v1/sim-requests/{id}/kyc/initiate primero"})
                if kyc.get("status") != "verified":
                    return response(self, 409, {"error": "kyc_not_verified",
                        "kyc_status": kyc.get("status"),
                        "kyc_id": kyc["id"]})
            # Si el cliente fue creado por POST /v1/customers (no por /customer-data),
            # vincularlo aquí y avanzar la SIMRequest al estado intermedio.
            if req and req["status"] == "offer_accepted":
                req["customer_id"] = customer_id
                transition_sim_request(req, "customer_data_submitted")
            cid = new_id("contract")
            # signing_token: 128 bits de entropía. La URL pública de firma lleva
            # este token aparte del contract_id (que solo es un slug enumerable).
            # Sin el token la página /v1/sign/{id} responde 404. Esto impide que
            # un atacante que conoce/enumera contract_ids pueda firmar contratos
            # ajenos.
            signing_token = secrets.token_urlsafe(24)
            base_url = os.environ.get("AMI_PUBLIC_URL", "http://localhost:8000").rstrip("/")
            contract = {
                "id": cid, "status": "signature_pending",
                "offer_id": offer_id, "customer_id": customer_id,
                "sim_request_id": offer["sim_request_id"],
                "signature_url": f"{base_url}/v1/sign/{cid}?t={signing_token}",
                "signing_token": signing_token,
                "created_at": now(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "account_id": _request_account_id(self),
            }
            STATE["contracts"][cid] = contract
            # Audit log mínimo (sin signature_url ni PII)
            event("contract_created", "contract", cid,
                  {"offer_id": offer_id, "customer_id": customer_id,
                   "account_id": contract.get("account_id")})
            if req and req["status"] == "customer_data_submitted":
                transition_sim_request(req, "signature_pending")
            return response(self, 201, contract)

        # Firma directa (atajo programático; la firma "real" pasa por /v1/sign/{id}).
        # Multi-tenant: solo el account dueño del contrato puede usar este atajo.
        m = re.match(r"^/v1/contracts/([^/]+)/mock-sign$", p)
        if m:
            contract = STATE["contracts"].get(m.group(1))
            if not contract or not _scoped(contract, _request_account_id(self)):
                return response(self, 404, {"error": "contract_not_found"})
            code, body = sign_contract(m.group(1))
            return response(self, code, body)

        # Actualizar límites del MID (patch parcial). Auth: API key del customer.
        m = re.match(r"^/v1/mobile-identities/([^/]+)/limits$", p)
        if m:
            mid = m.group(1)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            ok, reason, new_limits = ami_limits.update_limits(identity, data or {})
            if not ok:
                return response(self, 400, {"error": reason})
            event("limits_updated", "mobile_identity", mid, {"new": new_limits})
            return response(self, 200, {"mid": mid, "limits": new_limits})

        # Registrar un webhook saliente para un MID. AMI hará POST a la url
        # con eventos del MID (sms.inbound, call.inbound, etc.). El secret
        # devuelto se usa para verificar HMAC-SHA256 de cada entrega.
        m = re.match(r"^/v1/mobile-identities/([^/]+)/webhooks$", p)
        if m:
            mid = m.group(1)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            url = (data.get("url") or "").strip()
            # SSRF guard: validar scheme + IP (no loopback/private/link-local) +
            # no permitir hosts internos. Centralizado en ami_security para
            # poder testear; también re-validamos antes de cada delivery.
            ok_url, reason = ami_security.is_safe_webhook_url(url)
            if not ok_url:
                return response(self, 400, {"error": "invalid_url", "detail": reason})
            events_raw = data.get("events") or ["*"]
            if not isinstance(events_raw, list):
                return response(self, 400, {"error": "invalid_events"})
            for ev in events_raw:
                if ev != "*" and ev not in ami_webhooks.SUPPORTED_EVENTS:
                    return response(self, 400, {"error": "unsupported_event",
                                                "event": ev,
                                                "supported": sorted(ami_webhooks.SUPPORTED_EVENTS)})
            # Cap de webhooks por MID para evitar thread bombs en dispatch.
            mid_webhooks = [w for w in STATE["webhooks"].values() if w["mid"] == mid]
            if len(mid_webhooks) >= MAX_WEBHOOKS_PER_MID:
                return response(self, 409, {"error": "webhook_limit_reached",
                                             "limit": MAX_WEBHOOKS_PER_MID})
            wh = ami_webhooks.new_webhook(mid, url, events_raw)
            STATE["webhooks"][wh["id"]] = wh
            event("webhook_created", "webhook", wh["id"],
                  {"mid": mid, "url": url, "events": events_raw})
            # Devolvemos el secret en PLANO una sola vez en esta respuesta;
            # las consultas posteriores solo muestran prefix.
            return response(self, 201, {
                **ami_webhooks.webhook_summary(wh),
                "secret": wh["secret"],
                "secret_hint": "Guarda este secret. Lo necesitas para verificar "
                               "el header X-Ami-Signature de cada entrega. No se "
                               "podrá recuperar después.",
            })

        # Borrar un webhook. Auth: API key del customer. Multi-tenant scoping:
        # rechazamos si el MID o el webhook no pertenecen al account del request.
        m = re.match(r"^/v1/mobile-identities/([^/]+)/webhooks/([^/]+)/delete$", p)
        if m:
            mid, wh_id = m.group(1), m.group(2)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "webhook_not_found"})
            wh = STATE["webhooks"].get(wh_id)
            if not wh or wh.get("mid") != mid:
                return response(self, 404, {"error": "webhook_not_found"})
            del STATE["webhooks"][wh_id]
            event("webhook_deleted", "webhook", wh_id, {"mid": mid})
            return response(self, 200, {"id": wh_id, "deleted": True})

        # Crear un webhook ACCOUNT-SCOPED (kyc.*, etc.). Auth: API key del customer.
        if p == "/v1/account/webhooks":
            acc = _request_account_id(self)
            if not acc:
                return response(self, 401, {"error": "unauthorized"})
            url = (data.get("url") or "").strip()
            ok_url, reason = ami_security.is_safe_webhook_url(url)
            if not ok_url:
                return response(self, 400, {"error": "invalid_url", "detail": reason})
            events_raw = data.get("events") or ["*"]
            if not isinstance(events_raw, list):
                return response(self, 400, {"error": "invalid_events"})
            for ev in events_raw:
                if ev != "*" and ev not in ami_webhooks.SUPPORTED_EVENTS:
                    return response(self, 400, {"error": "unsupported_event", "event": ev,
                                                 "supported": sorted(ami_webhooks.SUPPORTED_EVENTS)})
            # Cap por account: mismo MAX_WEBHOOKS_PER_MID — protección thread bomb.
            acc_webhooks = [w for w in STATE["webhooks"].values() if w.get("account_id") == acc]
            if len(acc_webhooks) >= MAX_WEBHOOKS_PER_MID:
                return response(self, 409, {"error": "webhook_limit_reached", "limit": MAX_WEBHOOKS_PER_MID})
            wh = ami_webhooks.new_webhook(mid="", url=url, events=events_raw, account_id=acc)
            STATE["webhooks"][wh["id"]] = wh
            event("webhook_created", "webhook", wh["id"],
                  {"account_id": acc, "scope": "account", "url": url, "events": events_raw})
            return response(self, 201, {
                **ami_webhooks.webhook_summary(wh),
                "secret": wh["secret"],
                "secret_hint": "Guarda este secret una sola vez para verificar X-Ami-Signature.",
            })

        # Borrar un webhook account-scoped.
        m = re.match(r"^/v1/account/webhooks/([^/]+)/delete$", p)
        if m:
            acc = _request_account_id(self)
            if not acc:
                return response(self, 401, {"error": "unauthorized"})
            wh_id = m.group(1)
            wh = STATE["webhooks"].get(wh_id)
            if not wh or wh.get("account_id") != acc:
                return response(self, 404, {"error": "webhook_not_found"})
            del STATE["webhooks"][wh_id]
            event("webhook_deleted", "webhook", wh_id, {"account_id": acc})
            return response(self, 200, {"id": wh_id, "deleted": True})

        # Configurar el endpoint SIP de entrantes para un MID. Auth: API key del
        # customer (Nivel 1). El partner usa este URI para hacer SIP-forward de
        # las llamadas entrantes hacia el destino del cliente (su motor de voz,
        # PBX propio, o cualquier destino que hable SIP).
        m = re.match(r"^/v1/mobile-identities/([^/]+)/inbound-config$", p)
        if m:
            mid = m.group(1)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            sip_uri = data.get("inbound_sip_uri")
            # Permitimos pasar null/"" para limpiar la config.
            if sip_uri in (None, ""):
                identity["inbound_sip_uri"] = None
                event("mid_inbound_config_cleared", "mobile_identity", mid, {})
                return response(self, 200, {"mid": mid, "inbound_sip_uri": None})
            if not _validate_sip_uri(sip_uri):
                return response(self, 400, {"error": "invalid_inbound_sip_uri",
                                            "detail": "expected sip:user@host[:port][;params]"})
            identity["inbound_sip_uri"] = sip_uri.strip()
            event("mid_inbound_config_updated", "mobile_identity", mid,
                  {"inbound_sip_uri": identity["inbound_sip_uri"]})
            return response(self, 200, {"mid": mid, "inbound_sip_uri": identity["inbound_sip_uri"]})

        # Activar MobileIdentity (telco mock = lo único realmente simulado)
        # Rotación de agent_token. Auth: API key del customer (Nivel 1).
        m = re.match(r"^/v1/mobile-identities/([^/]+)/rotate-token$", p)
        if m:
            mid = m.group(1)
            identity = STATE["mobile_identities"].get(mid)
            if not identity or not _scoped(identity, _request_account_id(self)):
                return response(self, 404, {"error": "mobile_identity_not_found"})
            if identity.get("status") != "active":
                return response(self, 409, {"error": "mobile_identity_not_active",
                                            "status": identity.get("status")})
            revoked = revoke_agent_tokens_for_mid(mid, "rotated")
            new_plain, _ = mint_agent_token(mid, identity["customer_id"])
            event("agent_token_rotated", "mobile_identity", mid,
                  {"revoked_count": revoked, "new_token_prefix": new_plain[:18]})
            return response(self, 200, {
                "mid": mid,
                "agent_token": new_plain,
                "revoked_previous": revoked,
                "rotated_at": now(),
                "agent_token_hint": "El token anterior queda invalidado al instante. "
                                    "Actualiza AMI_AGENT_TOKEN del agente con este valor.",
            })

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
                "account_id": contract.get("account_id") or _request_account_id(self),
            }
            STATE["mobile_identities"][mid] = identity
            # Audit log mínimo (sin phone_number ni QR url completa).
            event("mobile_identity_active", "mobile_identity", mid,
                  {"contract_id": contract_id, "customer_id": contract["customer_id"],
                   "account_id": identity.get("account_id"),
                   "sim_type": identity["sim_type"]})
            if req and req["status"] == "provisioning":
                transition_sim_request(req, "active")
                req["mobile_identity_id"] = mid

            # Emitir agent_token scoped al MID. Lo devolvemos UNA SOLA VEZ
            # en plano en esta respuesta; en BD guardamos solo el hash.
            agent_token, _ = mint_agent_token(mid, contract["customer_id"])
            event("agent_token_issued", "mobile_identity", mid,
                  {"token_prefix": agent_token[:18]})

            # Copia para no contaminar el record con el plano.
            resp_body = dict(identity)
            resp_body["agent_token"] = agent_token
            resp_body["agent_token_hint"] = (
                "Guarda este token: solo se muestra una vez. "
                "Inyéctalo al agente como AMI_AGENT_TOKEN. "
                "Si se filtra, rota con POST /v1/mobile-identities/" + mid + "/rotate-token."
            )
            return response(self, 201, resp_body)

        return response(self, 404, {"error": "unknown_route", "path": p})


def _kyc_purge_loop():
    """Cron daemon: corre purge_expired cada hora. Borra imágenes vencidas
    y records caducados según política RGPD. Se arranca en __main__ no
    durante import (los tests no lo quieren corriendo)."""
    import time
    interval = int(os.environ.get("AMI_KYC_PURGE_INTERVAL_S") or 3600)
    while True:
        try:
            time.sleep(interval)
            with STATE_LOCK:
                result = ami_kyc.purge_expired(STATE)
            if any(result[k] for k in ("pending_expired", "images_purged", "rejected_dropped")):
                ami_log.info(
                    "kyc_purge",
                    pending_expired=result.get("pending_expired"),
                    images_purged=result.get("images_purged"),
                    rejected_dropped=result.get("rejected_dropped"),
                )
        except Exception as e:
            ami_log.error("kyc_purge_failed", error=str(e), exc_type=type(e).__name__)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    if API_KEY is None:
        ami_log.warn("auth_disabled", reason="ami_api_key_not_set", mode="dev")
    else:
        ami_log.info("auth_enabled", scheme="bearer")
    ami_log.info("server_listening", port=port)
    threading.Thread(target=_kyc_purge_loop, daemon=True, name="ami-kyc-purge").start()
    threading.Thread(target=ami_backup.backup_loop, daemon=True, name="ami-backup").start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
