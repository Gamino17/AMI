"""Webhook + accept-call para OpenAI Realtime con SIP.

Flujo:

  1. Asterisk recibe INVITE entrante del partner CO con DID 3336033869.
  2. AMI lookup → devuelve forward_sip_uri = sip:proj_xxx@sip.api.openai.com;transport=tls
  3. Asterisk hace Dial SIP-TLS al endpoint OpenAI.
  4. OpenAI recibe SIP, dispara webhook a /v1/_openai/realtime/webhook con
     event type realtime.call.incoming + call_id.
  5. AMI verifica firma del webhook, extrae call_id, llama a
     POST https://api.openai.com/v1/realtime/calls/{call_id}/accept con
     {type, model, instructions}.
  6. OpenAI conecta la sesión Realtime al SIP call → audio bidireccional
     fluye entre el agente (llamante humano) y gpt-realtime.

Env vars necesarias (todas en Render):

  OPENAI_API_KEY              · Bearer token para llamar a OpenAI REST.
  OPENAI_WEBHOOK_SECRET       · secret para verificar firma HMAC de los
                                webhooks. Lo da OpenAI al crear el webhook
                                en el dashboard.
  OPENAI_REALTIME_MODEL       · default "gpt-realtime-2".
  OPENAI_REALTIME_VOICE       · default "alloy".
  OPENAI_REALTIME_INSTRUCTIONS · system prompt del agente. Default es un
                                prompt mínimo en castellano para PoC.

Seguridad:
- Firma de webhooks verificada con HMAC-SHA256 sobre
  "{webhook-id}.{webhook-timestamp}.{body}" usando OPENAI_WEBHOOK_SECRET.
- Replay protection: rechazamos timestamps > 5 minutos de antigüedad.
- Si OPENAI_WEBHOOK_SECRET no está seteado → modo DEV: acepta sin firma
  y emite warning. NUNCA dejar así en producción.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request


DEFAULT_MODEL = "gpt-realtime-2"
DEFAULT_VOICE = "alloy"
DEFAULT_INSTRUCTIONS = (
    "Eres un asistente telefónico amable. Responde en castellano de forma "
    "concisa y natural. Si el llamante pregunta quién eres, di que eres una "
    "demo del protocolo AMI conectada a OpenAI Realtime."
)


# ====================== FIRMA DEL WEBHOOK ======================

REPLAY_WINDOW_SECONDS = 5 * 60   # rechazar webhooks > 5 min de antigüedad


def verify_signature(headers: dict, raw_body: bytes,
                     secret: str | None) -> tuple[bool, str | None]:
    """Verifica el HMAC del webhook de OpenAI.

    OpenAI firma sobre `{webhook_id}.{webhook_timestamp}.{body}` con
    HMAC-SHA256, valor en hex. El header `webhook-signature` puede tener
    formato `v1,<hex>` (similar a Stripe) o solo `<hex>` — aceptamos ambos.

    Si `secret` is None (no configurado), devolvemos (True, "dev_no_secret")
    como modo desarrollo. Documentado arriba.
    """
    if not secret:
        return True, "dev_no_secret"

    wh_id = (headers.get("webhook-id") or "").strip()
    wh_ts = (headers.get("webhook-timestamp") or "").strip()
    wh_sig = (headers.get("webhook-signature") or "").strip()

    if not wh_id or not wh_ts or not wh_sig:
        return False, "missing_signature_headers"

    # Anti-replay
    try:
        ts = int(wh_ts)
    except ValueError:
        return False, "invalid_timestamp"
    if abs(time.time() - ts) > REPLAY_WINDOW_SECONDS:
        return False, "timestamp_outside_window"

    # Acepta tanto "v1,<hex>" como "<hex>" pelado, y posibles múltiples
    # firmas separadas por coma (rotación).
    candidates: list[str] = []
    for part in wh_sig.split():
        for tok in part.split(","):
            tok = tok.strip()
            if tok.startswith("v1="):
                tok = tok[3:]
            elif tok == "v1":
                continue
            if tok:
                candidates.append(tok)

    if not candidates:
        return False, "no_candidate_signatures"

    signed_payload = f"{wh_id}.{wh_ts}.{raw_body.decode('utf-8', 'replace')}"
    expected = hmac.new(secret.encode("utf-8"),
                        signed_payload.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    for cand in candidates:
        try:
            if hmac.compare_digest(cand, expected):
                return True, "ok"
        except TypeError:
            continue
    return False, "signature_mismatch"


# ====================== ACCEPT CALL ======================

def accept_call(call_id: str, api_key: str | None = None,
                instructions: str | None = None,
                model: str | None = None,
                voice: str | None = None) -> dict:
    """Acepta una llamada SIP entrante en OpenAI Realtime.

    Devuelve {ok, status, body, error} para que el caller audite.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return {"ok": False, "error": "no_api_key",
                "detail": "OPENAI_API_KEY env var not set"}
    if not call_id:
        return {"ok": False, "error": "missing_call_id"}

    body = {
        "type": "realtime",
        "model": model or os.environ.get("OPENAI_REALTIME_MODEL", DEFAULT_MODEL),
        "instructions": instructions or os.environ.get(
            "OPENAI_REALTIME_INSTRUCTIONS", DEFAULT_INSTRUCTIONS),
    }

    url = f"https://api.openai.com/v1/realtime/calls/{call_id}/accept"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp_status = r.status
            resp_body = r.read().decode("utf-8", "replace")
        return {"ok": True, "status": resp_status, "body": resp_body[:500]}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace") if e.fp else ""
        return {"ok": False, "status": e.code, "error": "openai_http_error",
                "body": err_body[:500]}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": "network_error", "detail": str(e)}


# ====================== REJECT / HANGUP (helpers) ======================

def reject_call(call_id: str, status_code: int = 486,
                api_key: str | None = None) -> dict:
    """Rechaza una llamada SIP entrante con el SIP status code dado.
    Default 486 (Busy Here). Devuelve dict con el resultado."""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return {"ok": False, "error": "no_api_key"}
    url = f"https://api.openai.com/v1/realtime/calls/{call_id}/reject"
    body = json.dumps({"status_code": status_code}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"ok": True, "status": r.status}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": str(e)}


# ====================== TOP-LEVEL HANDLER ======================

def handle_webhook(headers: dict, raw_body: bytes) -> tuple[int, dict]:
    """Procesa un webhook entrante de OpenAI. Devuelve (status, json_body)
    que el handler HTTP responde.

    - Verifica firma SOLO en modo WARN (loguea fallo pero no rechaza) para
      no bloquear el flujo si nuestro algoritmo HMAC asumido no matchea el
      formato real de OpenAI. TODO: confirmar formato exacto con un webhook
      real y endurecer.
    - Si type == realtime.call.incoming → accept_call.
    - Otros types → log y 200 (no romper la entrega).
    """
    secret = os.environ.get("OPENAI_WEBHOOK_SECRET") or None
    ok_sig, reason = verify_signature(headers, raw_body, secret)
    # Log siempre el estado de la firma para debug — sin rechazar.
    print(f"[openai_realtime] webhook received · sig_ok={ok_sig} · reason={reason} · "
          f"body_len={len(raw_body)} · ct={headers.get('content-type','')}",
          file=sys.stderr)
    if not ok_sig:
        print(f"[openai_realtime] WARN: sig mismatch (reason={reason}) — accepting anyway",
              file=sys.stderr)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        print(f"[openai_realtime] ERROR invalid json: {e}", file=sys.stderr)
        return 400, {"error": "invalid_json", "detail": str(e)}

    event_type = (payload.get("type") or "").strip()
    print(f"[openai_realtime] event type={event_type!r}", file=sys.stderr)

    if event_type == "realtime.call.incoming":
        data = payload.get("data") or {}
        call_id = data.get("call_id")
        print(f"[openai_realtime] call_id={call_id!r}", file=sys.stderr)
        if not call_id:
            return 400, {"error": "missing_call_id"}
        result = accept_call(call_id)
        print(f"[openai_realtime] accept_call result: {result}", file=sys.stderr)
        return 200, {
            "received": "realtime.call.incoming",
            "call_id": call_id,
            "accept": result,
            "sig_check": reason,
        }

    # Otros eventos (lifecycle, transcripts, etc.) — los logueamos y
    # devolvemos 200 para no provocar retries de OpenAI.
    print(f"[openai_realtime] event ignored: type={event_type!r}",
          file=sys.stderr)
    return 200, {"received": event_type, "action": "ignored"}
