"""MockTelcoAdapter — simula el telco con threading.Timer.

Compatible bit-a-bit con el comportamiento que tenía ami_api.py antes del
refactor: queued → sent → delivered en ~450ms, call lifecycle en ~900ms.
Esto es lo que corre en v1 mientras el partner no enchufa SMSC/PBX reales.
"""
from __future__ import annotations
import secrets
import threading
from typing import Any

from .base import TelcoAdapter


class MockTelcoAdapter(TelcoAdapter):
    name = "mock"

    def send_sms(self, msg: dict[str, Any]) -> None:
        # Importamos perezosamente para evitar ciclo de imports al cargar el
        # módulo (ami_api importa ami_telco, ami_telco no puede importar
        # ami_api al top-level).
        import ami_api
        msg_id = msg["id"]

        def to_sent() -> None:
            m = ami_api.STATE["sms_messages"].get(msg_id)
            if m and m["status"] == "queued":
                m["status"] = "sent"
                m["telco_ref"] = "mock:" + secrets.token_hex(6)
                ami_api.event("sms_sent", "sms_message", msg_id,
                              {"telco_ref": m["telco_ref"]})

        def to_delivered() -> None:
            m = ami_api.STATE["sms_messages"].get(msg_id)
            if m and m["status"] == "sent":
                m["status"] = "delivered"
                m["delivered_at"] = ami_api.now()
                ami_api.event("sms_delivered", "sms_message", msg_id, {})

        threading.Timer(0.15, to_sent).start()
        threading.Timer(0.45, to_delivered).start()

    def place_call(self, call: dict[str, Any]) -> None:
        import ami_api
        call_id = call["id"]

        def go(target: str, **patch: Any) -> None:
            c = ami_api.STATE["calls"].get(call_id)
            if not c:
                return
            try:
                ami_api.transition_call(c, target, **patch)
            except ValueError:
                pass  # estado terminal, ignorar

        def to_ringing() -> None:
            c = ami_api.STATE["calls"].get(call_id)
            if c and c["status"] == "initiated":
                go("ringing", telco_ref="mock:" + secrets.token_hex(6))

        def to_in_progress() -> None:
            c = ami_api.STATE["calls"].get(call_id)
            if c and c["status"] == "ringing":
                go("in_progress")

        def to_completed() -> None:
            c = ami_api.STATE["calls"].get(call_id)
            if c and c["status"] == "in_progress":
                go("completed", hangup_cause="normal_clearing")

        threading.Timer(0.15, to_ringing).start()
        threading.Timer(0.40, to_in_progress).start()
        threading.Timer(0.90, to_completed).start()

    def hangup_call(self, call: dict[str, Any]) -> None:
        # En mock el "telco" obedece inmediatamente: el backend ya aplicó
        # la transición; aquí no hacemos red. En live esto manda BYE/HANGUP.
        return
