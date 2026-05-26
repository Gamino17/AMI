"""Webhooks salientes desde AMI hacia el cliente.

Cuando ocurre un evento relevante para un MID (SMS entrante, llamada entrante,
SMS marcado como delivered/failed, llamada completada, etc.), AMI dispara un
POST HTTP al webhook que el customer haya registrado para ese MID. El payload
va firmado con HMAC-SHA256 usando el secret del webhook, así el cliente puede
verificar autenticidad sin secretos compartidos en la URL.

Schema del webhook (vive en STATE["webhooks"]):

    {
        "id": "wh_xxx",
        "mid": "mid_xxx",
        "url": "https://customer.example.com/ami/hook",
        "events": ["sms.inbound", "call.inbound", "call.completed", ...]
                  o ["*"] para todos,
        "secret": "whsec_<64hex>",         # en plano (in-memory); HMAC necesita acceso
        "status": "active" | "disabled",
        "created_at": ISO,
        "last_delivery_at": None | ISO,
        "last_delivery_status": None | "ok" | "failed",
        "failure_count": 0,
    }

Eventos soportados (lista cerrada; cualquier otro nombre se ignora):

    sms.inbound       — llegó un MO al MID
    sms.delivered     — DLR confirmó entrega del MT
    sms.failed        — DLR reportó fallo del MT
    call.inbound      — entrante recibida (antes de forward)
    call.completed    — llamada terminada (con duración)
    call.failed       — llamada fallida (no_answer, busy, failed, cancelled)

Entrega: HTTP POST JSON con header X-Ami-Signature: sha256=<hex>. Hasta 3
intentos con backoff [0.5, 2, 8] segundos. Si los 3 fallan, failure_count++.
Si failure_count > 10, el webhook se auto-desactiva (status=disabled).
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


SUPPORTED_EVENTS = {
    "sms.inbound", "sms.delivered", "sms.failed",
    "call.inbound", "call.completed", "call.failed",
    "kyc.submitted", "kyc.verified", "kyc.rejected", "kyc.expired",
}

WEBHOOK_AUTO_DISABLE_THRESHOLD = 10  # tras este nº de fallos consecutivos, off.
DELIVERY_TIMEOUT_S = 8.0
RETRY_DELAYS_S = [0.5, 2.0, 8.0]

# Pool global para deliveries — cap el nº de threads concurrentes para
# protegernos de thread bombs (1000 webhooks × varios eventos en ráfaga).
# Configurable por env. Si la cola se llena, el siguiente delivery se mete
# a una cola interna del executor — no spawn ilimitado.
_MAX_DELIVERY_WORKERS = int(os.environ.get("AMI_WEBHOOK_MAX_WORKERS") or 32)
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_DELIVERY_WORKERS,
                                thread_name_prefix="ami-webhook")


def new_webhook(mid: str, url: str, events: list[str],
                 account_id: str | None = None) -> dict:
    """Construye el record. No lo persiste — el caller decide dónde guardarlo.

    Dos modos de scoping:
      - `mid` set, account_id None → webhook scoped a UN MID (sms/call events).
      - `mid` "", account_id set   → webhook scoped a TODA la cuenta del cliente
                                      (recibe eventos como kyc.* que ocurren antes
                                      de que exista un MID).
    """
    from ami_api import new_id, now
    secret = "whsec_" + secrets.token_hex(32)
    return {
        "id": new_id("wh"),
        "mid": mid or "",
        "account_id": account_id or None,
        "url": url,
        "events": events or ["*"],
        "secret": secret,
        "status": "active",
        "created_at": now(),
        "last_delivery_at": None,
        "last_delivery_status": None,
        "failure_count": 0,
    }


def webhook_summary(wh: dict) -> dict:
    """Vista pública del webhook (sin secret completo, solo prefijo)."""
    return {
        "id": wh["id"],
        "mid": wh.get("mid") or None,
        "account_id": wh.get("account_id"),
        "scope": "account" if wh.get("account_id") else "mid",
        "url": wh["url"],
        "events": wh["events"],
        "status": wh["status"],
        "created_at": wh["created_at"],
        "last_delivery_at": wh.get("last_delivery_at"),
        "last_delivery_status": wh.get("last_delivery_status"),
        "failure_count": wh.get("failure_count", 0),
        "secret_prefix": wh["secret"][:14],   # whsec_xxxxxx (prefijo)
    }


def dispatch_event(event: str, mid: str, data: dict) -> int:
    """Encuentra los webhooks scoped al MID y los dispara async.

    Para eventos a nivel cuenta (kyc.*, system.*), usar dispatch_account_event.

    Devuelve el nº de webhooks que se intentaron disparar (útil para audit log).
    El envío se hace en threads daemon: el caller no se bloquea ni le importa
    el resultado.
    """
    if event not in SUPPORTED_EVENTS:
        return 0
    from ami_api import STATE, now
    matched = []
    for wh in STATE["webhooks"].values():
        if wh.get("mid") != mid or wh.get("status") != "active":
            continue
        ev = wh.get("events") or ["*"]
        if "*" in ev or event in ev:
            matched.append(wh)
    if not matched:
        return 0
    return _dispatch(event, matched, {"mid": mid}, data, now())


def dispatch_account_event(event: str, account_id: str, data: dict) -> int:
    """Encuentra webhooks scoped al account_id (no a un MID específico)
    y los dispara. Usado para eventos como kyc.* que ocurren antes de
    que exista un MID.
    """
    if event not in SUPPORTED_EVENTS:
        return 0
    if not account_id:
        return 0
    from ami_api import STATE, now
    matched = []
    for wh in STATE["webhooks"].values():
        if wh.get("account_id") != account_id or wh.get("status") != "active":
            continue
        ev = wh.get("events") or ["*"]
        if "*" in ev or event in ev:
            matched.append(wh)
    if not matched:
        return 0
    return _dispatch(event, matched, {"account_id": account_id}, data, now())


def _dispatch(event: str, matched: list[dict], scope: dict,
              data: dict, ts: str) -> int:
    """Submitter común para MID-scoped y account-scoped."""
    payload = {
        "event": event,
        "delivered_at": ts,
        **scope,
        "data": data,
    }
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for wh in matched:
        _EXECUTOR.submit(
            _deliver_with_retries,
            wh["id"], wh["url"], wh["secret"], body_bytes,
        )
    return len(matched)


def _deliver_with_retries(wh_id: str, url: str, secret: str, body: bytes) -> None:
    """Intenta entregar el webhook. Reintentos con backoff. Actualiza STATE.

    Seguridad:
      - Re-validamos la URL contra SSRF guard JUSTO antes del connect, para
        defensar contra DNS rebinding (la IP puede haber cambiado desde el
        create del webhook).
      - Opener custom rechaza redirects 3xx (impide saltar a IP interna).
      - Capamos el body de respuesta a 64 KiB para evitar DoS de memoria si
        el endpoint malicioso responde gigabytes.
    """
    import time
    import ami_security
    from ami_api import STATE, now, event as audit_event

    safe, reason = ami_security.is_safe_webhook_url(url)
    if not safe:
        # No reintentar; el endpoint nunca debió aceptarse en su día.
        wh = STATE["webhooks"].get(wh_id)
        if wh:
            wh["last_delivery_at"] = now()
            wh["last_delivery_status"] = "failed"
            wh["failure_count"] = wh.get("failure_count", 0) + 1
        audit_event("webhook_failed", "webhook", wh_id,
                    {"error": f"ssrf_guard_{reason}"})
        return

    # Firmamos `timestamp.body_bytes` (no solo body) para que un replay
    # fuera de ventana sea detectable. El cliente verifica el ts y compara.
    import time as _time
    ts = str(int(_time.time()))
    signed_payload = ts.encode("ascii") + b"." + body
    sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Ami-Signature": f"sha256={sig}",
        "X-Ami-Timestamp": ts,
        "X-Ami-Webhook-Id": wh_id,
        "User-Agent": "AMI/1.0 (+webhooks)",
    }
    opener = ami_security.build_safe_opener()
    attempts = 0
    ok = False
    last_error = None
    while attempts < len(RETRY_DELAYS_S) + 1:
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in headers.items():
                req.add_header(k, v)
            with opener.open(req, timeout=DELIVERY_TIMEOUT_S) as r:
                # Cap del body para no agotar memoria con respuestas gigantes.
                _ = r.read(65536)
                if 200 <= r.status < 300:
                    ok = True
                    break
                last_error = f"http_{r.status}"
        except urllib.error.HTTPError as e:
            last_error = f"http_{e.code}"
        except Exception as e:
            last_error = type(e).__name__
        if attempts < len(RETRY_DELAYS_S):
            time.sleep(RETRY_DELAYS_S[attempts])
        attempts += 1

    # Métrica del intento (counter por result: ok | failed)
    try:
        import ami_metrics
        ami_metrics.WEBHOOKS_DELIVERED.inc(result="ok" if ok else "failed")
    except ImportError:
        pass

    # Actualiza el record del webhook
    wh = STATE["webhooks"].get(wh_id)
    if not wh:
        return
    wh["last_delivery_at"] = now()
    if ok:
        wh["last_delivery_status"] = "ok"
        wh["failure_count"] = 0
        audit_event("webhook_delivered", "webhook", wh_id,
                    {"url": url, "attempts": attempts + 1})
    else:
        wh["last_delivery_status"] = "failed"
        wh["failure_count"] = wh.get("failure_count", 0) + 1
        if wh["failure_count"] >= WEBHOOK_AUTO_DISABLE_THRESHOLD:
            wh["status"] = "disabled"
            audit_event("webhook_auto_disabled", "webhook", wh_id,
                        {"url": url, "failure_count": wh["failure_count"]})
            try:
                import ami_metrics
                ami_metrics.log_error("webhook_auto_disabled", wh_id=wh_id,
                                       url=url, failure_count=wh["failure_count"])
            except ImportError: pass
        audit_event("webhook_failed", "webhook", wh_id,
                    {"url": url, "error": last_error,
                     "failure_count": wh["failure_count"]})
        try:
            import ami_metrics
            ami_metrics.log_warn("webhook_failed", wh_id=wh_id, url=url,
                                  error=last_error,
                                  failure_count=wh["failure_count"])
        except ImportError: pass


def verify_signature(secret: str, body: bytes, signature_header: str,
                      timestamp_header: str | None = None,
                      tolerance_sec: int = 300) -> bool:
    """Verifica la firma HMAC del webhook.

    Si se pasa `timestamp_header` (X-Ami-Timestamp), verifica que esté dentro
    de ±tolerance_sec del reloj actual y firma `ts.body` (anti-replay).
    Si NO se pasa, mantiene compat con firmas viejas que solo cubrían `body`.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    presented = signature_header[7:]

    if timestamp_header is not None:
        import time as _time
        try:
            ts = int(timestamp_header)
        except (TypeError, ValueError):
            return False
        if abs(int(_time.time()) - ts) > tolerance_sec:
            return False
        signed = timestamp_header.encode("ascii") + b"." + body
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, presented)

    # Compat: sin timestamp solo cubre body
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, presented)
