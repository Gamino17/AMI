"""AMI telco adapters.

Capa de abstracción entre el backend del protocolo (ami_api.py) y la
infraestructura telco real (SMSC + PBX). El backend nunca habla SMPP ni
SIP/ARI directamente: delega en un TelcoAdapter que en v1 es mock y en
prod es un cliente HTTP a Kannel + Asterisk.

Selector vía env var AMI_TELCO_MODE:
  - "mock" (default): MockTelcoAdapter — comportamiento simulado.
  - "live": LiveTelcoAdapter — Kannel para SMS, Asterisk ARI para voz.

Cuando el partner enchufe credenciales (SMPP + ARI), cambia AMI_TELCO_MODE
a "live" y nada en ami_api.py necesita modificarse: el contrato externo y
los webhooks /v1/_telco/* son los mismos en ambos modos.
"""
from __future__ import annotations
import os

from .base import TelcoAdapter
from .mock import MockTelcoAdapter

__all__ = ["TelcoAdapter", "MockTelcoAdapter", "get_active_adapter"]


_ACTIVE: TelcoAdapter | None = None


def get_active_adapter() -> TelcoAdapter:
    """Devuelve el adapter activo (singleton). Se inicializa al primer uso."""
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE
    mode = (os.environ.get("AMI_TELCO_MODE") or "mock").strip().lower()
    if mode == "live":
        # Import perezoso para que el modo mock no requiera dependencias del live.
        from .live import LiveTelcoAdapter
        _ACTIVE = LiveTelcoAdapter()
    else:
        _ACTIVE = MockTelcoAdapter()
    return _ACTIVE


def reset_active_adapter() -> None:
    """Solo para tests: fuerza re-inicialización en la siguiente llamada."""
    global _ACTIVE
    _ACTIVE = None
