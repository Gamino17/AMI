"""Waitlist · captación de leads desde la landing y desde reuniones con socios.

Diseño (v1, lo mínimo útil):

  - Sin tablas nuevas en SQLite. El bucket vive en `STATE["waitlist"]` y se
    persiste a través del save_state() que ami_api ya ejecuta al final de
    cada request. La forma es una lista de entries (no un dict por id) para
    poder iterar cronológicamente sin tener que ordenar.

  - El módulo NO importa ami_api para evitar el ciclo de imports (ami_api
    importa ami_waitlist al final). En su lugar, las funciones de
    persistencia toman el STATE por referencia perezosa vía `_get_state()`.
    En tests se puede inyectar un STATE alternativo con `_set_state_for_test`.

  - Anti-spam: rate-limit por (ip OR email) — máximo 3 entries por ventana
    de 1 hora. Es trivialmente bypaseable con varias IPs pero filtra el
    spam casero de un bot básico, que es la amenaza real en v1.

  - La validación es defensiva pero no agresiva: rechazamos lo claramente
    inválido (email sin '@', name > 100 chars, use_case fuera de allowlist)
    y dejamos pasar todo lo demás. Mejor un lead "imperfecto" que un lead
    perdido por un validador overzealous.

Schema de una entry (dict en STATE["waitlist"], lista ordenada por created_at):
    {
        "id":         "wl_xxx",         # ulid-ish, 12 hex chars
        "name":       str,              # 1-100, html escapado al renderizar
        "email":      str,              # lowercased, validado por regex básica
        "company":    str | None,       # 0-100
        "use_case":   str,              # en USE_CASES (allowlist)
        "context":    str | None,       # 0-500, free text
        "created_at": str,              # ISO-8601 UTC
        "ip":         str | None,       # IP del request (para anti-spam)
        "user_agent": str | None,       # UA del request
    }
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Constantes / validación
# ---------------------------------------------------------------------------

USE_CASES = (
    "agent_provisioning",   # "Building an AI agent that needs phone"
    "b2b_telco",            # "Telco partner interested"
    "just_curious",         # "Just curious"
    "other",                # "Other"
)

USE_CASE_LABELS_ES = {
    "agent_provisioning": "Estoy construyendo un agente AI que necesita teléfono",
    "b2b_telco":          "Soy un operador / partner telco interesado",
    "just_curious":       "Sólo curiosidad",
    "other":              "Otro",
}
USE_CASE_LABELS_EN = {
    "agent_provisioning": "Building an AI agent that needs phone",
    "b2b_telco":          "Telco partner interested",
    "just_curious":       "Just curious",
    "other":              "Other",
}

# Regex deliberadamente laxa: cualquier cosa con un '@' rodeado de tokens
# razonables y un '.' en el dominio. No validamos RFC 5322 (impráctico y
# rechaza emails legítimos como `user+tag@host.io`).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Detecta HTML/script en el name. Si match, lo rechazamos (el name aparece
# en el panel admin y queremos zero superficie de XSS aunque ya escapemos).
_HTML_RE = re.compile(r"[<>]")

MAX_NAME_LEN     = 100
MAX_COMPANY_LEN  = 100
MAX_CONTEXT_LEN  = 500
MAX_EMAIL_LEN    = 254  # límite RFC

# Anti-spam: máximo N entries de la misma IP O del mismo email en la ventana.
RATE_LIMIT_COUNT  = 3
RATE_LIMIT_WINDOW = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Acceso al STATE (perezoso, evita ciclo de imports con ami_api)
# ---------------------------------------------------------------------------

# Si en algún test se inyecta un STATE alternativo se almacena aquí; si no,
# se lee de ami_api.STATE on-demand.
_STATE_OVERRIDE: dict[str, Any] | None = None


def _get_state() -> dict[str, Any]:
    """Devuelve el STATE global (o el inyectado en tests).

    Carga ami_api perezosamente para evitar el ciclo de imports — ami_api
    importa ami_waitlist al final, y ami_waitlist no importa nada del API
    en top-level."""
    if _STATE_OVERRIDE is not None:
        return _STATE_OVERRIDE
    import ami_api  # noqa: PLC0415 — perezoso a propósito
    return ami_api.STATE


def _set_state_for_test(state: dict[str, Any] | None) -> None:
    """Solo para tests: inyecta un STATE alternativo. None para resetear."""
    global _STATE_OVERRIDE
    _STATE_OVERRIDE = state


def _waitlist_bucket() -> list[dict[str, Any]]:
    """Devuelve (creando si hace falta) la lista de entries en STATE."""
    st = _get_state()
    bucket = st.get("waitlist")
    if not isinstance(bucket, list):
        bucket = []
        st["waitlist"] = bucket
    return bucket


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return "wl_" + secrets.token_hex(6)


def _validate(name: str, email: str, company: str | None,
              use_case: str | None, context: str | None) -> tuple[bool, str]:
    """Validación de campos. Devuelve (ok, error_message_in_english).

    El error message es genérico — el frontend traduce."""
    if not isinstance(name, str) or not name.strip():
        return False, "name_required"
    if len(name) > MAX_NAME_LEN:
        return False, "name_too_long"
    if _HTML_RE.search(name):
        return False, "name_invalid"

    if not isinstance(email, str) or not email.strip():
        return False, "email_required"
    if len(email) > MAX_EMAIL_LEN:
        return False, "email_too_long"
    if not _EMAIL_RE.match(email.strip()):
        return False, "email_invalid"

    if company is not None and len(company) > MAX_COMPANY_LEN:
        return False, "company_too_long"

    if use_case not in USE_CASES:
        return False, "use_case_invalid"

    if context is not None and len(context) > MAX_CONTEXT_LEN:
        return False, "context_too_long"

    return True, ""


def _rate_limited(ip: str | None, email: str) -> bool:
    """True si esta IP o este email ya pasaron del límite en la ventana."""
    cutoff = datetime.now(timezone.utc) - RATE_LIMIT_WINDOW
    recent = 0
    email_lower = email.strip().lower()
    for entry in _waitlist_bucket():
        ts = entry.get("created_at", "")
        try:
            # Parse ISO con 'Z' al final
            entry_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if entry_dt < cutoff:
            continue
        same_ip = bool(ip) and entry.get("ip") == ip
        same_email = entry.get("email", "").lower() == email_lower
        if same_ip or same_email:
            recent += 1
            if recent >= RATE_LIMIT_COUNT:
                return True
    return False


# ---------------------------------------------------------------------------
# API pública del módulo
# ---------------------------------------------------------------------------

def submit_waitlist_entry(
    name: str,
    email: str,
    company: str | None,
    use_case: str | None,
    context: str | None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Valida y persiste la entry. Devuelve (status_code, response_dict).

    Status codes:
      201  → creada (response incluye id)
      400  → validación
      429  → rate limit (misma IP/email > 3 en 1 h)
    """
    # Normalizar a None las cadenas vacías opcionales para que el schema
    # sea coherente con lo que la regla "0-N chars" implica.
    company = (company or "").strip() or None
    context = (context or "").strip() or None
    if isinstance(name, str):
        name = name.strip()
    if isinstance(email, str):
        email = email.strip().lower()

    ok, err = _validate(name, email, company, use_case, context)
    if not ok:
        return 400, {"ok": False, "error": err}

    if _rate_limited(ip, email):
        return 429, {"ok": False, "error": "rate_limited",
                     "message": "Too many submissions from this address. Try again in an hour."}

    entry = {
        "id":         _new_id(),
        "name":       name,
        "email":      email,
        "company":    company,
        "use_case":   use_case,
        "context":    context,
        "created_at": _now_iso(),
        "ip":         ip,
        "user_agent": user_agent,
    }
    _waitlist_bucket().append(entry)
    return 201, {"ok": True, "id": entry["id"]}


def list_waitlist_entries() -> list[dict[str, Any]]:
    """Devuelve una copia (shallow) de todas las entries, más reciente primero."""
    return list(reversed(_waitlist_bucket()))


# ---------------------------------------------------------------------------
# Render — página standalone /waitlist
# ---------------------------------------------------------------------------

def _waitlist_css() -> str:
    """Paleta dark ambient con halos violeta. Aislada bajo .wl-* para no
    chocar con CSS de otras páginas si en algún punto se embebe."""
    return """
      :root {
        --wl-bg:        #06060a;
        --wl-bg-soft:   #0c0c14;
        --wl-line:      rgba(255,255,255,0.08);
        --wl-line-strong: rgba(255,255,255,0.14);
        --wl-ink:       #ededf2;
        --wl-ink-soft:  #b8b8c8;
        --wl-ink-mute:  #8888a0;
        --wl-violet:    #8b6cff;
        --wl-cyan:      #5fd4ff;
        --wl-err:       #ff6c8b;
        --wl-ok:        #6cffb4;
        --wl-mono: "JetBrains Mono", "SF Mono", monospace;
        --wl-sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
      }
      html, body {
        background: var(--wl-bg); color: var(--wl-ink);
        font-family: var(--wl-sans);
        margin: 0; padding: 0;
        -webkit-font-smoothing: antialiased;
      }
      a { color: var(--wl-violet); text-decoration: none; }
      a:hover { color: var(--wl-cyan); }

      .wl-page {
        min-height: 100vh;
        display: flex; flex-direction: column;
        position: relative; overflow-x: hidden;
      }
      .wl-page::before {
        content: ""; position: absolute; pointer-events: none;
        top: -200px; left: 50%; transform: translateX(-50%);
        width: 900px; height: 600px;
        background: radial-gradient(ellipse, var(--wl-violet) 0%, transparent 65%);
        filter: blur(120px); opacity: 0.22; z-index: 0;
      }
      .wl-page::after {
        content: ""; position: absolute; pointer-events: none;
        bottom: -250px; right: -150px;
        width: 700px; height: 500px;
        background: radial-gradient(ellipse, var(--wl-cyan) 0%, transparent 65%);
        filter: blur(140px); opacity: 0.12; z-index: 0;
      }

      .wl-main {
        position: relative; z-index: 1;
        flex: 1; max-width: 480px; width: 100%;
        margin: 0 auto; padding: 5rem 1.5rem 4rem;
        text-align: center;
      }
      .wl-brand {
        display: inline-block;
        font-family: var(--wl-mono); font-weight: 700;
        font-size: 1rem; letter-spacing: 0.02em;
        color: var(--wl-ink); text-decoration: none;
        margin-bottom: 3rem;
      }
      .wl-brand .dot { color: var(--wl-violet); }

      .wl-pill {
        display: inline-flex; align-items: center; gap: 0.5rem;
        padding: 0.35rem 0.85rem; border-radius: 99px;
        background: rgba(139, 108, 255, 0.10);
        border: 1px solid rgba(139, 108, 255, 0.25);
        font-family: var(--wl-mono); font-size: 0.72rem;
        color: var(--wl-violet);
        margin-bottom: 1.5rem;
      }
      .wl-pill .live {
        width: 6px; height: 6px; border-radius: 50%;
        background: var(--wl-violet);
        box-shadow: 0 0 8px var(--wl-violet);
      }

      .wl-h1 {
        font-size: clamp(1.8rem, 5vw, 2.6rem);
        font-weight: 800; letter-spacing: -0.02em;
        line-height: 1.15; margin: 0 0 1rem;
        background: linear-gradient(180deg, #ffffff 20%, #a8a8c8 130%);
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent;
      }
      .wl-lead {
        color: var(--wl-ink-soft); font-size: 1rem;
        line-height: 1.55; margin: 0 0 2.5rem;
      }

      .wl-form {
        text-align: left;
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--wl-line);
        border-radius: 14px;
        padding: 2rem 1.75rem;
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
      }
      .wl-row { margin-bottom: 1.1rem; }
      .wl-label {
        display: block;
        font-family: var(--wl-mono); font-size: 0.7rem;
        text-transform: uppercase; letter-spacing: 0.14em;
        color: var(--wl-ink-mute);
        margin-bottom: 0.4rem;
      }
      .wl-label .req { color: var(--wl-violet); margin-left: 2px; }
      .wl-label .opt { color: var(--wl-ink-mute); font-weight: 400;
                       text-transform: none; letter-spacing: 0;
                       font-size: 0.65rem; margin-left: 0.4rem; }

      .wl-input, .wl-select, .wl-textarea {
        width: 100%; box-sizing: border-box;
        background: rgba(0,0,0,0.35);
        border: 1px solid var(--wl-line-strong);
        border-radius: 8px;
        padding: 0.7rem 0.85rem;
        color: var(--wl-ink); font-family: var(--wl-sans);
        font-size: 0.92rem;
        transition: border-color 120ms, box-shadow 120ms;
      }
      .wl-input:focus, .wl-select:focus, .wl-textarea:focus {
        outline: none;
        border-color: var(--wl-violet);
        box-shadow: 0 0 0 3px rgba(139, 108, 255, 0.18);
      }
      .wl-textarea { resize: vertical; min-height: 84px; }
      .wl-select {
        appearance: none; -webkit-appearance: none;
        background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'><path fill='%238888a0' d='M6 8L0 0h12z'/></svg>");
        background-repeat: no-repeat;
        background-position: right 0.85rem center;
        padding-right: 2.2rem;
      }
      .wl-select option { background: #0c0c14; color: var(--wl-ink); }

      .wl-hint {
        font-family: var(--wl-mono); font-size: 0.66rem;
        color: var(--wl-ink-mute); margin-top: 0.3rem;
      }

      .wl-btn {
        width: 100%;
        display: inline-flex; align-items: center; justify-content: center;
        gap: 0.5rem;
        padding: 0.95rem 1.4rem;
        border: none; border-radius: 10px;
        background: linear-gradient(180deg, #9d7cff 0%, #7b5cff 100%);
        color: #ffffff; font-family: var(--wl-mono);
        font-size: 0.86rem; font-weight: 600; letter-spacing: 0.01em;
        cursor: pointer; margin-top: 0.4rem;
        box-shadow: 0 8px 24px rgba(139, 108, 255, 0.30);
        transition: transform 80ms, box-shadow 120ms, opacity 120ms;
      }
      .wl-btn:hover { transform: translateY(-1px);
                       box-shadow: 0 12px 32px rgba(139, 108, 255, 0.40); }
      .wl-btn:active { transform: translateY(0); }
      .wl-btn[disabled] { opacity: 0.55; cursor: progress; }

      .wl-spinner {
        width: 14px; height: 14px; border-radius: 50%;
        border: 2px solid rgba(255,255,255,0.35);
        border-top-color: #ffffff;
        animation: wl-spin 0.7s linear infinite;
        display: none;
      }
      .wl-btn.is-loading .wl-spinner { display: inline-block; }
      @keyframes wl-spin { to { transform: rotate(360deg); } }

      .wl-error {
        display: none;
        margin-top: 1rem;
        padding: 0.7rem 0.9rem;
        background: rgba(255, 108, 139, 0.10);
        border: 1px solid rgba(255, 108, 139, 0.30);
        border-radius: 8px;
        color: var(--wl-err); font-size: 0.85rem;
        font-family: var(--wl-mono);
      }
      .wl-error.is-visible { display: block; }

      .wl-success {
        display: none;
        text-align: center;
        padding: 2.5rem 1.75rem;
        background: rgba(108, 255, 180, 0.06);
        border: 1px solid rgba(108, 255, 180, 0.25);
        border-radius: 14px;
      }
      .wl-success.is-visible { display: block; }
      .wl-success .check {
        font-size: 2.5rem; line-height: 1; color: var(--wl-ok);
        margin-bottom: 0.8rem;
      }
      .wl-success h3 {
        margin: 0 0 0.5rem;
        font-size: 1.15rem; color: var(--wl-ink);
      }
      .wl-success p {
        margin: 0 0 1rem; color: var(--wl-ink-soft);
        font-size: 0.92rem; line-height: 1.5;
      }
      .wl-success .next {
        font-family: var(--wl-mono); font-size: 0.78rem;
        color: var(--wl-ink-mute);
      }
      .wl-success .next a {
        color: var(--wl-cyan); border-bottom: 1px dashed currentColor;
      }

      .wl-footer {
        position: relative; z-index: 1;
        text-align: center; padding: 1.5rem;
        color: var(--wl-ink-mute);
        font-family: var(--wl-mono); font-size: 0.7rem;
      }
      .wl-footer a { color: var(--wl-ink-mute); }
      .wl-footer a:hover { color: var(--wl-ink); }

      /* Lang toggle (mismo patrón que el resto del site) */
      html[lang="es"] [data-wl-lang="en"] { display: none; }
      html[lang="en"] [data-wl-lang="es"] { display: none; }

      /* COMPACT embed (en landing, antes del final-cta) ------------------ */
      .wl-embed {
        max-width: 560px; margin: 2.5rem auto 0;
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--wl-line);
        border-radius: 14px;
        padding: 1.5rem 1.4rem;
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
      }
      .wl-embed h3 {
        margin: 0 0 1rem;
        font-size: 1rem; font-family: var(--wl-mono);
        color: var(--wl-ink); text-align: center;
        letter-spacing: 0.02em;
      }
      .wl-embed .wl-row { margin-bottom: 0.75rem; }
      .wl-embed .wl-btn { padding: 0.78rem 1.2rem; }
      .wl-embed .wl-success { padding: 1.5rem 1rem; }
      .wl-embed .wl-success .check { font-size: 1.8rem; margin-bottom: 0.4rem; }
      .wl-embed .wl-success h3 { font-size: 0.95rem; margin: 0 0 0.3rem; }
      .wl-embed .wl-success p { font-size: 0.82rem; margin: 0; }
    """


def _waitlist_js(form_id: str, success_id: str, error_id: str,
                 lang: str, redirect_on_success: bool = False) -> str:
    """JS de envío: intercepta el submit, hace fetch a /v1/waitlist, muestra
    estados visuales. Sin frameworks."""
    return (
        "(function() {\n"
        f"  var form    = document.getElementById('{form_id}');\n"
        f"  var success = document.getElementById('{success_id}');\n"
        f"  var error   = document.getElementById('{error_id}');\n"
        "  if (!form) return;\n"
        "  var btn = form.querySelector('.wl-btn');\n"
        "  var btnText = btn.querySelector('.wl-btn-text');\n"
        "  var origText = btnText ? btnText.textContent : '';\n"
        "\n"
        "  function showError(msg) {\n"
        "    error.textContent = msg;\n"
        "    error.classList.add('is-visible');\n"
        "  }\n"
        "  function clearError() { error.classList.remove('is-visible'); }\n"
        "\n"
        "  var ERRORS = {\n"
        "    es: {\n"
        "      name_required: 'Falta el nombre.',\n"
        "      name_too_long: 'El nombre es demasiado largo.',\n"
        "      name_invalid:  'El nombre contiene caracteres no permitidos.',\n"
        "      email_required:'Falta el email.',\n"
        "      email_invalid: 'El email no parece válido.',\n"
        "      email_too_long:'El email es demasiado largo.',\n"
        "      company_too_long: 'La empresa es demasiado larga.',\n"
        "      use_case_invalid: 'Selecciona una opción de uso.',\n"
        "      context_too_long: 'El contexto es demasiado largo (máx 500).',\n"
        "      rate_limited:  'Demasiados envíos. Vuelve a intentarlo en una hora.',\n"
        "      network:       'Error de red. Inténtalo de nuevo.',\n"
        "      generic:       'Algo ha ido mal. Inténtalo de nuevo.'\n"
        "    },\n"
        "    en: {\n"
        "      name_required: 'Name is required.',\n"
        "      name_too_long: 'Name is too long.',\n"
        "      name_invalid:  'Name contains disallowed characters.',\n"
        "      email_required:'Email is required.',\n"
        "      email_invalid: \"Email doesn't look valid.\",\n"
        "      email_too_long:'Email is too long.',\n"
        "      company_too_long: 'Company is too long.',\n"
        "      use_case_invalid: 'Please pick a use case.',\n"
        "      context_too_long: 'Context is too long (max 500).',\n"
        "      rate_limited:  'Too many submissions. Try again in an hour.',\n"
        "      network:       'Network error. Please try again.',\n"
        "      generic:       'Something went wrong. Please try again.'\n"
        "    }\n"
        "  };\n"
        f"  var L = (document.documentElement.lang === 'en') ? ERRORS.en : ERRORS.{lang};\n"
        "\n"
        "  form.addEventListener('submit', function(ev) {\n"
        "    ev.preventDefault();\n"
        "    clearError();\n"
        "    btn.disabled = true; btn.classList.add('is-loading');\n"
        "    if (btnText) btnText.textContent = (document.documentElement.lang === 'en') ? 'Sending…' : 'Enviando…';\n"
        "\n"
        "    var data = new FormData(form);\n"
        "    var payload = {\n"
        "      name:     data.get('name')     || '',\n"
        "      email:    data.get('email')    || '',\n"
        "      company:  data.get('company')  || '',\n"
        "      use_case: data.get('use_case') || '',\n"
        "      context:  data.get('context')  || ''\n"
        "    };\n"
        "\n"
        "    fetch('/v1/waitlist', {\n"
        "      method: 'POST',\n"
        "      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },\n"
        "      body: JSON.stringify(payload)\n"
        "    }).then(function(r) {\n"
        "      return r.json().then(function(j) { return { status: r.status, body: j }; });\n"
        "    }).then(function(res) {\n"
        "      if (res.status === 201 && res.body.ok) {\n"
        "        form.style.display = 'none';\n"
        "        success.classList.add('is-visible');\n"
        # Backslash NO permitido en una expresión f-string en Python 3.11,
        # así que precomputamos el snippet con concatenación normal.
        + ('        window.location.hash = "#joined";\n' if redirect_on_success else "")
        +
        "      } else {\n"
        "        var code = (res.body && res.body.error) || 'generic';\n"
        "        showError(L[code] || L.generic);\n"
        "      }\n"
        "    }).catch(function() {\n"
        "      showError(L.network);\n"
        "    }).finally(function() {\n"
        "      btn.disabled = false; btn.classList.remove('is-loading');\n"
        "      if (btnText) btnText.textContent = origText;\n"
        "    });\n"
        "  });\n"
        "})();\n"
    )


def _options_html(selected: str = "") -> str:
    """Options del select de use_case, bilingüe inline (data-wl-lang)."""
    placeholder = (
        '<option value="" disabled' + (' selected' if not selected else '') + '>'
        '<span data-wl-lang="es">Selecciona…</span>'
        '<span data-wl-lang="en">Select…</span>'
        '</option>'
    )
    # Nota: los <option> no renderizan <span> hijos en la mayoría de
    # browsers; usamos texto plano y dejamos que el JS de lang toggle
    # reescriba los labels al cambiar idioma.
    options = [placeholder.replace("<span data-wl-lang=\"es\">Selecciona…</span><span data-wl-lang=\"en\">Select…</span>",
                                    "Selecciona… / Select…")]
    for key in USE_CASES:
        es = _html_escape(USE_CASE_LABELS_ES[key])
        en = _html_escape(USE_CASE_LABELS_EN[key])
        sel = ' selected' if selected == key else ''
        options.append(
            f'<option value="{key}"{sel} data-es="{es}" data-en="{en}">{es} / {en}</option>'
        )
    return "\n".join(options)


def _lang_toggle_js() -> str:
    """Toggle ES/EN con persistencia en localStorage. Reescribe los labels
    del select de use_case en cada cambio para que reflejen el idioma actual
    (las opciones traen data-es / data-en)."""
    return """
      (function() {
        try {
          var l = localStorage.getItem('ami-lang');
          if (l === 'es' || l === 'en') document.documentElement.lang = l;
        } catch(e) {}

        function applySelectLabels() {
          var lang = document.documentElement.lang || 'es';
          document.querySelectorAll('.wl-select option[data-es]').forEach(function(opt) {
            var label = opt.getAttribute(lang === 'en' ? 'data-en' : 'data-es');
            if (label) opt.textContent = label;
          });
        }
        applySelectLabels();

        var btn = document.getElementById('wl-langtoggle');
        function sync() {
          if (!btn) return;
          var cur = document.documentElement.lang || 'es';
          btn.textContent = cur === 'es' ? 'EN' : 'ES';
        }
        if (btn) {
          btn.addEventListener('click', function() {
            var next = (document.documentElement.lang === 'es') ? 'en' : 'es';
            try { localStorage.setItem('ami-lang', next); } catch(e) {}
            document.cookie = 'ami-lang=' + next + '; path=/; max-age=' + (60*60*24*365);
            document.documentElement.lang = next;
            sync();
            applySelectLabels();
          });
          sync();
        }
      })();
    """


def render_waitlist_page(lang: str = "es") -> str:
    """Página standalone /waitlist con form grande centrado.

    Self-contained: no usa el chrome común (header/footer del resto del
    site) porque la página vive en un flujo dedicado de captación — menos
    nav, más foco en el form."""
    if lang not in ("es", "en"):
        lang = "es"

    form = """
      <form class="wl-form" id="wl-form" novalidate>
        <div class="wl-row">
          <label class="wl-label" for="wl-name">
            <span data-wl-lang="es">Nombre</span><span data-wl-lang="en">Name</span>
            <span class="req">*</span>
          </label>
          <input class="wl-input" id="wl-name" name="name" type="text"
                 maxlength="100" autocomplete="name" required>
        </div>

        <div class="wl-row">
          <label class="wl-label" for="wl-email">
            <span data-wl-lang="es">Email</span><span data-wl-lang="en">Email</span>
            <span class="req">*</span>
          </label>
          <input class="wl-input" id="wl-email" name="email" type="email"
                 maxlength="254" autocomplete="email" required>
        </div>

        <div class="wl-row">
          <label class="wl-label" for="wl-company">
            <span data-wl-lang="es">Empresa</span><span data-wl-lang="en">Company</span>
            <span class="opt">
              <span data-wl-lang="es">opcional</span><span data-wl-lang="en">optional</span>
            </span>
          </label>
          <input class="wl-input" id="wl-company" name="company" type="text"
                 maxlength="100" autocomplete="organization">
        </div>

        <div class="wl-row">
          <label class="wl-label" for="wl-usecase">
            <span data-wl-lang="es">¿Para qué lo quieres?</span>
            <span data-wl-lang="en">What's the use case?</span>
            <span class="req">*</span>
          </label>
          <select class="wl-select" id="wl-usecase" name="use_case" required>
            """ + _options_html() + """
          </select>
        </div>

        <div class="wl-row">
          <label class="wl-label" for="wl-context">
            <span data-wl-lang="es">Cuéntanos más</span>
            <span data-wl-lang="en">Tell us more</span>
            <span class="opt">
              <span data-wl-lang="es">opcional · 500 caracteres</span>
              <span data-wl-lang="en">optional · 500 chars</span>
            </span>
          </label>
          <textarea class="wl-textarea" id="wl-context" name="context"
                    maxlength="500" rows="3"></textarea>
        </div>

        <button class="wl-btn" type="submit">
          <span class="wl-spinner" aria-hidden="true"></span>
          <span class="wl-btn-text">
            <span data-wl-lang="es">Apúntame a la waitlist</span>
            <span data-wl-lang="en">Join the waitlist</span>
          </span>
        </button>

        <div class="wl-error" id="wl-error" role="alert" aria-live="polite"></div>
      </form>

      <div class="wl-success" id="wl-success" role="status" aria-live="polite">
        <div class="check">&#10003;</div>
        <h3>
          <span data-wl-lang="es">Gracias, te escribimos en menos de 48 h.</span>
          <span data-wl-lang="en">Thanks, we'll be in touch within 48h.</span>
        </h3>
        <p>
          <span data-wl-lang="es">Hemos recibido tu solicitud. Te contactaremos al email que nos has dado.</span>
          <span data-wl-lang="en">We got your request. We'll reach out to the email you provided.</span>
        </p>
        <div class="next">
          <span data-wl-lang="es">¿Quieres empezar ya? </span>
          <span data-wl-lang="en">Want to start now? </span>
          <a href="/docs">
            <span data-wl-lang="es">Salta a /docs e integra</span>
            <span data-wl-lang="en">Jump to /docs and integrate</span>
          </a>
        </div>
      </div>
    """

    js_submit = _waitlist_js("wl-form", "wl-success", "wl-error", lang)
    js_lang   = _lang_toggle_js()

    title_es = "Únete a la waitlist de AMI"
    title_en = "Join the AMI waitlist"

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_es} · {title_en}</title>
<meta name="description" content="Sign up for early access to AMI — the protocol layer for AI agents to provision their own mobile identity.">
<meta name="robots" content="index, follow">
<meta property="og:title" content="{title_en}">
<meta property="og:description" content="Sign up for early access to AMI.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_waitlist_css()}</style>
</head>
<body>
  <div class="wl-page">
    <main class="wl-main">
      <a href="/" class="wl-brand">AMI<span class="dot">.</span></a>

      <div style="display:flex; justify-content:flex-end; margin-top:-2rem; margin-bottom:1.5rem;">
        <button id="wl-langtoggle" type="button" aria-label="Toggle language"
                style="background:none; border:1px solid rgba(255,255,255,0.10); color:#8888a0; cursor:pointer; font-family:var(--wl-mono); font-size:0.7rem; padding:0.3rem 0.65rem; border-radius:99px;">EN</button>
      </div>

      <span class="wl-pill">
        <span class="live"></span>
        <span data-wl-lang="es">v1.0 · early access</span>
        <span data-wl-lang="en">v1.0 · early access</span>
      </span>

      <h1 class="wl-h1">
        <span data-wl-lang="es">{title_es}</span>
        <span data-wl-lang="en">{title_en}</span>
      </h1>

      <p class="wl-lead">
        <span data-wl-lang="es">El protocolo abierto para que tu agente AI obtenga su propio número, SMS y voz — sin hacks, sin pasar por flujos pensados para humanos.</span>
        <span data-wl-lang="en">The open protocol for your AI agent to get its own number, SMS and voice — no hacks, no human-only flows.</span>
      </p>

      {form}
    </main>

    <footer class="wl-footer">
      <span data-wl-lang="es">Parallax IEI · AMI v1.0 ·</span>
      <span data-wl-lang="en">Parallax IEI · AMI v1.0 ·</span>
      <a href="/">
        <span data-wl-lang="es">Volver al inicio</span>
        <span data-wl-lang="en">Back to home</span>
      </a>
      <span> · </span>
      <a href="/docs">/docs</a>
      <span> · </span>
      <a href="/spec">/spec</a>
    </footer>
  </div>

  <script>{js_lang}</script>
  <script>{js_submit}</script>
</body>
</html>
"""


def render_landing_signup_form(lang: str = "es") -> str:
    """Versión compacta del form, pensada para embeberla en la landing
    (antes del FINAL CTA). Devuelve un fragmento HTML — el caller decide
    dónde meterlo. Incluye su propio <style> y <script> autocontenido para
    que pueda inyectarse sin tocar nada más.

    Sólo 3 campos visibles: name, email, use_case. El backend acepta los
    otros 2 como vacíos (company, context)."""
    if lang not in ("es", "en"):
        lang = "es"

    js_submit = _waitlist_js("wl-embed-form", "wl-embed-success", "wl-embed-error", lang)

    return f"""
<style>{_waitlist_css()}</style>
<div class="wl-embed" id="ami-waitlist-embed">
  <h3>
    <span data-wl-lang="es">Únete a la waitlist</span>
    <span data-wl-lang="en">Join the waitlist</span>
  </h3>

  <form id="wl-embed-form" novalidate>
    <div class="wl-row">
      <label class="wl-label" for="wl-embed-name">
        <span data-wl-lang="es">Nombre</span><span data-wl-lang="en">Name</span>
        <span class="req">*</span>
      </label>
      <input class="wl-input" id="wl-embed-name" name="name" type="text"
             maxlength="100" autocomplete="name" required>
    </div>

    <div class="wl-row">
      <label class="wl-label" for="wl-embed-email">
        Email <span class="req">*</span>
      </label>
      <input class="wl-input" id="wl-embed-email" name="email" type="email"
             maxlength="254" autocomplete="email" required>
    </div>

    <div class="wl-row">
      <label class="wl-label" for="wl-embed-usecase">
        <span data-wl-lang="es">¿Para qué lo quieres?</span>
        <span data-wl-lang="en">Use case</span>
        <span class="req">*</span>
      </label>
      <select class="wl-select" id="wl-embed-usecase" name="use_case" required>
        {_options_html()}
      </select>
    </div>

    <button class="wl-btn" type="submit">
      <span class="wl-spinner" aria-hidden="true"></span>
      <span class="wl-btn-text">
        <span data-wl-lang="es">Apuntarme</span>
        <span data-wl-lang="en">Sign me up</span>
      </span>
    </button>

    <div class="wl-error" id="wl-embed-error" role="alert" aria-live="polite"></div>
  </form>

  <div class="wl-success" id="wl-embed-success" role="status" aria-live="polite">
    <div class="check">&#10003;</div>
    <h3>
      <span data-wl-lang="es">Gracias. Te escribimos en menos de 48 h.</span>
      <span data-wl-lang="en">Thanks. We'll be in touch within 48h.</span>
    </h3>
    <p>
      <span data-wl-lang="es">Mientras tanto, <a href="/docs">salta a /docs</a> para empezar a integrar.</span>
      <span data-wl-lang="en">Meanwhile, <a href="/docs">jump to /docs</a> to start integrating.</span>
    </p>
  </div>
</div>
<script>
  (function() {{
    // Sincroniza labels del select con el idioma actual (mismo patrón
    // que la página standalone).
    function applyLabels() {{
      var lang = document.documentElement.lang || 'es';
      document.querySelectorAll('#ami-waitlist-embed .wl-select option[data-es]').forEach(function(opt) {{
        var label = opt.getAttribute(lang === 'en' ? 'data-en' : 'data-es');
        if (label) opt.textContent = label;
      }});
    }}
    applyLabels();
    // Re-aplica cuando el toggle global del site cambia el lang.
    var obs = new MutationObserver(applyLabels);
    obs.observe(document.documentElement, {{ attributes: true, attributeFilter: ['lang'] }});
  }})();
</script>
<script>{js_submit}</script>
"""
