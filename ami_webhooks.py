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
import secrets
import threading
import urllib.error
import urllib.request


SUPPORTED_EVENTS = {
    "sms.inbound", "sms.delivered", "sms.failed",
    "call.inbound", "call.completed", "call.failed",
}

WEBHOOK_AUTO_DISABLE_THRESHOLD = 10  # tras este nº de fallos consecutivos, off.
DELIVERY_TIMEOUT_S = 8.0
RETRY_DELAYS_S = [0.5, 2.0, 8.0]


def new_webhook(mid: str, url: str, events: list[str]) -> dict:
    """Construye el record. No lo persiste — el caller decide dónde guardarlo."""
    from ami_api import new_id, now
    secret = "whsec_" + secrets.token_hex(32)
    return {
        "id": new_id("wh"),
        "mid": mid,
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
        "mid": wh["mid"],
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
    """Encuentra los webhooks suscritos al event/mid y los dispara async.

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
    payload = {
        "event": event,
        "delivered_at": now(),
        "mid": mid,
        "data": data,
    }
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for wh in matched:
        t = threading.Thread(
            target=_deliver_with_retries,
            args=(wh["id"], wh["url"], wh["secret"], body_bytes),
            daemon=True,
        )
        t.start()
    return len(matched)


def _deliver_with_retries(wh_id: str, url: str, secret: str, body: bytes) -> None:
    """Intenta entregar el webhook. Reintentos con backoff. Actualiza STATE."""
    import time
    from ami_api import STATE, now, event as audit_event
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Ami-Signature": f"sha256={sig}",
        "X-Ami-Webhook-Id": wh_id,
        "User-Agent": "AMI/1.0 (+webhooks)",
    }
    attempts = 0
    ok = False
    last_error = None
    while attempts < len(RETRY_DELAYS_S) + 1:
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=DELIVERY_TIMEOUT_S) as r:
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


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """Helper que el cliente puede usar (o testear) para validar la firma."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])
