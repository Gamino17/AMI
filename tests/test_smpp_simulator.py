"""Tests del SMPP simulator local — sin Kannel ni partner real.

Estos tests arrancan el simulador en un puerto efímero, abren un socket TCP,
hablan SMPP 3.4 binario a mano y validan que los PDU de respuesta son los
esperados. La idea es proteger la implementación contra regresiones cuando
toquemos el formato del DLR, el secuenciado, o los modos --fail-rate / --no-dlr.
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import time
from typing import Optional, Tuple

import pytest


# Make `infra/smpp_simulator` importable como módulo.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIM_DIR = os.path.join(_PROJECT_ROOT, "infra", "smpp_simulator")
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

import smpp_simulator as sim_mod  # noqa: E402


# ---------------------------------------------------------------------------
# helpers para hablar SMPP 3.4 contra el simulador
# ---------------------------------------------------------------------------

HEADER_LEN = sim_mod.HEADER_LEN


def _connect(port: int, timeout: float = 5.0) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", port))
    return s


def _send_pdu(sock: socket.socket, command_id: int, seq: int, body: bytes,
              status: int = 0) -> None:
    sock.sendall(sim_mod.pack_pdu(command_id, status, seq, body))


def _recv_pdu(sock: socket.socket) -> Tuple[int, int, int, bytes]:
    pdu = sim_mod.recv_pdu(sock)
    assert pdu is not None, "expected a PDU, got EOF"
    return pdu


def _bind_transceiver_body(system_id: str, password: str) -> bytes:
    return (
        sim_mod.cstring(system_id)
        + sim_mod.cstring(password)
        + sim_mod.cstring("")          # system_type
        + b"\x34"                       # interface_version 3.4
        + b"\x00"                       # addr_ton
        + b"\x00"                       # addr_npi
        + sim_mod.cstring("")          # address_range
    )


def _submit_sm_body(source: str, destination: str, text: str) -> bytes:
    sm = text.encode("latin-1", errors="replace")
    return (
        sim_mod.cstring("")            # service_type
        + bytes([1, 1])                 # source_addr_ton/npi
        + sim_mod.cstring(source)
        + bytes([1, 1])                 # dest_addr_ton/npi
        + sim_mod.cstring(destination)
        + b"\x00"                       # esm_class
        + b"\x00"                       # protocol_id
        + b"\x00"                       # priority_flag
        + sim_mod.cstring("")          # schedule_delivery_time
        + sim_mod.cstring("")          # validity_period
        + b"\x01"                       # registered_delivery (queremos DLR)
        + b"\x00"                       # replace_if_present_flag
        + b"\x00"                       # data_coding
        + b"\x00"                       # sm_default_msg_id
        + bytes([len(sm)])              # sm_length
        + sm                            # short_message
    )


def _await_pdu_of(sock: socket.socket, command_id: int,
                  timeout: float = 2.0) -> Tuple[int, int, int, bytes]:
    """Drena PDUs hasta encontrar uno con command_id == esperado."""
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"timed out waiting for command_id=0x{command_id:08x}")
        sock.settimeout(remaining)
        cmd, status, seq, body = _recv_pdu(sock)
        if cmd == command_id:
            return cmd, status, seq, body


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simulator():
    s = sim_mod.SmppSimulator(host="127.0.0.1", port=0)
    port = s.start()
    try:
        yield s, port
    finally:
        s.stop()


@pytest.fixture
def simulator_no_dlr():
    s = sim_mod.SmppSimulator(host="127.0.0.1", port=0, send_dlr=False)
    port = s.start()
    try:
        yield s, port
    finally:
        s.stop()


@pytest.fixture
def simulator_all_fail():
    s = sim_mod.SmppSimulator(host="127.0.0.1", port=0, fail_rate=1.0, dlr_delay=0.05)
    port = s.start()
    try:
        yield s, port
    finally:
        s.stop()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_bind_transceiver_responds_ok_with_simulator_system_id(simulator):
    _, port = simulator
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("test_user", "test_pw"))
        cmd, status, seq, body = _recv_pdu(sock)
        assert cmd == sim_mod.BIND_TRANSCEIVER_RESP
        assert status == sim_mod.ESME_ROK
        assert seq == 1
        system_id, _ = sim_mod.read_cstring(body, 0)
        assert system_id == sim_mod.SIMULATOR_SYSTEM_ID
    finally:
        sock.close()


def test_enquire_link_roundtrip(simulator):
    _, port = simulator
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("u", "p"))
        _recv_pdu(sock)  # bind resp

        _send_pdu(sock, sim_mod.ENQUIRE_LINK, seq=42, body=b"")
        cmd, status, seq, _body = _recv_pdu(sock)
        assert cmd == sim_mod.ENQUIRE_LINK_RESP
        assert status == sim_mod.ESME_ROK
        assert seq == 42
    finally:
        sock.close()


def test_submit_sm_returns_message_id_and_dlr_delivered(simulator):
    _, port = simulator
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("u", "p"))
        _recv_pdu(sock)

        _send_pdu(sock, sim_mod.SUBMIT_SM, seq=7,
                  body=_submit_sm_body("AMI", "+34600111222", "Hello"))

        # SUBMIT_SM_RESP: status OK, body contains message_id (c-string).
        cmd, status, seq, body = _await_pdu_of(sock, sim_mod.SUBMIT_SM_RESP, timeout=2.0)
        assert status == sim_mod.ESME_ROK
        assert seq == 7
        message_id, _ = sim_mod.read_cstring(body, 0)
        assert message_id and len(message_id) >= 4

        # DELIVER_SM con el DLR. El simulador asigna su propio sequence_number.
        cmd, status, _seq, body = _await_pdu_of(sock, sim_mod.DELIVER_SM, timeout=2.0)
        assert status == sim_mod.ESME_ROK
        submit = sim_mod.parse_submit_sm(body)
        # En el DLR los addresses se invierten: source <- destination original.
        assert submit.source_addr == "+34600111222"
        assert submit.destination_addr == "AMI"
        # esm_class = 0x04 => MC Delivery Receipt.
        assert submit.esm_class == 0x04
        dlr = sim_mod.parse_dlr_text(submit.short_message.decode("latin-1"))
        assert dlr["id"] == message_id
        assert dlr["stat"] == "DELIVRD"
        assert dlr["err"] == "000"
    finally:
        sock.close()


def test_submit_sm_with_fail_rate_one_returns_undeliv_dlr(simulator_all_fail):
    _, port = simulator_all_fail
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("u", "p"))
        _recv_pdu(sock)

        _send_pdu(sock, sim_mod.SUBMIT_SM, seq=2,
                  body=_submit_sm_body("AMI", "+34600111222", "Hello"))
        _await_pdu_of(sock, sim_mod.SUBMIT_SM_RESP)

        _cmd, _st, _seq, body = _await_pdu_of(sock, sim_mod.DELIVER_SM, timeout=2.0)
        submit = sim_mod.parse_submit_sm(body)
        dlr = sim_mod.parse_dlr_text(submit.short_message.decode("latin-1"))
        assert dlr["stat"] == "UNDELIV"
        assert dlr["err"] == "001"
    finally:
        sock.close()


def test_no_dlr_mode_does_not_send_deliver_sm(simulator_no_dlr):
    sim, port = simulator_no_dlr
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("u", "p"))
        _recv_pdu(sock)

        _send_pdu(sock, sim_mod.SUBMIT_SM, seq=3,
                  body=_submit_sm_body("AMI", "+34600999000", "Hi"))
        # Esperamos sólo el SUBMIT_SM_RESP.
        cmd, status, _seq, _body = _await_pdu_of(sock, sim_mod.SUBMIT_SM_RESP)
        assert status == sim_mod.ESME_ROK

        # Y NINGÚN DELIVER_SM en una ventana razonable.
        sock.settimeout(0.4)
        try:
            extra = sim_mod.recv_pdu(sock)
        except socket.timeout:
            extra = "timeout"
        assert extra == "timeout", f"expected no further PDU, got {extra!r}"
    finally:
        sock.close()


def test_unbind_closes_session_cleanly(simulator):
    _, port = simulator
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("u", "p"))
        _recv_pdu(sock)

        _send_pdu(sock, sim_mod.UNBIND, seq=9, body=b"")
        cmd, status, seq, _body = _recv_pdu(sock)
        assert cmd == sim_mod.UNBIND_RESP
        assert status == sim_mod.ESME_ROK
        assert seq == 9

        # Tras UNBIND_RESP la sesión se cierra; el siguiente recv devuelve EOF.
        sock.settimeout(1.0)
        # Puede llegar b"" (EOF limpio) o lanzar OSError según timing/SO.
        try:
            extra = sock.recv(16)
        except OSError:
            extra = b""
        assert extra == b""
    finally:
        sock.close()


def test_inject_mo_reaches_bound_session(simulator):
    sim, port = simulator
    sock = _connect(port)
    try:
        _send_pdu(sock, sim_mod.BIND_TRANSCEIVER, seq=1,
                  body=_bind_transceiver_body("u", "p"))
        _recv_pdu(sock)

        # Damos al simulador un instante para registrar la sesión como bound.
        deadline = time.monotonic() + 1.0
        delivered = False
        while time.monotonic() < deadline:
            if sim.inject_mo(source="+34600111222", destination="AMI", text="ping"):
                delivered = True
                break
            time.sleep(0.02)
        assert delivered, "inject_mo did not find a bound session"

        cmd, status, _seq, body = _await_pdu_of(sock, sim_mod.DELIVER_SM, timeout=2.0)
        assert status == sim_mod.ESME_ROK
        submit = sim_mod.parse_submit_sm(body)
        assert submit.source_addr == "+34600111222"
        assert submit.destination_addr == "AMI"
        # MO normal, NO DLR -> esm_class == 0
        assert submit.esm_class == 0x00
        assert submit.short_message == b"ping"
    finally:
        sock.close()


def test_parse_dlr_text_roundtrip_through_build_dlr_text():
    text = sim_mod.build_dlr_text("abc123", delivered=True)
    parsed = sim_mod.parse_dlr_text(text)
    assert parsed["id"] == "abc123"
    assert parsed["sub"] == "001"
    assert parsed["dlvrd"] == "001"
    assert parsed["stat"] == "DELIVRD"
    assert parsed["err"] == "000"
    assert "submit_date" in parsed and len(parsed["submit_date"]) == 10
    assert "done_date" in parsed and len(parsed["done_date"]) == 10

    text_fail = sim_mod.build_dlr_text("xyz", delivered=False)
    parsed_fail = sim_mod.parse_dlr_text(text_fail)
    assert parsed_fail["stat"] == "UNDELIV"
    assert parsed_fail["err"] == "001"
    assert parsed_fail["dlvrd"] == "000"


def test_cli_help_lists_main_flags(capsys):
    parser = sim_mod._build_arg_parser()
    help_text = parser.format_help()
    for flag in ("--port", "--fail-rate", "--no-dlr", "--host", "--dlr-delay"):
        assert flag in help_text, f"flag {flag} missing from --help"
