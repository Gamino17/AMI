"""SMPP 3.4 simulator — un SMSC partner de juguete, en stdlib pura.

Sirve para probar la stack SMS de AMI end-to-end (Kannel → SMSC → DLR) sin
necesitar al partner telco real. Kannel hace BIND_TRANSCEIVER contra este
proceso, le envía SUBMIT_SM, y el simulador responde con SUBMIT_SM_RESP y
(opcionalmente) un DELIVER_SM con el DLR.

Reglas:
- Solo stdlib: socket, struct, threading, time, argparse, logging, secrets.
- SMPP 3.4 binario (https://smpp.org/smpp-3-4.html), implementación mínima
  pero correcta de los PDUs que Kannel usa en un trunk transceiver normal.

Uso:
    python smpp_simulator.py --port 2775
    python smpp_simulator.py --fail-rate 0.3
    python smpp_simulator.py --no-dlr

Comandos interactivos por stdin (cuando es TTY):
    mo <destino> <origen> <texto>   inyecta un MO al primer cliente conectado
    quit                            sale ordenadamente
"""
from __future__ import annotations

import argparse
import logging
import random
import secrets
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# ============================================================================
# SMPP 3.4 constants
# ============================================================================

# Command IDs (spec §5.1.2.1)
BIND_RECEIVER          = 0x00000001
BIND_TRANSMITTER       = 0x00000002
BIND_TRANSCEIVER       = 0x00000009
BIND_RECEIVER_RESP     = 0x80000001
BIND_TRANSMITTER_RESP  = 0x80000002
BIND_TRANSCEIVER_RESP  = 0x80000009
UNBIND                 = 0x00000006
UNBIND_RESP            = 0x80000006
SUBMIT_SM              = 0x00000004
SUBMIT_SM_RESP         = 0x80000004
DELIVER_SM             = 0x00000005
DELIVER_SM_RESP        = 0x80000005
ENQUIRE_LINK           = 0x00000015
ENQUIRE_LINK_RESP      = 0x80000015
GENERIC_NACK           = 0x80000000

ESME_ROK               = 0x00000000
ESME_RINVCMDID         = 0x00000003

PDU_NAME = {
    BIND_RECEIVER:          "BIND_RECEIVER",
    BIND_TRANSMITTER:       "BIND_TRANSMITTER",
    BIND_TRANSCEIVER:       "BIND_TRANSCEIVER",
    BIND_RECEIVER_RESP:     "BIND_RECEIVER_RESP",
    BIND_TRANSMITTER_RESP:  "BIND_TRANSMITTER_RESP",
    BIND_TRANSCEIVER_RESP:  "BIND_TRANSCEIVER_RESP",
    UNBIND:                 "UNBIND",
    UNBIND_RESP:            "UNBIND_RESP",
    SUBMIT_SM:              "SUBMIT_SM",
    SUBMIT_SM_RESP:         "SUBMIT_SM_RESP",
    DELIVER_SM:             "DELIVER_SM",
    DELIVER_SM_RESP:        "DELIVER_SM_RESP",
    ENQUIRE_LINK:           "ENQUIRE_LINK",
    ENQUIRE_LINK_RESP:      "ENQUIRE_LINK_RESP",
    GENERIC_NACK:           "GENERIC_NACK",
}

# Bind response system_id que devolvemos a cualquier cliente.
SIMULATOR_SYSTEM_ID = "ami_simulator"

# Delay (segundos) entre SUBMIT_SM_RESP y el DELIVER_SM con el DLR.
DLR_DELAY_S = 0.2


# ============================================================================
# PDU primitives
# ============================================================================

HEADER_FMT = "!IIII"  # command_length, command_id, command_status, sequence_number
HEADER_LEN = 16


def pack_pdu(command_id: int, command_status: int, sequence_number: int, body: bytes) -> bytes:
    total = HEADER_LEN + len(body)
    return struct.pack(HEADER_FMT, total, command_id, command_status, sequence_number) + body


def cstring(s: str) -> bytes:
    """Encode a C-string (NUL-terminated) as ASCII/latin-1, like SMPP wants."""
    return s.encode("latin-1", errors="replace") + b"\x00"


def read_cstring(buf: bytes, offset: int) -> Tuple[str, int]:
    """Read a NUL-terminated string starting at `offset`. Return (value, new_offset)."""
    end = buf.find(b"\x00", offset)
    if end < 0:
        # Malformed but tolerate it: take the rest.
        return buf[offset:].decode("latin-1", errors="replace"), len(buf)
    return buf[offset:end].decode("latin-1", errors="replace"), end + 1


def recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes from sock, or return None on clean EOF."""
    chunks: List[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_pdu(sock: socket.socket) -> Optional[Tuple[int, int, int, bytes]]:
    """Read a full PDU from sock. Return (command_id, status, seq, body) or None on EOF."""
    header = recv_exact(sock, HEADER_LEN)
    if header is None:
        return None
    command_length, command_id, command_status, sequence_number = struct.unpack(HEADER_FMT, header)
    body_len = command_length - HEADER_LEN
    if body_len < 0 or body_len > 1 << 20:  # 1 MiB sanity cap
        return None
    body = b"" if body_len == 0 else recv_exact(sock, body_len)
    if body is None:
        return None
    return command_id, command_status, sequence_number, body


# ============================================================================
# SUBMIT_SM body parsing (we only need the fields needed to build a DLR)
# ============================================================================

@dataclass
class SubmitSm:
    service_type: str
    source_addr_ton: int
    source_addr_npi: int
    source_addr: str
    dest_addr_ton: int
    dest_addr_npi: int
    destination_addr: str
    esm_class: int
    short_message: bytes


def parse_submit_sm(body: bytes) -> SubmitSm:
    """Parse just enough of a SUBMIT_SM body to build a credible DLR."""
    off = 0
    service_type, off = read_cstring(body, off)
    source_addr_ton = body[off]; off += 1
    source_addr_npi = body[off]; off += 1
    source_addr, off = read_cstring(body, off)
    dest_addr_ton = body[off]; off += 1
    dest_addr_npi = body[off]; off += 1
    destination_addr, off = read_cstring(body, off)
    esm_class = body[off]; off += 1
    # protocol_id, priority_flag
    off += 2
    # schedule_delivery_time, validity_period
    _, off = read_cstring(body, off)
    _, off = read_cstring(body, off)
    # registered_delivery, replace_if_present_flag, data_coding, sm_default_msg_id
    off += 4
    sm_length = body[off]; off += 1
    short_message = body[off:off + sm_length]
    return SubmitSm(
        service_type=service_type,
        source_addr_ton=source_addr_ton,
        source_addr_npi=source_addr_npi,
        source_addr=source_addr,
        dest_addr_ton=dest_addr_ton,
        dest_addr_npi=dest_addr_npi,
        destination_addr=destination_addr,
        esm_class=esm_class,
        short_message=short_message,
    )


# ============================================================================
# DLR / DELIVER_SM construction
# ============================================================================

def build_dlr_text(message_id: str, delivered: bool) -> str:
    """Standard SMPP DLR text body (spec appendix B). Kannel parses this."""
    now = time.strftime("%y%m%d%H%M", time.gmtime())
    stat = "DELIVRD" if delivered else "UNDELIV"
    err = "000" if delivered else "001"
    dlvrd = "001" if delivered else "000"
    return (
        f"id:{message_id} sub:001 dlvrd:{dlvrd} "
        f"submit date:{now} done date:{now} stat:{stat} err:{err} text:"
    )


def build_deliver_sm_body(
    source_addr: str,
    destination_addr: str,
    short_message: bytes,
    *,
    esm_class: int,
    source_ton: int = 1,
    source_npi: int = 1,
    dest_ton: int = 1,
    dest_npi: int = 1,
) -> bytes:
    parts: List[bytes] = []
    parts.append(cstring(""))                       # service_type
    parts.append(bytes([source_ton, source_npi]))   # source_addr_ton/npi
    parts.append(cstring(source_addr))              # source_addr
    parts.append(bytes([dest_ton, dest_npi]))       # dest_addr_ton/npi
    parts.append(cstring(destination_addr))         # destination_addr
    parts.append(bytes([esm_class]))                # esm_class (0x04 = DLR)
    parts.append(b"\x00")                           # protocol_id
    parts.append(b"\x00")                           # priority_flag
    parts.append(cstring(""))                       # schedule_delivery_time
    parts.append(cstring(""))                       # validity_period
    parts.append(b"\x00")                           # registered_delivery
    parts.append(b"\x00")                           # replace_if_present_flag
    parts.append(b"\x00")                           # data_coding
    parts.append(b"\x00")                           # sm_default_msg_id
    parts.append(bytes([min(len(short_message), 254)]))  # sm_length
    parts.append(short_message[:254])               # short_message
    return b"".join(parts)


# ============================================================================
# Session
# ============================================================================

@dataclass
class Session:
    sock: socket.socket
    peer: Tuple[str, int]
    system_id: str = ""
    bound: bool = False
    bind_type: str = ""  # "TRX", "TX", "RX"
    write_lock: threading.Lock = field(default_factory=threading.Lock)
    seq_counter: int = 0
    seq_lock: threading.Lock = field(default_factory=threading.Lock)
    alive: bool = True

    def next_seq(self) -> int:
        with self.seq_lock:
            self.seq_counter += 1
            return self.seq_counter

    def send(self, pdu: bytes) -> None:
        with self.write_lock:
            try:
                self.sock.sendall(pdu)
            except OSError:
                self.alive = False


# ============================================================================
# Simulator
# ============================================================================

class SmppSimulator:
    """Thread-per-connection SMPP 3.4 server."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 2775,
        *,
        fail_rate: float = 0.0,
        send_dlr: bool = True,
        dlr_delay: float = DLR_DELAY_S,
        rng: Optional[random.Random] = None,
        on_submit: Optional[Callable[[Session, SubmitSm, str], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.fail_rate = max(0.0, min(1.0, fail_rate))
        self.send_dlr = send_dlr
        self.dlr_delay = dlr_delay
        self.rng = rng or random.Random()
        self.on_submit = on_submit  # hook for tests

        self.log = logging.getLogger("smpp_simulator")
        self._server_sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._sessions: List[Session] = []
        self._sessions_lock = threading.Lock()
        self._submitted: Dict[str, SubmitSm] = {}
        self._submitted_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> int:
        """Bind and start accepting connections. Returns the actual bound port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(64)
        sock.settimeout(0.5)
        self._server_sock = sock
        actual_port = sock.getsockname()[1]
        self.port = actual_port
        threading.Thread(target=self._accept_loop, name="smpp-accept", daemon=True).start()
        self.log.info("listening host=%s port=%d fail_rate=%.2f send_dlr=%s",
                      self.host, actual_port, self.fail_rate, self.send_dlr)
        return actual_port

    def stop(self) -> None:
        self._stop.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
        with self._sessions_lock:
            sessions = list(self._sessions)
        for s in sessions:
            s.alive = False
            try:
                s.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ accept loop

    def _accept_loop(self) -> None:
        assert self._server_sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(None)
            session = Session(sock=client, peer=addr)
            with self._sessions_lock:
                self._sessions.append(session)
            self.log.info("accepted peer=%s:%d", addr[0], addr[1])
            threading.Thread(
                target=self._session_loop,
                args=(session,),
                name=f"smpp-session-{addr[1]}",
                daemon=True,
            ).start()

    # ------------------------------------------------------------------ session loop

    def _session_loop(self, session: Session) -> None:
        try:
            while session.alive and not self._stop.is_set():
                pdu = recv_pdu(session.sock)
                if pdu is None:
                    break
                command_id, _status, seq, body = pdu
                self._log_pdu("recv", command_id, seq, session)
                self._handle_pdu(session, command_id, seq, body)
        except OSError:
            pass
        finally:
            session.alive = False
            try:
                session.sock.close()
            except OSError:
                pass
            with self._sessions_lock:
                if session in self._sessions:
                    self._sessions.remove(session)
            self.log.info("closed peer=%s:%d system_id=%s",
                          session.peer[0], session.peer[1], session.system_id or "-")

    def _log_pdu(self, direction: str, command_id: int, seq: int, session: Session) -> None:
        name = PDU_NAME.get(command_id, f"UNKNOWN(0x{command_id:08x})")
        self.log.info("[%s] %s seq=%d system_id=%s peer=%s:%d",
                      direction, name, seq, session.system_id or "-",
                      session.peer[0], session.peer[1])

    # ------------------------------------------------------------------ PDU dispatch

    def _handle_pdu(self, session: Session, command_id: int, seq: int, body: bytes) -> None:
        if command_id in (BIND_TRANSCEIVER, BIND_TRANSMITTER, BIND_RECEIVER):
            self._handle_bind(session, command_id, seq, body)
        elif command_id == ENQUIRE_LINK:
            self._send(session, ENQUIRE_LINK_RESP, ESME_ROK, seq, b"")
        elif command_id == UNBIND:
            self._send(session, UNBIND_RESP, ESME_ROK, seq, b"")
            session.alive = False
        elif command_id == SUBMIT_SM:
            self._handle_submit_sm(session, seq, body)
        elif command_id == DELIVER_SM_RESP:
            # Cliente confirmando un DLR/MO; nada que hacer.
            pass
        else:
            self.log.warning("unsupported command_id=0x%08x seq=%d", command_id, seq)
            self._send(session, GENERIC_NACK, ESME_RINVCMDID, seq, b"")

    # ------------------------------------------------------------------ BIND_*

    def _handle_bind(self, session: Session, command_id: int, seq: int, body: bytes) -> None:
        # body = system_id, password, system_type, interface_version, addr_ton, addr_npi, address_range
        off = 0
        system_id, off = read_cstring(body, off)
        password, off = read_cstring(body, off)
        # Aceptamos cualquiera. El campo password lo logueamos enmascarado.
        session.system_id = system_id
        session.bound = True
        session.bind_type = {
            BIND_TRANSCEIVER: "TRX",
            BIND_TRANSMITTER: "TX",
            BIND_RECEIVER:    "RX",
        }[command_id]
        resp_cmd = {
            BIND_TRANSCEIVER: BIND_TRANSCEIVER_RESP,
            BIND_TRANSMITTER: BIND_TRANSMITTER_RESP,
            BIND_RECEIVER:    BIND_RECEIVER_RESP,
        }[command_id]
        resp_body = cstring(SIMULATOR_SYSTEM_ID)
        self.log.info("bind ok system_id=%s bind_type=%s password_len=%d",
                      system_id, session.bind_type, len(password))
        self._send(session, resp_cmd, ESME_ROK, seq, resp_body)

    # ------------------------------------------------------------------ SUBMIT_SM

    def _handle_submit_sm(self, session: Session, seq: int, body: bytes) -> None:
        try:
            submit = parse_submit_sm(body)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("malformed SUBMIT_SM seq=%d err=%s", seq, exc)
            self._send(session, SUBMIT_SM_RESP, ESME_RINVCMDID, seq, b"")
            return

        message_id = secrets.token_hex(8)
        with self._submitted_lock:
            self._submitted[message_id] = submit

        self.log.info("SUBMIT_SM from=%s to=%s len=%d message_id=%s",
                      submit.source_addr, submit.destination_addr,
                      len(submit.short_message), message_id)

        resp_body = cstring(message_id)
        self._send(session, SUBMIT_SM_RESP, ESME_ROK, seq, resp_body)

        if self.on_submit is not None:
            try:
                self.on_submit(session, submit, message_id)
            except Exception:  # noqa: BLE001
                self.log.exception("on_submit hook raised")

        if self.send_dlr:
            threading.Timer(
                self.dlr_delay,
                self._send_dlr,
                args=(session, submit, message_id),
            ).start()

    def _send_dlr(self, session: Session, submit: SubmitSm, message_id: str) -> None:
        if not session.alive:
            return
        delivered = self.rng.random() >= self.fail_rate
        text = build_dlr_text(message_id, delivered=delivered)
        body = build_deliver_sm_body(
            source_addr=submit.destination_addr,
            destination_addr=submit.source_addr,
            short_message=text.encode("latin-1", errors="replace"),
            esm_class=0x04,  # MC Delivery Receipt
        )
        seq = session.next_seq()
        self.log.info("DELIVER_SM (DLR) message_id=%s stat=%s seq=%d",
                      message_id, "DELIVRD" if delivered else "UNDELIV", seq)
        self._send(session, DELIVER_SM, ESME_ROK, seq, body)

    # ------------------------------------------------------------------ MO injection

    def inject_mo(self, source: str, destination: str, text: str) -> bool:
        """Send a mobile-originated SMS to the first live bound session.

        Returns True if at least one session received it.
        """
        body = build_deliver_sm_body(
            source_addr=source,
            destination_addr=destination,
            short_message=text.encode("latin-1", errors="replace"),
            esm_class=0x00,  # normal MO, not a DLR
        )
        delivered = False
        with self._sessions_lock:
            targets = [s for s in self._sessions if s.alive and s.bound]
        for s in targets:
            seq = s.next_seq()
            self.log.info("inject MO from=%s to=%s len=%d seq=%d",
                          source, destination, len(text), seq)
            self._send(s, DELIVER_SM, ESME_ROK, seq, body)
            delivered = True
        return delivered

    # ------------------------------------------------------------------ send helper

    def _send(self, session: Session, command_id: int, status: int, seq: int, body: bytes) -> None:
        pdu = pack_pdu(command_id, status, seq, body)
        self._log_pdu("send", command_id, seq, session)
        session.send(pdu)


# ============================================================================
# DLR text parser (helper for tests / debugging)
# ============================================================================

def parse_dlr_text(text: str) -> Dict[str, str]:
    """Parse the standard DLR text body into a dict of fields.

    Expected format (spec appendix B):
        "id:X sub:Y dlvrd:Z submit date:T1 done date:T2 stat:S err:E text:M"
    The "submit date" / "done date" keys contain a space, so we treat them
    specially when walking the token stream.
    """
    out: Dict[str, str] = {}
    parts = text.split(" ")
    j = 0
    while j < len(parts):
        p = parts[j]
        if p in ("submit", "done") and j + 1 < len(parts) and parts[j + 1].startswith("date:"):
            out[f"{p}_date"] = parts[j + 1][len("date:"):]
            j += 2
            continue
        if ":" in p:
            k, v = p.split(":", 1)
            out[k] = v
        j += 1
    return out


# ============================================================================
# CLI
# ============================================================================

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smpp_simulator",
        description="SMPP 3.4 simulator for AMI — pretend to be a partner SMSC.",
    )
    p.add_argument("--host", default="0.0.0.0",
                   help="Bind address (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=2775,
                   help="Bind port (default: 2775)")
    p.add_argument("--fail-rate", type=float, default=0.0,
                   help="Fraction (0..1) of SUBMIT_SM that get a FAILED DLR (default: 0.0)")
    p.add_argument("--no-dlr", action="store_true",
                   help="Do not send DELIVER_SM with the DLR (to test timeouts)")
    p.add_argument("--dlr-delay", type=float, default=DLR_DELAY_S,
                   help=f"Seconds between SUBMIT_SM_RESP and DELIVER_SM (default: {DLR_DELAY_S})")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   help="Logging level (default: INFO)")
    return p


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _interactive_loop(sim: SmppSimulator) -> None:
    """Read commands from stdin: `mo dst src text...` or `quit`."""
    if not sys.stdin or not sys.stdin.isatty():
        # Non-interactive (piped, daemon under docker, etc): just block forever.
        while True:
            time.sleep(3600)
        return
    print("commands: 'mo <dst> <src> <text>'  |  'quit'", flush=True)
    while True:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if not ready:
                continue
            line = sys.stdin.readline()
        except (OSError, ValueError):
            return
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        if line in ("quit", "exit"):
            return
        parts = line.split(None, 3)
        if len(parts) >= 4 and parts[0] == "mo":
            _, dst, src, text = parts
            ok = sim.inject_mo(source=src, destination=dst, text=text)
            print(f"injected (delivered_to_session={ok})", flush=True)
        else:
            print("usage: mo <dst> <src> <text>", flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    _configure_logging(args.log_level)

    sim = SmppSimulator(
        host=args.host,
        port=args.port,
        fail_rate=args.fail_rate,
        send_dlr=not args.no_dlr,
        dlr_delay=args.dlr_delay,
    )
    sim.start()
    try:
        _interactive_loop(sim)
    except KeyboardInterrupt:
        print("", flush=True)
    finally:
        sim.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
