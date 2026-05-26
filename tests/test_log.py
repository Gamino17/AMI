"""Tests del logger estructurado `ami_log`.

Cubrimos:
- Modo JSON: cada call produce 1 línea JSON parseable con ts/level/event y
  los kwargs.
- Modo text: la línea es legible (timestamp, [level], event, k=v).
- Redacción de secretos: campos cuyo nombre empieza por password/secret/
  token/api_key se enmascaran como "***".
- Routing: info → stdout, warn/error → stderr.
- Filtro AMI_LOG_LEVEL: warn descarta info, error descarta info+warn.
"""
from __future__ import annotations

import importlib
import json
import os

import pytest


@pytest.fixture
def ami_log_json(monkeypatch):
    monkeypatch.setenv("AMI_LOG_FORMAT", "json")
    monkeypatch.delenv("AMI_LOG_LEVEL", raising=False)
    import ami_log
    return importlib.reload(ami_log)


@pytest.fixture
def ami_log_text(monkeypatch):
    monkeypatch.setenv("AMI_LOG_FORMAT", "text")
    monkeypatch.delenv("AMI_LOG_LEVEL", raising=False)
    import ami_log
    return importlib.reload(ami_log)


def _last_line(text: str) -> str:
    return [ln for ln in text.splitlines() if ln.strip()][-1]


def test_info_json_basic(ami_log_json, capsys):
    ami_log_json.info("http_request", method="POST", path="/v1/sim-requests", status=201, ip="127.0.0.1")
    captured = capsys.readouterr()
    assert captured.err == ""
    record = json.loads(_last_line(captured.out))
    assert record["level"] == "info"
    assert record["event"] == "http_request"
    assert record["method"] == "POST"
    assert record["path"] == "/v1/sim-requests"
    assert record["status"] == 201
    assert record["ip"] == "127.0.0.1"
    assert "ts" in record
    # ts es ISO-8601 UTC con Z
    assert record["ts"].endswith("Z")


def test_warn_json_to_stderr(ami_log_json, capsys):
    ami_log_json.warn("auth_disabled", reason="ami_api_key_not_set")
    captured = capsys.readouterr()
    assert captured.out == ""
    record = json.loads(_last_line(captured.err))
    assert record["level"] == "warn"
    assert record["event"] == "auth_disabled"
    assert record["reason"] == "ami_api_key_not_set"


def test_error_json_to_stderr(ami_log_json, capsys):
    ami_log_json.error("backup_failed", error="disk full", exc_type="OSError")
    captured = capsys.readouterr()
    assert captured.out == ""
    record = json.loads(_last_line(captured.err))
    assert record["level"] == "error"
    assert record["event"] == "backup_failed"
    assert record["error"] == "disk full"
    assert record["exc_type"] == "OSError"


def test_json_redacts_secrets(ami_log_json, capsys):
    ami_log_json.info(
        "login_attempt",
        user="ada",
        password="supersecret",
        api_key="ak_live_xxx",
        token="tok_abc",
        secret_value="nope",
        # Campo no sensible que contiene "password" en mitad → NO se enmascara
        password_hint_for_user="contiene mayúsculas",
    )
    captured = capsys.readouterr()
    record = json.loads(_last_line(captured.out))
    assert record["user"] == "ada"
    assert record["password"] == "***"
    assert record["api_key"] == "***"
    assert record["token"] == "***"
    assert record["secret_value"] == "***"
    # Empieza por "password" → también se enmascara (prefijo)
    assert record["password_hint_for_user"] == "***"


def test_redaction_skips_empty_values(ami_log_json, capsys):
    """Si el secreto está vacío/None, no nos molestamos en enmascarar — más útil
    para debugging (ves que el caller pasó vacío en lugar de un ***)."""
    ami_log_json.info("login", password="", token=None)
    captured = capsys.readouterr()
    record = json.loads(_last_line(captured.out))
    assert record["password"] == ""
    assert record["token"] is None


def test_text_mode_readable(ami_log_text, capsys):
    ami_log_text.info("http_request", method="GET", path="/health", status=200)
    captured = capsys.readouterr()
    line = _last_line(captured.out)
    assert "[info]" in line
    assert "http_request" in line
    assert "method=GET" in line
    assert "path=/health" in line
    assert "status=200" in line
    # No es JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_text_mode_redacts(ami_log_text, capsys):
    ami_log_text.info("login", user="ada", password="leak")
    captured = capsys.readouterr()
    line = _last_line(captured.out)
    assert "user=ada" in line
    assert "password=***" in line
    assert "leak" not in line


def test_text_mode_quotes_values_with_spaces(ami_log_text, capsys):
    ami_log_text.info("kyc_submitted", note="multiple words here")
    captured = capsys.readouterr()
    line = _last_line(captured.out)
    assert '"multiple words here"' in line


def test_default_format_is_text(monkeypatch, capsys):
    """Sin AMI_LOG_FORMAT seteada el default es text (dev-friendly)."""
    monkeypatch.delenv("AMI_LOG_FORMAT", raising=False)
    monkeypatch.delenv("AMI_LOG_LEVEL", raising=False)
    import ami_log
    importlib.reload(ami_log)
    ami_log.info("boot", port=8000)
    captured = capsys.readouterr()
    line = _last_line(captured.out)
    assert "[info]" in line
    assert "port=8000" in line


def test_level_filter_warn(monkeypatch, capsys):
    monkeypatch.setenv("AMI_LOG_FORMAT", "json")
    monkeypatch.setenv("AMI_LOG_LEVEL", "warn")
    import ami_log
    importlib.reload(ami_log)
    ami_log.info("ignored", x=1)
    ami_log.warn("kept", x=2)
    ami_log.error("kept2", x=3)
    captured = capsys.readouterr()
    assert captured.out == ""  # info filtrado
    err_lines = [ln for ln in captured.err.splitlines() if ln.strip()]
    assert len(err_lines) == 2
    assert json.loads(err_lines[0])["event"] == "kept"
    assert json.loads(err_lines[1])["event"] == "kept2"


def test_level_filter_error(monkeypatch, capsys):
    monkeypatch.setenv("AMI_LOG_FORMAT", "json")
    monkeypatch.setenv("AMI_LOG_LEVEL", "error")
    import ami_log
    importlib.reload(ami_log)
    ami_log.info("nope1")
    ami_log.warn("nope2")
    ami_log.error("yes")
    captured = capsys.readouterr()
    assert captured.out == ""
    err_lines = [ln for ln in captured.err.splitlines() if ln.strip()]
    assert len(err_lines) == 1
    assert json.loads(err_lines[0])["event"] == "yes"


def test_unserializable_field_falls_back_to_string(ami_log_json, capsys):
    class Weird:
        def __repr__(self):
            return "<Weird>"

    ami_log_json.info("weirdness", obj=Weird())
    captured = capsys.readouterr()
    record = json.loads(_last_line(captured.out))
    # default=str en json.dumps lo cubre sin romper la línea.
    assert "Weird" in record["obj"]


def test_no_kwargs_emits_event_only(ami_log_json, capsys):
    ami_log_json.info("ping")
    captured = capsys.readouterr()
    record = json.loads(_last_line(captured.out))
    assert record["event"] == "ping"
    assert record["level"] == "info"
    assert set(record.keys()) == {"ts", "level", "event"}


def test_env_var_picked_up_per_call(monkeypatch, capsys):
    """El formato se lee en cada call, no se cachea. Útil para tests."""
    monkeypatch.setenv("AMI_LOG_FORMAT", "json")
    import ami_log
    importlib.reload(ami_log)
    ami_log.info("first")
    captured = capsys.readouterr()
    assert _last_line(captured.out).startswith("{")

    monkeypatch.setenv("AMI_LOG_FORMAT", "text")
    ami_log.info("second")
    captured = capsys.readouterr()
    line = _last_line(captured.out)
    assert "[info] second" in line
    assert not line.startswith("{")
