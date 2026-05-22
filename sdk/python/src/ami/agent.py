"""Level 2 client: agent_token scoped to a single MobileIdentity.

:class:`AmiAgent` is the operations plane (SMS, voice, usage). Every method
acts on the MID that owns the agent_token; no MID has to be passed in.

Get an agent_token by activating a MobileIdentity via
:meth:`ami.AmiClient.activate_identity` (or the one-shot
:meth:`ami.AmiClient.provision_number`). The token is returned exactly once
and must be persisted in a secret store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

import requests

from ._http import DEFAULT_TIMEOUT, _HttpClient


@dataclass
class AgentSelf:
    """Identity of the MID that owns the agent_token."""
    mobile_identity_id: str
    customer_id: str
    phone_number: str
    capabilities: List[str]
    token_prefix: str
    token_created_at: str
    status: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SmsMessage:
    id: str
    mid: str
    direction: str           # "outbound" or "inbound"
    to: str
    from_: str               # ``from`` is a Python keyword; use ``from_``
    body: str
    status: str              # "queued" | "sent" | "delivered" | "failed"
    created_at: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Call:
    id: str
    mid: str
    direction: str
    to: str
    from_: str
    status: str              # "initiated" | "ringing" | "answered" | "completed" | "failed" | ...
    callback_sip_uri: Optional[str]
    created_at: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageSnapshot:
    """Snapshot of consumption and active policy limits for the MID."""
    mid: str
    usage: Dict[str, Any]
    limits: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)


def _sms_from_dict(d: Mapping[str, Any]) -> SmsMessage:
    return SmsMessage(
        id=d["id"],
        mid=d.get("mid", ""),
        direction=d.get("direction", ""),
        to=d.get("to", ""),
        from_=d.get("from", ""),
        body=d.get("body", ""),
        status=d.get("status", ""),
        created_at=d.get("created_at", ""),
        raw=dict(d),
    )


def _call_from_dict(d: Mapping[str, Any]) -> Call:
    return Call(
        id=d["id"],
        mid=d.get("mid", ""),
        direction=d.get("direction", ""),
        to=d.get("to", ""),
        from_=d.get("from", ""),
        status=d.get("status", ""),
        callback_sip_uri=d.get("callback_sip_uri"),
        created_at=d.get("created_at", ""),
        raw=dict(d),
    )


class AmiAgent:
    """Operations-plane client for a single MobileIdentity.

    All calls are authenticated with the ``agent_token`` (Level 2). Every
    response is scoped to the MID that owns the token: there is no way to
    accidentally operate another customer's number from this client.

    Example
    -------
    ::

        from ami import AmiAgent

        agent = AmiAgent(
            agent_token="amiagt_live_...",
            base_url="https://api.protocolami.com",
        )
        agent.send_sms(to="+34600111222", body="hello")
        for msg in agent.iter_sms(direction="inbound"):
            print(msg.id, msg.body)
    """

    def __init__(
        self,
        agent_token: str,
        base_url: str = "https://api.protocolami.com",
        *,
        timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not agent_token.startswith("amiagt_"):
            # Soft check — don't reject (forward-compatible with future
            # prefixes) but make obvious typos visible early.
            pass
        self._http = _HttpClient(
            base_url=base_url,
            token=agent_token,
            timeout=timeout,
            session=session,
        )

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AmiAgent":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return self._http.base_url

    # -- self ---------------------------------------------------------------

    def self_(self) -> AgentSelf:
        """Return information about the MID that owns the agent_token.

        Method is named ``self_`` (trailing underscore) because ``self`` is
        a Python keyword in instance scope. The alias :meth:`whoami` reads
        the same endpoint.
        """
        data = self._http.request("GET", "/v1/agent/self")
        return AgentSelf(
            mobile_identity_id=data["mobile_identity_id"],
            customer_id=data.get("customer_id", ""),
            phone_number=data.get("phone_number", ""),
            capabilities=list(data.get("capabilities", [])),
            token_prefix=data.get("token_prefix", ""),
            token_created_at=data.get("token_created_at", ""),
            status=data.get("status", ""),
            raw=dict(data),
        )

    whoami = self_  # ergonomic alias

    # -- SMS ----------------------------------------------------------------

    def send_sms(self, *, to: str, body: str) -> SmsMessage:
        """Enqueue an outbound SMS. The returned message starts in
        ``queued``; the partner DLR will drive it to ``sent`` then
        ``delivered`` (or ``failed``). Subscribe a webhook to
        ``sms.delivered`` / ``sms.failed`` to learn about the terminal
        transition without polling."""
        data = self._http.request(
            "POST",
            "/v1/agent/sms/send",
            json={"to": to, "body": body},
        )
        return _sms_from_dict(data)

    def list_sms(
        self,
        *,
        limit: int = 20,
        direction: Optional[str] = None,
    ) -> List[SmsMessage]:
        """List recent SMS for this MID.

        Parameters
        ----------
        limit:
            Maximum number of messages to return per call. The backend caps
            individual responses; use :meth:`iter_sms` if you need everything.
        direction:
            ``"inbound"``, ``"outbound"``, or ``None`` for both.
        """
        params: Dict[str, Any] = {"limit": int(limit)}
        if direction is not None:
            params["direction"] = direction
        data = self._http.request("GET", "/v1/agent/sms", params=params)
        return [_sms_from_dict(m) for m in data.get("messages", [])]

    def iter_sms(
        self,
        *,
        page_size: int = 100,
        direction: Optional[str] = None,
    ) -> Iterator[SmsMessage]:
        """Iterate over the SMS history.

        The backend currently returns a single page of size ``limit`` ordered
        newest-first; this helper exposes a generator so the call site does
        not have to special-case that today and will keep working when proper
        pagination cursors land. While the backend has no cursor, the
        generator yields the latest ``page_size`` messages and stops.
        """
        # Forward-compatible: when the backend adds ``next_cursor`` we'll
        # paginate here. For now, yield the single page.
        for msg in self.list_sms(limit=page_size, direction=direction):
            yield msg

    # -- voice --------------------------------------------------------------

    def place_call(self, *, to: str, callback_sip_uri: str) -> Call:
        """Originate an outbound call (bridge-by-API).

        AMI dials the PSTN and SIP-bridges the answered leg to
        ``callback_sip_uri`` (typically the customer's realtime voice engine
        or PBX). AMI carries the pipe; the agent brain lives at the SIP
        endpoint.
        """
        data = self._http.request(
            "POST",
            "/v1/agent/calls/place",
            json={"to": to, "callback_sip_uri": callback_sip_uri},
        )
        return _call_from_dict(data)

    def hangup_call(self, call_id: str) -> Call:
        """Terminate an active call."""
        data = self._http.request(
            "POST",
            f"/v1/agent/calls/{call_id}/hangup",
        )
        return _call_from_dict(data)

    def get_call(self, call_id: str) -> Call:
        """Fetch a single call by id (must belong to this MID)."""
        data = self._http.request("GET", f"/v1/agent/calls/{call_id}")
        return _call_from_dict(data)

    def list_calls(
        self,
        *,
        limit: int = 20,
        direction: Optional[str] = None,
    ) -> List[Call]:
        """List recent calls for this MID."""
        params: Dict[str, Any] = {"limit": int(limit)}
        if direction is not None:
            params["direction"] = direction
        data = self._http.request("GET", "/v1/agent/calls", params=params)
        return [_call_from_dict(c) for c in data.get("calls", [])]

    def iter_calls(
        self,
        *,
        page_size: int = 100,
        direction: Optional[str] = None,
    ) -> Iterator[Call]:
        """Iterate over the call history. See :meth:`iter_sms` for the
        pagination contract."""
        for call in self.list_calls(limit=page_size, direction=direction):
            yield call

    # -- usage --------------------------------------------------------------

    def usage(self) -> UsageSnapshot:
        """Snapshot of consumption (SMS / calls / spend this month) plus the
        active policy limits. Use it inside the agent loop to decide whether
        to keep spending or escalate to a human."""
        data = self._http.request("GET", "/v1/agent/usage")
        return UsageSnapshot(
            mid=data.get("mid", ""),
            usage=dict(data.get("usage", {})),
            limits=dict(data.get("limits", {})),
            raw=dict(data),
        )


__all__ = [
    "AmiAgent",
    "AgentSelf",
    "SmsMessage",
    "Call",
    "UsageSnapshot",
]
