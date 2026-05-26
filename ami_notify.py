"""Envío de notificaciones transaccionales: email (SMTP) y SMS (Kannel).

KYC, en el futuro: confirmaciones de contrato, alertas, etc.

Usa smtplib (stdlib) — sin dependencias externas. Config por env vars:

  AMI_SMTP_HOST       (ej. smtp.resend.com, smtp.postmarkapp.com, smtp.gmail.com)
  AMI_SMTP_PORT       (587 STARTTLS por defecto, 465 SSL alternativo)
  AMI_SMTP_USER
  AMI_SMTP_PASSWORD
  AMI_SMTP_FROM       (ej. "AMI <noreply@parallax-iei.com>")
  AMI_SMTP_USE_SSL    (1 para usar SMTPS en lugar de STARTTLS, default 0)
  AMI_OPS_EMAIL       (destinatario interno para notificaciones de ops, ej. los KYC submitted)

Modo desarrollo: si no hay AMI_SMTP_HOST, NO se manda email pero se loguea
a stdout con el contenido. Útil en demo y en tests. La función devuelve
`{"delivered": False, "mode": "dev-log"}` para que el caller sepa.

Diseño:
- Envío síncrono (smtplib es bloqueante). Es lo bastante rápido (<1s con
  buen relay). Si fuese cuello de botella se mueve a un thread pool aparte.
- Plantillas inline (no Jinja). El HTML es trivial y el plain-text fallback
  es necesario para clientes sin HTML.
- No reintentos: si el SMTP falla, se loguea y el caller decide. El KYC
  flow sigue funcionando porque el link también va por la API response.
"""
from __future__ import annotations

import email.message
import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request

import ami_log


def _smtp_configured() -> bool:
    return bool(os.environ.get("AMI_SMTP_HOST"))


def _send_email(to: str, subject: str, html_body: str, text_body: str) -> dict:
    """Envía un email via SMTP. Si SMTP no está configurado, dev-log fallback."""
    if not to or "@" not in to:
        return {"delivered": False, "mode": "skipped", "reason": "no_recipient"}

    if not _smtp_configured():
        # Dev-log: emitimos un solo evento estructurado. El body completo va
        # truncado para no llenar logs en producción si alguien olvida SMTP.
        ami_log.info(
            "notify_devlog",
            channel="email",
            to=to,
            subject=subject,
            body_preview=text_body[:500],
        )
        return {"delivered": False, "mode": "dev-log", "to": to, "subject": subject}

    host = os.environ["AMI_SMTP_HOST"]
    port = int(os.environ.get("AMI_SMTP_PORT") or "587")
    user = os.environ.get("AMI_SMTP_USER")
    password = os.environ.get("AMI_SMTP_PASSWORD")
    sender = os.environ.get("AMI_SMTP_FROM") or (user or "noreply@localhost")
    use_ssl = os.environ.get("AMI_SMTP_USE_SSL") == "1"

    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as s:
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        return {"delivered": True, "mode": "smtp", "to": to}
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        ami_log.error(
            "notify_send_failed",
            channel="email",
            to=to,
            error=str(e),
            exc_type=type(e).__name__,
        )
        return {"delivered": False, "mode": "smtp", "error": str(e)}


# ==========================  SMS via Kannel ==========================

def _send_sms_via_kannel(to: str, body: str) -> dict:
    """Manda un SMS administrativo (no asociado a un MID) directo al
    Kannel sendsms endpoint. Útil para notificaciones de servicio (KYC link,
    confirmaciones de contrato), donde el remitente es AMI, no un cliente.

    Usa las mismas envs que el TelcoAdapter live para Kannel, más
    `AMI_KYC_SMS_FROM` como remitente alfa-numérico/E.164.
    """
    if not to:
        return {"delivered": False, "mode": "skipped", "reason": "no_recipient"}

    sendsms_url = os.environ.get("AMI_KANNEL_SENDSMS_URL")
    if not sendsms_url:
        ami_log.info(
            "notify_devlog",
            channel="sms",
            to=to,
            body_preview=body[:60],
        )
        return {"delivered": False, "mode": "dev-log", "to": to}

    user = os.environ.get("AMI_KANNEL_USERNAME") or ""
    password = os.environ.get("AMI_KANNEL_PASSWORD") or ""
    sender = os.environ.get("AMI_KYC_SMS_FROM") or "AMI"

    params = urllib.parse.urlencode({
        "username": user,
        "password": password,
        "from": sender,
        "to": to,
        "text": body,
    })
    url = sendsms_url + ("&" if "?" in sendsms_url else "?") + params
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if 200 <= status < 300:
                return {"delivered": True, "mode": "kannel", "to": to}
            return {"delivered": False, "mode": "kannel", "status": status}
    except (urllib.error.URLError, OSError) as e:
        ami_log.error(
            "notify_send_failed",
            channel="sms",
            to=to,
            error=str(e),
            exc_type=type(e).__name__,
        )
        return {"delivered": False, "mode": "kannel", "error": str(e)}


# ==========================  KYC ==========================

def send_kyc_invitation(kyc: dict, customer: dict, verification_url: str) -> dict:
    """Envía al rep. legal el link para subir su DNI.

    Por email siempre que tengamos rep_email. Si el customer tiene también
    `representative_phone` (ya normalizado a E.164 en /customer-data), enviamos
    SMS de refuerzo. Devuelve un dict con los dos resultados.
    """
    rep_email = kyc.get("rep_email") or ""
    rep_phone = (customer or {}).get("representative_phone") or ""
    rep_name = (customer or {}).get("representative_name", "")
    legal_name = (customer or {}).get("legal_name", "tu empresa")

    subject = "Verifica tu identidad para activar el número de tu agente AI"
    text = f"""Hola {rep_name},

Para activar el número móvil del agente AI de {legal_name} necesitamos
verificar tu identidad como representante legal.

Es un proceso rápido: abre este enlace desde tu móvil, haz una foto de
las dos caras de tu DNI y una selfie sujetándolo.

{verification_url}

El enlace caduca en 72 horas.

Si no esperabas este email, ignóralo — el proceso se descartará solo.

— Parallax IEI · AMI Protocol
"""
    html = f"""<!doctype html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 560px; margin: 2rem auto; color: #1a1a2e; padding: 0 1rem;">
<h2 style="font-weight: 600; letter-spacing: -0.01em;">Verifica tu identidad</h2>
<p>Hola <strong>{rep_name}</strong>,</p>
<p>Para activar el número móvil del agente AI de <strong>{legal_name}</strong> necesitamos
verificar tu identidad como representante legal.</p>
<p>Es un proceso rápido: abre este enlace desde tu móvil, haz una foto de
las dos caras de tu DNI y una selfie sujetándolo.</p>
<p style="margin: 2rem 0;">
  <a href="{verification_url}"
     style="background: linear-gradient(180deg,#9d80ff,#7a5cff); color: white; padding: 0.9rem 1.6rem;
            border-radius: 8px; text-decoration: none; font-weight: 600;">
    Verificar identidad
  </a>
</p>
<p style="color: #666; font-size: 0.9em;">El enlace caduca en 72 horas.
Si prefieres, copia esta URL: <code style="font-size:0.85em">{verification_url}</code></p>
<hr style="border:0; border-top:1px solid #eee; margin: 2rem 0;">
<p style="color: #888; font-size: 0.85em;">Parallax IEI · AMI Protocol —
si no esperabas este email, ignóralo y el proceso se descartará solo.</p>
</body></html>"""
    email_result = _send_email(rep_email, subject, html, text)

    # SMS redundante si tenemos teléfono (E.164). Cuerpo corto, sin acentos
    # raros, máx 160 GSM7 para no fragmentar.
    sms_result = None
    if rep_phone:
        sms_body = (
            f"AMI: para activar el numero de {legal_name[:30]} verifica tu "
            f"identidad aqui: {verification_url} (caduca 72h)"
        )[:320]  # 2 SMS GSM7 máx
        sms_result = _send_sms_via_kannel(rep_phone, sms_body)

    return {"email": email_result, "sms": sms_result}


def send_kyc_verified_to_human(kyc: dict) -> dict:
    rep_email = kyc.get("rep_email") or ""
    subject = "✓ Identidad verificada"
    text = """Tu identidad ha sido verificada correctamente.

El contrato del número móvil del agente AI está listo para firma.
Tu agente recibirá la notificación automáticamente.

— Parallax IEI · AMI Protocol
"""
    html = """<!doctype html><html><body style="font-family: -apple-system, sans-serif; max-width: 560px; margin: 2rem auto; color: #1a1a2e; padding: 0 1rem;">
<h2 style="color: #2bb55c;">✓ Identidad verificada</h2>
<p>Todo correcto. El contrato del número móvil está listo para firma.
Tu agente AI recibirá la notificación automáticamente.</p>
<hr style="border:0; border-top:1px solid #eee; margin: 2rem 0;">
<p style="color: #888; font-size: 0.85em;">Parallax IEI · AMI Protocol</p>
</body></html>"""
    return _send_email(rep_email, subject, html, text)


def send_kyc_rejected_to_human(kyc: dict) -> dict:
    rep_email = kyc.get("rep_email") or ""
    reason = kyc.get("rejection_reason") or "razón no especificada"
    subject = "No hemos podido verificar tu identidad"
    text = f"""No hemos podido verificar tu identidad.

Motivo: {reason}

Contacta con tu agente o con soporte para reintentarlo.

— Parallax IEI · AMI Protocol
"""
    html = f"""<!doctype html><html><body style="font-family: -apple-system, sans-serif; max-width: 560px; margin: 2rem auto; color: #1a1a2e; padding: 0 1rem;">
<h2 style="color: #c43c5a;">Verificación rechazada</h2>
<p>No hemos podido verificar tu identidad.</p>
<p><strong>Motivo:</strong> {reason}</p>
<p>Contacta con tu agente o con soporte para reintentarlo.</p>
<hr style="border:0; border-top:1px solid #eee; margin: 2rem 0;">
<p style="color: #888; font-size: 0.85em;">Parallax IEI · AMI Protocol</p>
</body></html>"""
    return _send_email(rep_email, subject, html, text)


def send_kyc_submitted_to_ops(kyc: dict) -> dict:
    """Avisa al equipo de ops que hay un nuevo KYC esperando revisión."""
    ops_email = os.environ.get("AMI_OPS_EMAIL", "")
    if not ops_email:
        return {"delivered": False, "mode": "skipped", "reason": "no_ops_email"}
    base = (os.environ.get("AMI_PUBLIC_URL") or "http://localhost:8000").rstrip("/")
    subject = f"[AMI] Nuevo KYC pendiente · {kyc.get('rep_email', '?')}"
    text = f"""Nuevo KYC esperando revisión humana:

  rep_email:       {kyc.get('rep_email', '—')}
  customer_id:     {kyc.get('customer_id', '—')}
  sim_request:     {kyc.get('sim_request_id', '—')}
  account_id:      {kyc.get('account_id', '—')}

Abre el panel para revisarlo: {base}/panel/kyc
"""
    html = f"""<!doctype html><html><body style="font-family: -apple-system, sans-serif; max-width: 560px; margin: 2rem auto;">
<h3>Nuevo KYC pendiente</h3>
<table style="font-family: monospace; font-size: 0.9em;">
<tr><td>rep_email:</td><td>{kyc.get('rep_email', '—')}</td></tr>
<tr><td>customer_id:</td><td>{kyc.get('customer_id', '—')}</td></tr>
<tr><td>sim_request:</td><td>{kyc.get('sim_request_id', '—')}</td></tr>
</table>
<p><a href="{base}/panel/kyc">Abrir panel KYC</a></p>
</body></html>"""
    return _send_email(ops_email, subject, html, text)
