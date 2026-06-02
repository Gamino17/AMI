"""AMI Voice Streams · capa Twilio-compatible para que cualquier agente AI
construido sobre Twilio Voice + Media Streams pueda recibir llamadas via AMI
sin modificarse.

Arquitectura general (alto nivel):

  Llamante (PSTN)
       ↓ SIP
  Partner telco CO
       ↓ SIP
  Asterisk (VPS) ─ Stasis app `ami_voice`
       ↓ ARI                       ↓ externalMedia (RTP μ-law 8kHz)
  ami_voice_bridge (Python)        ami_voice_bridge
       ↓ HTTP webhook (Twilio fmt)        ↑ RTP↔WebSocket
  Cliente (openclaw/Vapi/Retell/…)
       ← TwiML response             ↑↓ WS Media Streams μ-law base64

Este módulo cubre la capa de SIGNALING (no audio):

  - VoiceConfig: por MID, registra voice_url + status_callback_url.
  - twilio_voice_payload: arma el POST que llega al cliente cuando entra
    una llamada (mismo schema que Twilio: CallSid, From, To, …).
  - parse_twiml: parser mínimo de TwiML para extraer las acciones que
    soportamos en v1 (Connect/Stream, Say, Hangup, Reject, Pause).
  - dispatch_voice_webhook: hace el POST al voice_url del cliente,
    devuelve la "TwiML actions" parseadas listas para el bridge.

La capa de AUDIO (puente RTP↔WebSocket Media Streams) vive en
`infra/ami_voice_bridge/` y consume estas funciones via el backend AMI.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


# ====================== VOICE CONFIG (por MID) ======================

def new_voice_config(voice_url: str,
                     status_callback_url: str | None = None,
                     voice_method: str = "POST",
                     status_callback_method: str = "POST") -> dict:
    """Construye el record VoiceConfig (lo persiste el caller en STATE).

    `voice_url` es el equivalente a "Voice URL" del número en Twilio:
    AMI hará POST aquí cuando llegue una llamada al MID. La respuesta
    se parsea como TwiML.
    """
    from ami_api import new_id, now
    return {
        "id": new_id("vcfg"),
        "voice_url": voice_url,
        "voice_method": (voice_method or "POST").upper(),
        "status_callback_url": status_callback_url,
        "status_callback_method": (status_callback_method or "POST").upper(),
        "created_at": now(),
        "updated_at": now(),
    }


# ====================== PAYLOAD TWILIO-COMPATIBLE ======================

def twilio_voice_payload(call: dict, mid_phone: str) -> dict[str, str]:
    """Construye el form-urlencoded payload que Twilio envía a Voice URL.

    Sigue exactamente los nombres y formatos de Twilio para que openclaw,
    Vapi, Retell, Bland y cualquier framework Twilio-compatible parseen
    sin cambios. Campos esenciales documentados aquí:
    https://www.twilio.com/docs/voice/twiml#request-parameters

    Mapeamos sobre nuestro Call record (definido en ami_api.py):
      call_id       → CallSid
      call.from     → From / Caller
      mid_phone     → To / Called
      direction     → Direction (inbound|outbound-api|…)
    """
    return {
        "CallSid":      call.get("id", ""),
        "AccountSid":   call.get("account_id") or "ami_account_default",
        "From":         call.get("from", ""),
        "To":           mid_phone or call.get("to", ""),
        "Caller":       call.get("from", ""),
        "Called":       mid_phone or call.get("to", ""),
        "Direction":    (
            "inbound" if call.get("direction") == "inbound" else "outbound-api"
        ),
        "CallStatus":   _map_call_status(call.get("status", "initiated")),
        "ApiVersion":   "2010-04-01",
        "ForwardedFrom": "",
        "CallerName":   call.get("caller_name") or "",
        "FromCountry":  call.get("from_country") or "",
        "FromState":    "",
        "FromCity":     "",
        "FromZip":      "",
        "ToCountry":    call.get("to_country") or "",
        "ToState":      "",
        "ToCity":       "",
        "ToZip":        "",
    }


def _map_call_status(ami_status: str) -> str:
    """AMI → Twilio status. Twilio usa otros nombres en el webhook inicial."""
    return {
        "initiated":   "ringing",
        "ringing":     "ringing",
        "in_progress": "in-progress",
        "completed":   "completed",
        "no_answer":   "no-answer",
        "busy":        "busy",
        "failed":      "failed",
        "cancelled":   "canceled",
    }.get(ami_status or "", "ringing")


# ====================== PARSER TWIML ======================

# Verbos TwiML soportados en v1. Resto se ignora con warning.
_SUPPORTED_VERBS = {"Response", "Connect", "Stream", "Say", "Hangup",
                     "Reject", "Pause", "Parameter"}


def parse_twiml(xml_bytes: bytes) -> list[dict]:
    """Parsea TwiML y devuelve una lista de acciones para el bridge.

    Cada acción es un dict con `verb` y atributos relevantes. El bridge
    las ejecuta en orden hasta que una llega a terminar la llamada
    (Connect, Hangup, Reject).

    Soportados en v1:
      - <Connect><Stream url=".." track="inbound_track"/></Connect>
        → {"verb": "stream", "url": "wss://...", "track": "...", "params": {...}}
      - <Say voice="alice">texto</Say>
        → {"verb": "say", "text": "texto", "voice": "alice", "language": "es-ES"}
      - <Hangup/>
        → {"verb": "hangup"}
      - <Reject reason="busy"/>
        → {"verb": "reject", "reason": "busy"}
      - <Pause length="2"/>
        → {"verb": "pause", "length_s": 2}

    Levanta `ValueError` si el XML no es válido. NO levanta si encuentra
    verbos no soportados (los registra silencioso, robusto frente a TwiML
    que mezcla verbos avanzados con los soportados).
    """
    if not xml_bytes or not xml_bytes.strip():
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"invalid_twiml: {e}")

    if _strip_ns(root.tag) != "Response":
        raise ValueError(f"twiml_root_must_be_Response, got {root.tag!r}")

    actions: list[dict] = []
    for child in root:
        tag = _strip_ns(child.tag)
        if tag == "Connect":
            # <Connect> envuelve un <Stream>. Soportamos solo Stream en v1.
            for sub in child:
                if _strip_ns(sub.tag) == "Stream":
                    params = {}
                    for p in sub:
                        if _strip_ns(p.tag) == "Parameter":
                            name = p.attrib.get("name", "")
                            value = p.attrib.get("value", "")
                            if name:
                                params[name] = value
                    actions.append({
                        "verb":  "stream",
                        "url":   sub.attrib.get("url", ""),
                        "track": sub.attrib.get("track", "inbound_track"),
                        "params": params,
                    })
        elif tag == "Say":
            actions.append({
                "verb":     "say",
                "text":     (child.text or "").strip(),
                "voice":    child.attrib.get("voice", "alice"),
                "language": child.attrib.get("language", "es-ES"),
                "loop":     int(child.attrib.get("loop", "1") or "1"),
            })
        elif tag == "Hangup":
            actions.append({"verb": "hangup"})
        elif tag == "Reject":
            actions.append({
                "verb":   "reject",
                "reason": child.attrib.get("reason", "rejected"),
            })
        elif tag == "Pause":
            actions.append({
                "verb":     "pause",
                "length_s": int(child.attrib.get("length", "1") or "1"),
            })
        # Verbos no soportados: ignorar (verbo silencioso para v1).

    return actions


def _strip_ns(tag: str) -> str:
    """Quita el namespace XML del tag si lo tiene."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


# ====================== WEBHOOK DISPATCH ======================

def dispatch_voice_webhook(voice_url: str, payload: dict[str, str],
                           method: str = "POST",
                           timeout: float = 5.0) -> tuple[int, bytes, list[dict]]:
    """Hace el POST form-urlencoded al voice_url del cliente y parsea la
    respuesta TwiML. Devuelve (http_status, raw_body, parsed_actions).

    Si el cliente devuelve un body que NO es TwiML válido, parsed_actions
    es []. El caller decide qué hacer (probablemente colgar la llamada
    con un Hangup implícito para no dejarla en limbo).
    """
    if method.upper() == "GET":
        url = voice_url + ("&" if "?" in voice_url else "?") + urllib.parse.urlencode(payload)
        req = urllib.request.Request(url, method="GET")
    else:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(voice_url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", "TwilioProxy/AMI-Voice-Streams/v1")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            body = r.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if e.fp else b""), []
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e).encode("utf-8"), []

    try:
        actions = parse_twiml(body)
    except ValueError:
        actions = []
    return status, body, actions


# ====================== HELPERS ======================

_WSS_RE = re.compile(r"^wss://[A-Za-z0-9.\-]+(?::\d{1,5})?/.+$")


def is_safe_wss_url(url: str) -> bool:
    """Valida que el URL del <Stream> sea wss:// bien formado. NO valida
    que el host sea reachable — eso lo descubre el bridge al intentar
    abrir el WebSocket."""
    if not url:
        return False
    return bool(_WSS_RE.match(url.strip()))
