"""Interface abstracta TelcoAdapter.

Mínimo contrato que el backend (ami_api.py) necesita para operar SMS y voz:

  send_sms(msg_record)       — pide al adapter que envíe el SMS y reporte
                               estado vía callback (queued → sent → delivered).
                               El adapter NO modifica STATE directamente; en su
                               lugar invoca callbacks que el backend cablea.

  place_call(call_record)    — pide al adapter que origine la llamada saliente
                               y la bridgee al callback_sip_uri. Reporta
                               estado vía callback.

  forward_call(call_record, sip_uri)
                             — para entrantes: pide al adapter que reenvíe
                               la llamada al destino SIP del cliente.

Los callbacks son los webhooks ya existentes en ami_api.py:
  - on_sms_sent(msg_id, telco_ref)
  - on_sms_delivered(msg_id)
  - on_sms_failed(msg_id, reason)
  - on_call_status(call_id, new_status, **patch)

En modo mock estos callbacks se invocan desde threading.Timer. En modo live
los invocan los webhooks /v1/_telco/* que recibe del SMSC/PBX real.
"""
from __future__ import annotations
from typing import Callable, Any


class TelcoAdapter:
    """Interfaz abstracta. Las subclases implementan send/place según el
    transporte real (mock, kannel HTTP, asterisk ARI, etc.)."""

    name: str = "abstract"

    # ----- SMS -----
    def send_sms(self, msg: dict[str, Any]) -> None:
        raise NotImplementedError

    # ----- Voz -----
    def place_call(self, call: dict[str, Any]) -> None:
        raise NotImplementedError

    def hangup_call(self, call: dict[str, Any]) -> None:
        """Solicita al telco que cuelgue. Si el adapter no puede ejecutarlo
        síncrono, el cambio de estado lo hace el backend al recibir el cuelgue
        confirmado por webhook. En modo mock, marca el estado al instante."""
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        """Estado del adapter (útil en /v1/health). Por defecto sólo el nombre."""
        return {"adapter": self.name}
