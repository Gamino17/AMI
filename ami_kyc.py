"""Verificación de identidad humana (KYC) del firmante.

Punto crítico del flujo de contratación: tras que el agente AI envíe los
datos del cliente legal (legal_name, NIF, billing_email, representative_name),
hay que verificar que **el humano detrás existe y es quien dice ser**.

Flujo:

   agente AI                AMI backend           Humano (rep legal)
       │                         │                       │
       │ POST .../customer-data  │                       │
       ├────────────────────────►│                       │
       │                         │ POST .../kyc/initiate │
       ├────────────────────────►│                       │
       │                         │ ──► genera token único│
       │                         │ ──► email al humano   │
       │                         │     con /kyc/{token}  │
       │                         │                       │
       │                         │                       │ abre URL en su móvil
       │                         │                       │ sube DNI + selfie
       │                         │      POST /kyc/{token}/submit
       │                         │◄──────────────────────┤
       │                         │                       │
       │                         │ revisor humano        │
       │                         │ verifica las fotos    │
       │                         │ → verified | rejected │
       │                         │                       │
       │ webhook kyc.verified    │                       │
       │◄────────────────────────┤                       │
       │                         │                       │
       │ ahora puede generar contrato                    │

Bucket en STATE["kyc_verifications"]:

    {
      "id": "kyc_xxx",
      "token": "<64 hex>",          # capability secret de la URL pública
      "sim_request_id": "simreq_xxx",
      "customer_id": "customer_xxx",
      "account_id": "acct_xxx",
      "rep_email": "ada@acme.test",
      "status": "pending" | "submitted" | "in_review" | "verified" | "rejected",
      "rejection_reason": str | None,
      "dni_front_b64": str | None,   # imagen JPEG/PNG base64 (cap a 4 MB)
      "dni_back_b64":  str | None,
      "selfie_b64":    str | None,
      "created_at": ISO,
      "submitted_at": ISO | None,
      "verified_at": ISO | None,
      "reviewer": str | None,         # nombre del operador que revisó
    }

Para v1 la verificación es manual: un operador nuestro abre
/v1/admin/kyc, ve las imágenes y aprueba o rechaza. v2 se puede integrar
con un proveedor KYC externo (sin nombrar marcas).
"""
from __future__ import annotations
import secrets


# Tamaño máximo de cada imagen (base64). 4 MB → ~5.4 MB en base64.
MAX_IMAGE_B64_BYTES = 5_500_000

ALLOWED_STATUSES = {"pending", "submitted", "in_review", "verified", "rejected"}


def new_verification(sim_request_id: str, customer_id: str,
                      account_id: str | None, rep_email: str) -> dict:
    """Construye un KYC verification record. No lo persiste — el caller decide."""
    from ami_api import new_id, now
    return {
        "id": new_id("kyc"),
        "token": secrets.token_urlsafe(32),
        "sim_request_id": sim_request_id,
        "customer_id": customer_id,
        "account_id": account_id,
        "rep_email": rep_email,
        "status": "pending",
        "rejection_reason": None,
        "dni_front_b64": None,
        "dni_back_b64": None,
        "selfie_b64": None,
        "created_at": now(),
        "submitted_at": None,
        "verified_at": None,
        "reviewer": None,
    }


def kyc_summary(kyc: dict, include_images: bool = False) -> dict:
    """Vista pública sin imágenes pesadas (a menos que se pidan explícitamente)."""
    out = {
        "id": kyc["id"],
        "sim_request_id": kyc.get("sim_request_id"),
        "customer_id": kyc.get("customer_id"),
        "account_id": kyc.get("account_id"),
        "rep_email": kyc.get("rep_email"),
        "status": kyc.get("status"),
        "rejection_reason": kyc.get("rejection_reason"),
        "created_at": kyc.get("created_at"),
        "submitted_at": kyc.get("submitted_at"),
        "verified_at": kyc.get("verified_at"),
        "reviewer": kyc.get("reviewer"),
        "has_dni_front": bool(kyc.get("dni_front_b64")),
        "has_dni_back":  bool(kyc.get("dni_back_b64")),
        "has_selfie":    bool(kyc.get("selfie_b64")),
    }
    if include_images:
        out["dni_front_b64"] = kyc.get("dni_front_b64")
        out["dni_back_b64"]  = kyc.get("dni_back_b64")
        out["selfie_b64"]    = kyc.get("selfie_b64")
    return out


def submit_images(kyc: dict, dni_front_b64: str | None,
                   dni_back_b64: str | None,
                   selfie_b64: str | None) -> tuple[bool, str | None]:
    """Valida y guarda las imágenes. Devuelve (ok, reason)."""
    from ami_api import now
    if not dni_front_b64:
        return False, "dni_front_required"
    # Validación de tamaño (base64 raw cap)
    for label, img in (("dni_front", dni_front_b64),
                       ("dni_back", dni_back_b64),
                       ("selfie", selfie_b64)):
        if img and len(img) > MAX_IMAGE_B64_BYTES:
            return False, f"{label}_too_large"
        if img and not _looks_like_b64(img):
            return False, f"{label}_invalid_format"
    kyc["dni_front_b64"] = dni_front_b64
    kyc["dni_back_b64"]  = dni_back_b64
    kyc["selfie_b64"]    = selfie_b64
    kyc["status"] = "submitted"
    kyc["submitted_at"] = now()
    return True, None


def _looks_like_b64(s: str) -> bool:
    """Heurística: que sea base64 de imagen (data:image/...;base64,...) o raw."""
    if s.startswith("data:image/"):
        return ";base64," in s and len(s) > 100
    # raw base64: caracteres válidos
    try:
        import base64
        base64.b64decode(s[:200], validate=True)
        return len(s) > 100
    except Exception:
        return False


# ====================== HTML render ======================

_KYC_CSS = """
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
    --red: #ff6b8a;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans); color: var(--ink); background: var(--bg);
    background: radial-gradient(ellipse 80% 50% at 50% 0%, rgba(139,108,255,0.10), transparent 70%), var(--bg);
    background-attachment: fixed;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    display: flex; align-items: flex-start; justify-content: center;
    padding: 2rem 1rem;
  }
  .kyc-wrap {
    width: 100%; max-width: 540px;
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 2rem 1.8rem;
  }
  .kyc-eyebrow {
    font-family: var(--mono); font-size: 0.7rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em;
    margin-bottom: 0.7rem;
  }
  h1 { font-size: 1.7rem; font-weight: 700; letter-spacing: -0.02em;
       margin: 0 0 0.6rem; line-height: 1.15; }
  .kyc-sub {
    color: var(--ink-soft); font-size: 0.95rem; margin: 0 0 1.6rem;
    line-height: 1.5;
  }
  .kyc-customer {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 8px; padding: 0.85rem 1rem;
    margin-bottom: 1.4rem;
    font-family: var(--mono); font-size: 0.82rem;
  }
  .kyc-customer .k { color: var(--ink-mute); }
  .kyc-customer .v { color: var(--ink); font-weight: 500; }
  .kyc-customer .row { margin-bottom: 0.3rem; }
  .kyc-customer .row:last-child { margin-bottom: 0; }
  .kyc-field { margin-bottom: 1.1rem; }
  .kyc-field label {
    display: block; font-family: var(--mono); font-size: 0.7rem;
    text-transform: uppercase; letter-spacing: 0.12em;
    color: var(--ink-mute); margin-bottom: 0.5rem;
  }
  .kyc-field label .req { color: var(--accent); }
  .kyc-dropzone {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center;
    border: 2px dashed var(--line); border-radius: 10px;
    padding: 1.5rem 1rem; cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    text-align: center;
  }
  .kyc-dropzone:hover {
    border-color: var(--accent); background: rgba(139,108,255,0.04);
  }
  .kyc-dropzone.has-file {
    border-color: var(--green); background: rgba(74,222,128,0.06);
  }
  .kyc-dropzone .icon { font-size: 1.6rem; margin-bottom: 0.4rem; }
  .kyc-dropzone .text {
    font-family: var(--mono); font-size: 0.78rem; color: var(--ink-soft);
  }
  .kyc-dropzone .filename {
    font-family: var(--mono); font-size: 0.78rem; color: var(--green);
    margin-top: 0.3rem;
  }
  .kyc-preview {
    margin-top: 0.5rem;
    max-width: 100%; max-height: 140px;
    border-radius: 6px; border: 1px solid var(--line);
    display: none;
  }
  .kyc-preview.show { display: block; }
  input[type="file"] { display: none; }
  .kyc-btn {
    width: 100%; padding: 0.9rem; margin-top: 1rem;
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; border: 0; cursor: pointer;
    border-radius: 8px; font-size: 0.95rem; font-weight: 600;
    box-shadow: 0 8px 24px -8px rgba(123,92,255,0.5);
  }
  .kyc-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .kyc-err {
    background: rgba(255,107,138,0.10);
    border: 1px solid rgba(255,107,138,0.3);
    color: var(--red); border-radius: 6px;
    padding: 0.6rem 0.8rem; margin-bottom: 1rem;
    font-family: var(--mono); font-size: 0.82rem;
    display: none;
  }
  .kyc-err.show { display: block; }
  .kyc-state-pill {
    display: inline-block; font-family: var(--mono); font-size: 0.7rem;
    padding: 0.2rem 0.6rem; border-radius: 99px;
    text-transform: uppercase; letter-spacing: 0.1em;
  }
  .kyc-state-pill.pending { color: var(--amber); background: rgba(251,191,36,0.10); }
  .kyc-state-pill.verified { color: var(--green); background: rgba(74,222,128,0.10); }
  .kyc-state-pill.rejected { color: var(--red); background: rgba(255,107,138,0.10); }
  .kyc-success {
    text-align: center; padding: 2rem 1rem;
  }
  .kyc-success h2 { color: var(--green); margin-top: 0; }
  .legal {
    font-size: 0.78rem; color: var(--ink-mute);
    text-align: center; margin-top: 1.5rem;
    line-height: 1.5;
  }
"""


def render_kyc_form(kyc: dict, customer: dict | None) -> str:
    """Renderiza el formulario público para subir DNI + selfie."""
    import html as _html
    legal_name = _html.escape((customer or {}).get("legal_name", "—"))
    tax_id     = _html.escape((customer or {}).get("tax_id", "—"))
    rep_name   = _html.escape((customer or {}).get("representative_name", "—"))
    rep_email  = _html.escape(kyc.get("rep_email", "—"))
    token      = _html.escape(kyc.get("token", ""))

    # Estado: si ya está submitted/verified/rejected, mostrar mensaje
    status = kyc.get("status", "pending")
    if status in ("submitted", "in_review"):
        body = f"""
          <div class="kyc-success">
            <div class="kyc-state-pill pending">recibido</div>
            <h2>Gracias, lo tenemos.</h2>
            <p style="color: var(--ink-soft);">Estamos revisando tu documentación. Te avisaremos por email
            cuando esté verificado (normalmente en menos de 24h).</p>
          </div>
        """
    elif status == "verified":
        body = f"""
          <div class="kyc-success">
            <div class="kyc-state-pill verified">verificado</div>
            <h2>✓ Identidad verificada</h2>
            <p style="color: var(--ink-soft);">Todo listo. El contrato del número está listo para firma.</p>
          </div>
        """
    elif status == "rejected":
        reason = _html.escape(kyc.get("rejection_reason") or "razón no especificada")
        body = f"""
          <div class="kyc-success">
            <div class="kyc-state-pill rejected">rechazado</div>
            <h2>No hemos podido verificarlo</h2>
            <p style="color: var(--ink-soft);">Motivo: <strong>{reason}</strong></p>
            <p style="color: var(--ink-soft);">Contacta con tu agente o con soporte para reintentarlo.</p>
          </div>
        """
    else:
        # pending — mostrar formulario
        body = f"""
          <p class="kyc-sub">Para activar el número de tu agente AI tenemos que verificar tu
          identidad como representante legal del titular. Sube una foto clara
          de tu DNI por las dos caras y una selfie sujetando el DNI.</p>

          <div class="kyc-customer">
            <div class="row"><span class="k">titular:</span> <span class="v">{legal_name}</span></div>
            <div class="row"><span class="k">NIF:</span> <span class="v">{tax_id}</span></div>
            <div class="row"><span class="k">representante:</span> <span class="v">{rep_name}</span></div>
            <div class="row"><span class="k">email:</span> <span class="v">{rep_email}</span></div>
          </div>

          <div class="kyc-err" id="kycErr"></div>

          <form id="kycForm">
            <div class="kyc-field">
              <label>DNI · frontal <span class="req">*</span></label>
              <label class="kyc-dropzone" for="dniFront" id="zoneFront">
                <div class="icon">📄</div>
                <div class="text">Toca para hacer foto o subir imagen</div>
                <img class="kyc-preview" id="previewFront" alt="">
              </label>
              <input type="file" id="dniFront" accept="image/*" capture="environment">
            </div>

            <div class="kyc-field">
              <label>DNI · reverso <span class="req">*</span></label>
              <label class="kyc-dropzone" for="dniBack" id="zoneBack">
                <div class="icon">📄</div>
                <div class="text">Toca para hacer foto o subir imagen</div>
                <img class="kyc-preview" id="previewBack" alt="">
              </label>
              <input type="file" id="dniBack" accept="image/*" capture="environment">
            </div>

            <div class="kyc-field">
              <label>Selfie sujetando el DNI <span style="color: var(--ink-mute);">(opcional pero recomendado)</span></label>
              <label class="kyc-dropzone" for="selfie" id="zoneSelfie">
                <div class="icon">🤳</div>
                <div class="text">Toca para hacerte una foto</div>
                <img class="kyc-preview" id="previewSelfie" alt="">
              </label>
              <input type="file" id="selfie" accept="image/*" capture="user">
            </div>

            <button class="kyc-btn" type="submit" id="kycSubmit" disabled>
              Enviar para verificación
            </button>
          </form>

          <p class="legal">
            Tu DNI se almacena cifrado y se borra automáticamente después de 90 días.
            Cumplimos RGPD. Solo lo verá un operador autorizado de Parallax IEI.
          </p>
        """

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Verificación de identidad</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_KYC_CSS}</style>
</head>
<body>
  <main class="kyc-wrap">
    <div class="kyc-eyebrow">verificación de identidad</div>
    <h1>Confirma que eres tú.</h1>
    {body}
  </main>

<script>
(function() {{
  var token = '{token}';
  var form = document.getElementById('kycForm');
  if (!form) return;

  var fields = [
    {{ inputId: 'dniFront', zoneId: 'zoneFront', previewId: 'previewFront', key: 'dni_front_b64', required: true }},
    {{ inputId: 'dniBack',  zoneId: 'zoneBack',  previewId: 'previewBack',  key: 'dni_back_b64',  required: true }},
    {{ inputId: 'selfie',   zoneId: 'zoneSelfie',previewId: 'previewSelfie',key: 'selfie_b64',    required: false }},
  ];

  var values = {{}};

  function checkReady() {{
    var ready = fields.filter(function(f) {{ return f.required; }})
                       .every(function(f) {{ return values[f.key]; }});
    document.getElementById('kycSubmit').disabled = !ready;
  }}

  fields.forEach(function(f) {{
    var input = document.getElementById(f.inputId);
    var zone = document.getElementById(f.zoneId);
    var preview = document.getElementById(f.previewId);
    input.addEventListener('change', function(e) {{
      var file = e.target.files && e.target.files[0];
      if (!file) return;
      if (file.size > 4 * 1024 * 1024) {{
        showErr('La imagen no puede pesar más de 4 MB. Reduce la resolución y vuelve a intentarlo.');
        return;
      }}
      var reader = new FileReader();
      reader.onload = function(ev) {{
        var dataUrl = ev.target.result;
        values[f.key] = dataUrl;
        preview.src = dataUrl;
        preview.classList.add('show');
        zone.classList.add('has-file');
        zone.querySelector('.text').textContent = file.name;
        checkReady();
      }};
      reader.readAsDataURL(file);
    }});
  }});

  function showErr(msg) {{
    var el = document.getElementById('kycErr');
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(function() {{ el.classList.remove('show'); }}, 6000);
  }}

  form.addEventListener('submit', async function(e) {{
    e.preventDefault();
    document.getElementById('kycSubmit').disabled = true;
    document.getElementById('kycSubmit').textContent = 'Enviando...';
    try {{
      var r = await fetch('/kyc/' + token + '/submit', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(values),
      }});
      if (r.ok) {{
        window.location.reload();
      }} else {{
        var err = await r.json();
        showErr('Error: ' + (err.error || 'desconocido'));
        document.getElementById('kycSubmit').disabled = false;
        document.getElementById('kycSubmit').textContent = 'Enviar para verificación';
      }}
    }} catch(e) {{
      showErr('Error de red: ' + e.message);
      document.getElementById('kycSubmit').disabled = false;
      document.getElementById('kycSubmit').textContent = 'Enviar para verificación';
    }}
  }});
}})();
</script>
</body>
</html>"""


def render_kyc_not_found() -> str:
    """Página 404 si el token no existe."""
    return f"""<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>AMI · KYC</title>
<style>{_KYC_CSS}</style></head>
<body>
  <main class="kyc-wrap" style="text-align:center;">
    <h1>Enlace no válido</h1>
    <p class="kyc-sub">Este enlace de verificación no existe o ha caducado.
    Si crees que es un error, contacta con tu agente.</p>
  </main>
</body>
</html>"""
