"""LiveTelcoAdapter — habla a SMSC (Kannel) y PBX (Asterisk ARI).

Stdlib pura: urllib.request para HTTP. No mantenemos sockets persistentes ni
WebSocket en este lado — Asterisk/Kannel nos llaman de vuelta vía webhooks
en /v1/_telco/* cuando hay eventos. Eso mantiene este adapter delgado y
recuperable (cada request es independiente, sin estado entre calls).

Variables de entorno (configurar al activar AMI_TELCO_MODE=live):

  # Kannel (SMSC HTTP gateway)
  AMI_KANNEL_SENDSMS_URL   — p.ej. http://kannel:13013/cgi-bin/sendsms
  AMI_KANNEL_USERNAME      — sendsms-user del kannel.conf
  AMI_KANNEL_PASSWORD      — password del sendsms-user
  AMI_KANNEL_DLR_URL       — URL pública del webhook DLR (p.ej.
                              https://api.protocolami.com/v1/_telco/sms/dlr)
                              que Kannel llama cuando llega DLR
  AMI_KANNEL_DLR_MASK      — máscara de DLRs a recibir (default 31 = todos)

  # Asterisk ARI
  AMI_ARI_URL              — p.ej. http://asterisk:8088/ari
  AMI_ARI_USERNAME         — usuario ARI configurado en ari.conf
  AMI_ARI_PASSWORD         — password ARI
  AMI_ARI_APP              — Stasis application name (default "ami_voice")
  AMI_ARI_TRUNK            — endpoint PJSIP del trunk al PSTN (p.ej.
                              "PJSIP/trunk_partner")
"""
from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request
from typing import Any

from .base import TelcoAdapter


def _http_get(url: str, params: dict[str, Any] | None = None,
              auth: tuple[str, str] | None = None,
              timeout: float = 10.0) -> tuple[int, str]:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    if auth:
        import base64
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace") if e.fp else str(e)
    except Exception as e:
        return 0, str(e)


def _http_post_json(url: str, body: dict[str, Any],
                    auth: tuple[str, str] | None = None,
                    timeout: float = 10.0) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if auth:
        import base64
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace") if e.fp else str(e)
    except Exception as e:
        return 0, str(e)


class LiveTelcoAdapter(TelcoAdapter):
    """Implementación de producción. SMS via Kannel HTTP, voz via Asterisk ARI."""

    name = "live"

    def __init__(self) -> None:
        # Kannel
        self.kannel_url = os.environ.get("AMI_KANNEL_SENDSMS_URL") or ""
        self.kannel_user = os.environ.get("AMI_KANNEL_USERNAME") or ""
        self.kannel_pass = os.environ.get("AMI_KANNEL_PASSWORD") or ""
        self.kannel_dlr_url = os.environ.get("AMI_KANNEL_DLR_URL") or ""
        self.kannel_dlr_mask = os.environ.get("AMI_KANNEL_DLR_MASK") or "31"
        # Asterisk
        self.ari_url = (os.environ.get("AMI_ARI_URL") or "").rstrip("/")
        self.ari_user = os.environ.get("AMI_ARI_USERNAME") or ""
        self.ari_pass = os.environ.get("AMI_ARI_PASSWORD") or ""
        self.ari_app = os.environ.get("AMI_ARI_APP") or "ami_voice"
        self.ari_trunk = os.environ.get("AMI_ARI_TRUNK") or ""

    # ===================== SMS =====================
    def send_sms(self, msg: dict[str, Any]) -> None:
        """Envía el SMS a Kannel por HTTP. Kannel lo cursa por SMPP al partner
        y nos llama de vuelta a /v1/_telco/sms/dlr con el DLR final."""
        import ami_api
        if not self.kannel_url:
            self._mark_failed(msg["id"], "kannel_not_configured")
            return

        params = {
            "username": self.kannel_user,
            "password": self.kannel_pass,
            "from": msg["from"],
            "to": msg["to"],
            "text": msg["body"],
            "charset": "UTF-8",
            "coding": "2",  # UCS-2 para soportar emoji y caracteres no-GSM
        }
        if self.kannel_dlr_url:
            # Kannel sustituye %F (foreign_id), %d (DLR mask), %A (msisdn dest).
            # Pasamos nuestro msg_id como meta-data en el path para que el DLR
            # nos lo devuelva y podamos correlacionar.
            dlr_url = f"{self.kannel_dlr_url}?msg_id={urllib.parse.quote(msg['id'])}"
            params["dlr-url"] = dlr_url
            params["dlr-mask"] = self.kannel_dlr_mask

        status, body = _http_get(self.kannel_url, params=params, timeout=10.0)
        if 200 <= status < 300:
            # Kannel devuelve "0: Accepted for delivery" cuando lo encola.
            m = ami_api.STATE["sms_messages"].get(msg["id"])
            if m and m["status"] == "queued":
                m["status"] = "sent"
                m["telco_ref"] = body.strip()[:64]
                ami_api.event("sms_sent", "sms_message", msg["id"],
                              {"telco_ref": m["telco_ref"]})
            # delivered llega después por DLR webhook
        else:
            self._mark_failed(msg["id"], f"kannel_http_{status}: {body[:200]}")

    def _mark_failed(self, msg_id: str, reason: str) -> None:
        import ami_api
        m = ami_api.STATE["sms_messages"].get(msg_id)
        if m and m["status"] in ("queued", "sent"):
            m["status"] = "failed"
            ami_api.event("sms_failed", "sms_message", msg_id, {"reason": reason})

    # ===================== Voz =====================
    def place_call(self, call: dict[str, Any]) -> None:
        """Origina la llamada vía ARI. Asterisk marca al PSTN por el trunk y
        cuando responde la bridgea por SIP al callback_sip_uri del cliente.

        El dialplan / Stasis application se encarga del bridge SIP↔SIP y de
        notificar transiciones a /v1/_telco/calls/{id}/status."""
        import ami_api
        if not self.ari_url or not self.ari_trunk:
            self._fail_call(call["id"], "ari_not_configured")
            return

        # Lanzamos la llamada al PSTN vía PJSIP/trunk_partner/sip:+34...@...
        endpoint = f"{self.ari_trunk}/sip:{call['to'].lstrip('+')}@pstn"
        body = {
            "endpoint": endpoint,
            "app": self.ari_app,
            "appArgs": json.dumps({
                "ami_call_id": call["id"],
                "ami_mid": call["mid"],
                "callback_sip_uri": call["callback_sip_uri"],
                "from": call["from"],
            }),
            "callerId": call["from"],
            "timeout": 30,
            "channelId": call["id"],  # idempotencia + correlación
        }
        status, response = _http_post_json(
            f"{self.ari_url}/channels",
            body=body,
            auth=(self.ari_user, self.ari_pass),
            timeout=15.0,
        )
        if 200 <= status < 300:
            try:
                resp = json.loads(response)
            except Exception:
                resp = {}
            c = ami_api.STATE["calls"].get(call["id"])
            if c:
                c["telco_ref"] = resp.get("id") or resp.get("name") or "asterisk"
            # transiciones (ringing/in_progress/completed) llegan por webhook
            # /v1/_telco/calls/{id}/status que el dialplan dispara.
        else:
            self._fail_call(call["id"], f"ari_http_{status}: {response[:200]}")

    def _fail_call(self, call_id: str, reason: str) -> None:
        import ami_api
        c = ami_api.STATE["calls"].get(call_id)
        if not c:
            return
        try:
            ami_api.transition_call(c, "failed", hangup_cause=reason)
        except ValueError:
            pass

    def hangup_call(self, call: dict[str, Any]) -> None:
        """DELETE /ari/channels/{channelId} provoca BYE/HANGUP en Asterisk.
        El webhook /v1/_telco/calls/{id}/status confirma la transición."""
        if not self.ari_url:
            return
        url = f"{self.ari_url}/channels/{urllib.parse.quote(call['id'])}"
        req = urllib.request.Request(url, method="DELETE")
        import base64
        token = base64.b64encode(f"{self.ari_user}:{self.ari_pass}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
        try:
            urllib.request.urlopen(req, timeout=10.0)
        except Exception:
            pass  # idempotente; webhook reportará el estado real

    def health(self) -> dict[str, Any]:
        return {
            "adapter": "live",
            "kannel_configured": bool(self.kannel_url and self.kannel_user),
            "ari_configured": bool(self.ari_url and self.ari_trunk),
        }
