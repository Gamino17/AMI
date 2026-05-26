"""Structured logging utility for AMI.

JSON-line logger sin dependencias externas (stdlib only). Output:
- info/warn/error reciben un `event` (snake_case) y campos kwargs.
- En `AMI_LOG_FORMAT=json` (default en prod) cada call imprime una sola
  línea JSON a stdout (info) o stderr (warn/error). Compatible con
  Render/CloudWatch/Datadog que parsean JSON automáticamente.
- En `AMI_LOG_FORMAT=text` (default cuando no se setea) imprime formato
  legible `<ts> [<level>] <event> | k=v k=v` para dev local.

Por qué no usar `logging` de stdlib:
- Configurar handlers/formatters es overkill para 3 funciones.
- El módulo `logging` complica los tests (handlers globales, captura de
  output). `print()` con `json.dumps()` es trivial de testear con capsys.

Env vars:
- `AMI_LOG_FORMAT`: "json" o "text". Default "text".
- `AMI_LOG_LEVEL`: "info" | "warn" | "error". Default "info". Filtra
  por nivel: warn descarta info, error descarta info+warn.

Redacción de secretos: campos cuyo nombre empieza por `password`, `secret`,
`token` o `api_key` se enmascaran como "***" para evitar filtrar
credenciales en logs. Aplica tanto a JSON como a text.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os as _os
import sys as _sys
from typing import Any


_LEVELS = {"info": 10, "warn": 20, "error": 30}

_SECRET_PREFIXES = ("password", "secret", "token", "api_key")


def _now_iso() -> str:
    """ISO-8601 UTC con sufijo Z. Resolución hasta ms."""
    now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _is_secret_field(name: str) -> bool:
    lower = name.lower()
    return any(lower.startswith(p) for p in _SECRET_PREFIXES)


def _redact(fields: dict) -> dict:
    """Devuelve copia con campos sensibles enmascarados."""
    out = {}
    for k, v in fields.items():
        if _is_secret_field(k) and v not in (None, "", 0):
            out[k] = "***"
        else:
            out[k] = v
    return out


def _format_value(v: Any) -> str:
    """Convierte valor a string compacto para modo text."""
    if v is None:
        return ""
    if isinstance(v, str):
        # Si contiene espacios o caracteres delicados, lo encerramos.
        if any(c in v for c in (" ", "\t", "\n", '"', "=")):
            return _json.dumps(v, ensure_ascii=False)
        return v
    if isinstance(v, (int, float, bool)):
        return str(v).lower() if isinstance(v, bool) else str(v)
    return _json.dumps(v, ensure_ascii=False, default=str)


def _current_format() -> str:
    return (_os.environ.get("AMI_LOG_FORMAT") or "text").lower()


def _current_min_level() -> int:
    lv = (_os.environ.get("AMI_LOG_LEVEL") or "info").lower()
    return _LEVELS.get(lv, _LEVELS["info"])


def _emit(level: str, event: str, fields: dict) -> None:
    if _LEVELS[level] < _current_min_level():
        return

    safe = _redact(fields)
    stream = _sys.stdout if level == "info" else _sys.stderr

    if _current_format() == "json":
        record = {"ts": _now_iso(), "level": level, "event": event}
        # Si el usuario pasa ts/level/event como kwargs ganan los del envoltorio.
        for k, v in safe.items():
            if k in record:
                continue
            record[k] = v
        try:
            line = _json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # Fallback robusto: convertir todo a str si algo no es serializable.
            line = _json.dumps(
                {k: str(v) for k, v in record.items()}, ensure_ascii=False
            )
        print(line, file=stream, flush=True)
        return

    # Text mode (dev).
    parts = [f"{_now_iso()} [{level}] {event}"]
    if safe:
        kv = " ".join(f"{k}={_format_value(v)}" for k, v in safe.items())
        parts.append("| " + kv)
    print(" ".join(parts), file=stream, flush=True)


def info(event: str, **fields: Any) -> None:
    """Loguea un evento informativo. stdout."""
    _emit("info", event, fields)


def warn(event: str, **fields: Any) -> None:
    """Loguea un warning. stderr."""
    _emit("warn", event, fields)


def error(event: str, **fields: Any) -> None:
    """Loguea un error. stderr. No lanza — el caller decide el flujo."""
    _emit("error", event, fields)
