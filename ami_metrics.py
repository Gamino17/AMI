"""Observabilidad de AMI: métricas Prometheus + structured logging.

Stdlib pura, sin dependencias externas. Implementa el subset del formato
Prometheus text exposition (counter, histogram, gauge) que necesitamos para
exponer `/metrics`, más helpers de logging JSON-line para que cualquier
pipeline (Datadog, Loki, etc.) pueda ingerir los eventos del servidor.

El módulo expone singletons globales (`REGISTRY`, `REQUESTS`, `LATENCY`,
`SMS_SENT`, `CALLS_PLACED`, `WEBHOOKS_DELIVERED`, `ACTIVE_MIDS`) listos para
que `ami_api.py` los importe y los conecte en los puntos calientes.

Diseño:
- Thread-safe: cada métrica protege sus diccionarios con `threading.Lock`
  porque el `ThreadingHTTPServer` sirve requests en paralelo.
- Labels: tuplas ordenadas (`tuple(values for name in label_names)`) como
  clave del dict interno; eso da exposición determinista (orden estable) sin
  pagar el coste de `sorted()` en cada hit.
- Sin allocations en el camino caliente: `inc()` y `observe()` solo tocan
  enteros/floats en el dict. La concatenación de strings ocurre en `render()`,
  que solo se llama cuando Prometheus rasca.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from typing import Iterable


# Buckets por defecto del histograma de latencia HTTP (segundos). Cubren desde
# los 5 ms (endpoints sincrónicos) hasta los 10 s (calls largas con telco real).
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape_label_value(v: str) -> str:
    """Escape mínimo según la spec de Prometheus text exposition.

    Solo aplica a *valores* de label, no a nombres. Los nombres ya deben ser
    `[a-zA-Z_][a-zA-Z0-9_]*` por construcción nuestra.
    """
    return (
        str(v)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _format_labels(label_names: Iterable[str], label_values: Iterable[str]) -> str:
    """Devuelve `{name="val",name2="val2"}` o `""` si no hay labels."""
    pairs = [
        f'{name}="{_escape_label_value(value)}"'
        for name, value in zip(label_names, label_values)
    ]
    if not pairs:
        return ""
    return "{" + ",".join(pairs) + "}"


def _format_number(x: float) -> str:
    """Prometheus exige `+Inf` literal y formato consistente para floats."""
    if math.isinf(x):
        return "+Inf" if x > 0 else "-Inf"
    if math.isnan(x):
        return "NaN"
    # Enteros como enteros (los counters lo agradecen visualmente).
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return repr(float(x))


class _MetricBase:
    """Estado compartido por Counter / Histogram / Gauge: nombre, help, labels."""

    def __init__(self, name: str, help_text: str, label_names: list[str]):
        self.name = name
        self.help_text = help_text
        self.label_names = tuple(label_names or [])
        self._lock = threading.Lock()

    def _labels_key(self, labels: dict) -> tuple:
        """Resuelve un dict de labels a la tupla ordenada por label_names."""
        if set(labels.keys()) != set(self.label_names):
            missing = set(self.label_names) - set(labels.keys())
            extra = set(labels.keys()) - set(self.label_names)
            raise ValueError(
                f"label mismatch for metric {self.name!r}: "
                f"missing={sorted(missing)} extra={sorted(extra)}"
            )
        return tuple(str(labels[n]) for n in self.label_names)


class Counter(_MetricBase):
    """Counter monotónico (solo crece). Útil para conteos acumulados."""

    def __init__(self, name: str, help_text: str, label_names: list[str]):
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple, float] = {}

    def inc(self, amount: float = 1.0, **labels) -> None:
        if amount < 0:
            raise ValueError("Counter.inc requires a non-negative amount")
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def collect(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            snapshot = list(self._values.items())
        for key, value in snapshot:
            lines.append(
                f"{self.name}{_format_labels(self.label_names, key)} {_format_number(value)}"
            )
        return lines


class Histogram(_MetricBase):
    """Histograma con buckets acumulativos (formato Prometheus clásico).

    Cada `observe(v)` incrementa todos los buckets cuyo `le >= v`, más el
    bucket implícito `+Inf`, y suma `v` al total. Eso permite calcular
    cuantiles aproximados con `histogram_quantile()` en PromQL.
    """

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: list[str],
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ):
        super().__init__(name, help_text, label_names)
        # Garantizamos orden estricto y dedupe.
        bs = sorted(set(float(b) for b in buckets))
        if not bs:
            raise ValueError("Histogram requires at least one bucket")
        self.buckets = tuple(bs)
        # Por serie de labels: {"counts": [c0, c1, ...], "sum": s, "count": n}
        self._series: dict[tuple, dict] = {}

    def observe(self, value: float, **labels) -> None:
        key = self._labels_key(labels)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = {"counts": [0] * len(self.buckets), "sum": 0.0, "count": 0}
                self._series[key] = s
            for i, b in enumerate(self.buckets):
                if value <= b:
                    s["counts"][i] += 1
            s["sum"] += float(value)
            s["count"] += 1

    def collect(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            snapshot = [(key, dict(series, counts=list(series["counts"])))
                        for key, series in self._series.items()]
        for key, series in snapshot:
            base_labels = list(zip(self.label_names, key))
            for i, b in enumerate(self.buckets):
                labels_with_le = base_labels + [("le", _format_number(b))]
                names = [n for n, _ in labels_with_le]
                values = [v for _, v in labels_with_le]
                lines.append(
                    f"{self.name}_bucket{_format_labels(names, values)} "
                    f"{_format_number(series['counts'][i])}"
                )
            # Bucket +Inf obligatorio.
            labels_with_inf = base_labels + [("le", "+Inf")]
            names = [n for n, _ in labels_with_inf]
            values = [v for _, v in labels_with_inf]
            lines.append(
                f"{self.name}_bucket{_format_labels(names, values)} "
                f"{_format_number(series['count'])}"
            )
            lines.append(
                f"{self.name}_sum{_format_labels(self.label_names, key)} "
                f"{_format_number(series['sum'])}"
            )
            lines.append(
                f"{self.name}_count{_format_labels(self.label_names, key)} "
                f"{_format_number(series['count'])}"
            )
        return lines


class Gauge(_MetricBase):
    """Valor instantáneo que puede subir o bajar (active connections, etc.)."""

    def __init__(self, name: str, help_text: str, label_names: list[str]):
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple, float] = {}

    def set(self, value: float, **labels) -> None:
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels) -> None:
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels) -> None:
        self.inc(-amount, **labels)

    def collect(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            snapshot = list(self._values.items())
        # Si la gauge no tiene labels y nunca se ha seteado, emitimos 0 para
        # que el scrape no se quede sin sample (clásico edge case con
        # Prometheus: una serie nueva sin datos no aparece en absent()).
        if not snapshot and not self.label_names:
            snapshot = [((), 0.0)]
        for key, value in snapshot:
            lines.append(
                f"{self.name}{_format_labels(self.label_names, key)} {_format_number(value)}"
            )
        return lines


class Registry:
    """Coordina varias métricas y las renderiza en formato exposition."""

    def __init__(self):
        self.metrics: list[_MetricBase] = []
        self._lock = threading.Lock()

    def register(self, metric: _MetricBase) -> _MetricBase:
        with self._lock:
            self.metrics.append(metric)
        return metric

    def unregister_all(self) -> None:
        """Útil para tests; no se usa en runtime."""
        with self._lock:
            self.metrics.clear()

    def render(self) -> str:
        with self._lock:
            metrics = list(self.metrics)
        out: list[str] = []
        for m in metrics:
            out.extend(m.collect())
        # Prometheus exige una línea en blanco final (newline al cierre).
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------- Singletons

REGISTRY = Registry()

REQUESTS = REGISTRY.register(Counter(
    "ami_requests_total",
    "Total HTTP requests by method, path template, and status code",
    ["method", "path", "status"],
))

LATENCY = REGISTRY.register(Histogram(
    "ami_request_duration_seconds",
    "HTTP request latency in seconds by method and path template",
    ["method", "path"],
))

SMS_SENT = REGISTRY.register(Counter(
    "ami_sms_sent_total",
    "SMS messages sent through the protocol, by final status",
    ["status"],
))

CALLS_PLACED = REGISTRY.register(Counter(
    "ami_calls_placed_total",
    "Voice calls placed through the protocol, by final status",
    ["status"],
))

WEBHOOKS_DELIVERED = REGISTRY.register(Counter(
    "ami_webhooks_delivered_total",
    "Outbound webhook delivery attempts, by result",
    ["result"],
))

ACTIVE_MIDS = REGISTRY.register(Gauge(
    "ami_active_mids",
    "Currently active MobileIdentities in the in-memory state",
    [],
))


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# --------------------------------------------------------- Structured logging
#
# JSON-line logging: una línea = un evento, parseable por jq / Loki / Datadog.
# Va a stdout porque eso es lo que captura el runtime de Render / Docker.

_LOG_LOCK = threading.Lock()


def log_json(level: str, event: str, **fields) -> None:
    """Emite una línea JSON a stdout con ts, level, event y campos extra.

    El lock global serializa la escritura para que dos threads no entrelacen
    bytes de líneas distintas (stdout no es atómico en bloques largos).
    """
    rec = {"ts": time.time(), "level": level, "event": event}
    # Mergeamos al final para que el caller no pueda romper los campos base.
    rec.update(fields)
    try:
        line = json.dumps(rec, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        # Último recurso: dropea los campos problemáticos antes que romper.
        line = json.dumps(
            {"ts": rec["ts"], "level": level, "event": event,
             "_log_error": "fields not serializable"},
            ensure_ascii=False,
        )
    with _LOG_LOCK:
        print(line, file=sys.stdout, flush=True)


def log_info(event: str, **fields) -> None:
    log_json("info", event, **fields)


def log_warn(event: str, **fields) -> None:
    log_json("warn", event, **fields)


def log_error(event: str, **fields) -> None:
    log_json("error", event, **fields)
