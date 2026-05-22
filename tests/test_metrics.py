"""Tests del módulo de observabilidad (`ami_metrics`).

Cubre el subset de formato Prometheus exposition que implementamos a mano,
más el helper de logging JSON-line y el endpoint público `/metrics`.
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading

import pytest

import ami_metrics


# ---------------------------------------------------------------- Counter

def test_counter_increment_and_exposition():
    c = ami_metrics.Counter("test_hits_total", "Hits counted in the test", ["route"])
    c.inc(route="/a")
    c.inc(route="/a")
    c.inc(amount=3, route="/b")

    lines = c.collect()
    body = "\n".join(lines)
    assert "# HELP test_hits_total Hits counted in the test" in body
    assert "# TYPE test_hits_total counter" in body
    assert 'test_hits_total{route="/a"} 2' in body
    assert 'test_hits_total{route="/b"} 3' in body


def test_counter_rejects_negative_increment():
    c = ami_metrics.Counter("test_neg_total", "neg", ["k"])
    with pytest.raises(ValueError):
        c.inc(amount=-1, k="x")


def test_counter_rejects_label_mismatch():
    c = ami_metrics.Counter("test_mismatch_total", "mismatch", ["a", "b"])
    with pytest.raises(ValueError):
        c.inc(a="1")  # falta 'b'
    with pytest.raises(ValueError):
        c.inc(a="1", b="2", c="3")  # sobra 'c'


# ---------------------------------------------------------------- Histogram

def test_histogram_observe_buckets_and_aggregates():
    h = ami_metrics.Histogram(
        "test_latency_seconds", "latency", ["path"],
        buckets=(0.1, 0.5, 1.0, 2.0),
    )
    for v in (0.05, 0.2, 0.4, 0.9, 1.5, 3.0):
        h.observe(v, path="/x")

    body = "\n".join(h.collect())
    # le="0.1" captura solo 0.05 → 1
    assert 'test_latency_seconds_bucket{path="/x",le="0.1"} 1' in body
    # le="0.5" captura 0.05, 0.2, 0.4 → 3
    assert 'test_latency_seconds_bucket{path="/x",le="0.5"} 3' in body
    # le="1" captura 0.05, 0.2, 0.4, 0.9 → 4
    assert 'test_latency_seconds_bucket{path="/x",le="1"} 4' in body
    # le="2" captura 5 valores (sin contar el 3.0)
    assert 'test_latency_seconds_bucket{path="/x",le="2"} 5' in body
    # +Inf siempre captura todo
    assert 'test_latency_seconds_bucket{path="/x",le="+Inf"} 6' in body
    # count y sum agregados
    assert 'test_latency_seconds_count{path="/x"} 6' in body
    assert re.search(
        r'test_latency_seconds_sum\{path="/x"\} [\d.]+', body
    ), "sum line missing"


def test_histogram_requires_at_least_one_bucket():
    with pytest.raises(ValueError):
        ami_metrics.Histogram("bad", "bad", [], buckets=[])


# ---------------------------------------------------------------- Gauge

def test_gauge_set_and_emit_zero_when_unlabeled_empty():
    g = ami_metrics.Gauge("test_active", "active widgets", [])
    body = "\n".join(g.collect())
    # Sin set(), una gauge sin labels debe emitir 0 (evita "absent" en PromQL).
    assert "test_active 0" in body

    g.set(7)
    body = "\n".join(g.collect())
    assert "test_active 7" in body

    g.inc()
    g.inc(amount=2)
    g.dec()
    body = "\n".join(g.collect())
    assert "test_active 9" in body


def test_gauge_with_labels_does_not_emit_default_zero():
    g = ami_metrics.Gauge("test_pool", "pool size", ["region"])
    body = "\n".join(g.collect())
    # Sin valores y con labels, solo deberíamos ver HELP + TYPE (no muestras).
    assert "# HELP test_pool pool size" in body
    assert "# TYPE test_pool gauge" in body
    assert "test_pool{" not in body


# ---------------------------------------------------------- Registry / format

def test_registry_render_includes_all_metrics_in_prometheus_format():
    reg = ami_metrics.Registry()
    c = reg.register(ami_metrics.Counter("foo_total", "foo", ["k"]))
    g = reg.register(ami_metrics.Gauge("bar_value", "bar", []))
    c.inc(k="v")
    g.set(42)

    body = reg.render()
    # Termina en newline (Prometheus exige newline final).
    assert body.endswith("\n")

    # Estructura: cada métrica con su bloque HELP/TYPE seguido de sample(s).
    # Verificamos con regex que reconoce el shape canónico.
    sample_re = re.compile(
        r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? (\d+(\.\d+)?|\+Inf|-Inf|NaN)$'
    )
    help_re = re.compile(r"^# HELP [a-zA-Z_:][a-zA-Z0-9_:]* .+$")
    type_re = re.compile(r"^# TYPE [a-zA-Z_:][a-zA-Z0-9_:]* (counter|gauge|histogram|summary)$")

    saw_help = saw_type = saw_sample = False
    for line in body.splitlines():
        if not line:
            continue
        if help_re.match(line):
            saw_help = True
        elif type_re.match(line):
            saw_type = True
        elif sample_re.match(line):
            saw_sample = True
        else:
            pytest.fail(f"Unparseable Prometheus line: {line!r}")
    assert saw_help and saw_type and saw_sample


def test_singleton_registry_lists_default_metrics():
    body = ami_metrics.REGISTRY.render()
    for name in (
        "ami_requests_total",
        "ami_request_duration_seconds",
        "ami_sms_sent_total",
        "ami_calls_placed_total",
        "ami_webhooks_delivered_total",
        "ami_active_mids",
    ):
        assert f"# HELP {name}" in body, f"missing HELP for {name}"
        assert f"# TYPE {name}" in body, f"missing TYPE for {name}"


# ------------------------------------------------------------ Concurrencia

def test_counter_is_thread_safe_under_concurrent_increments():
    c = ami_metrics.Counter("test_concurrent_total", "concurrent", ["k"])
    threads = []
    increments_per_thread = 1000

    def worker():
        for _ in range(increments_per_thread):
            c.inc(k="x")

    for _ in range(100):
        threads.append(threading.Thread(target=worker))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    body = "\n".join(c.collect())
    expected = 100 * increments_per_thread
    assert f'test_concurrent_total{{k="x"}} {expected}' in body


# ----------------------------------------------------------------- Logging

def test_log_json_emits_parseable_line(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    ami_metrics.log_info("sms_sent", mid="mid_abc", to="+34600111222", status="queued")
    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["level"] == "info"
    assert parsed["event"] == "sms_sent"
    assert parsed["mid"] == "mid_abc"
    assert parsed["to"] == "+34600111222"
    assert parsed["status"] == "queued"
    assert isinstance(parsed["ts"], (int, float))


def test_log_json_levels_emit_correct_level(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    ami_metrics.log_warn("rate_limit_close", remaining=3)
    ami_metrics.log_error("webhook_failed", url="https://example.test", code=503)
    lines = [json.loads(ln) for ln in buf.getvalue().strip().splitlines()]
    assert lines[0]["level"] == "warn"
    assert lines[0]["event"] == "rate_limit_close"
    assert lines[1]["level"] == "error"
    assert lines[1]["code"] == 503


def test_log_json_handles_non_serializable_fields_gracefully(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    class Opaque:
        pass

    # `default=str` debería serializar la clase a su repr en vez de romper.
    ami_metrics.log_info("opaque_field", obj=Opaque())
    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["event"] == "opaque_field"
    assert "obj" in parsed


# --------------------------------------------------------------- HTTP endpoint

def test_metrics_endpoint_returns_prometheus_format(anon_client):
    r = anon_client.get("/metrics")
    assert r.status_code == 200
    ctype = r.headers.get("content-type", "")
    assert "text/plain" in ctype
    assert "version=0.0.4" in ctype
    body = r.text
    assert body, "metrics body must not be empty"
    assert "# HELP ami_requests_total" in body
    assert "# TYPE ami_requests_total counter" in body
    assert "# TYPE ami_request_duration_seconds histogram" in body
    assert body.endswith("\n")


def test_metrics_endpoint_is_public(anon_client):
    # Sin Authorization el endpoint debe responder 200 (Prometheus scrape).
    r = anon_client.get("/metrics")
    assert r.status_code == 200
