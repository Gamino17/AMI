"""Level 1 client: customer API key.

:class:`AmiClient` covers the full contracting flow (request number, accept
offer, submit customer data, create contract, sign, activate MobileIdentity)
and the administrative plane (limits, webhooks, inbound SIP routing, token
rotation).

Use :class:`ami.agent.AmiAgent` for the operations plane (SMS / voice scoped
to a single MobileIdentity).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import requests

from ._http import DEFAULT_TIMEOUT, _HttpClient


# ---------------------------------------------------------------------------
# Typed return values. These mirror the REST shapes (kept minimal — extra
# fields are preserved in ``.raw`` so the SDK doesn't break when the backend
# adds new fields).
# ---------------------------------------------------------------------------


@dataclass
class Offer:
    """An offer from the partner operator for a new mobile identity."""
    id: str
    sim_request_id: str
    status: str
    country: str
    sim_type: str
    capabilities: List[str]
    monthly_price: float
    setup_fee: float
    currency: str
    requires_contract: bool
    requires_customer_data: bool
    expires_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Customer:
    id: str
    status: str
    legal_name: str
    tax_id: str
    billing_email: str
    address: str
    representative_name: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Contract:
    id: str
    status: str
    offer_id: str
    customer_id: str
    sim_request_id: Optional[str]
    signature_url: str
    expires_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MobileIdentity:
    """A provisioned MobileIdentity.

    ``agent_token`` is **only present on the response of**
    :meth:`AmiClient.activate_identity`. Subsequent reads of the same MID
    return the record without the secret. Persist ``agent_token`` immediately
    in a vault — it is not recoverable later.
    """
    id: str
    status: str
    phone_number: str
    sim_type: str
    capabilities: List[str]
    contract_id: str
    customer_id: str
    agent_token: Optional[str] = None
    activated_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def mid(self) -> str:
        """Alias for ``id``. The wider AMI ecosystem talks about ``mid``."""
        return self.id


@dataclass
class WebhookCreated:
    """Response of :meth:`AmiClient.create_webhook`. ``secret`` is exposed
    once and never again."""
    id: str
    mid: str
    url: str
    events: List[str]
    status: str
    secret: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookInfo:
    id: str
    mid: str
    url: str
    events: List[str]
    status: str
    failure_count: int
    secret_prefix: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvisionedCredentials:
    """Result of :meth:`AmiClient.provision_number`. Carries everything the
    caller needs to bootstrap an :class:`AmiAgent` against the new MID."""
    mid: str
    phone_number: str
    agent_token: str
    customer_id: str
    contract_id: str
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_kwargs(d: Mapping[str, Any], keys: Tuple[str, ...]) -> Dict[str, Any]:
    """Pick exactly ``keys`` from ``d``; missing keys default to ``None``."""
    return {k: d.get(k) for k in keys}


def _offer_from_dict(d: Mapping[str, Any]) -> Offer:
    return Offer(
        id=d["id"],
        sim_request_id=d.get("sim_request_id", ""),
        status=d.get("status", ""),
        country=d.get("country", ""),
        sim_type=d.get("sim_type", ""),
        capabilities=list(d.get("capabilities", [])),
        monthly_price=float(d.get("monthly_price", 0.0)),
        setup_fee=float(d.get("setup_fee", 0.0)),
        currency=d.get("currency", ""),
        requires_contract=bool(d.get("requires_contract", False)),
        requires_customer_data=bool(d.get("requires_customer_data", False)),
        expires_at=d.get("expires_at"),
        raw=dict(d),
    )


def _customer_from_dict(d: Mapping[str, Any]) -> Customer:
    return Customer(
        id=d["id"],
        status=d.get("status", ""),
        legal_name=d.get("legal_name", ""),
        tax_id=d.get("tax_id", ""),
        billing_email=d.get("billing_email", ""),
        address=d.get("address", ""),
        representative_name=d.get("representative_name", ""),
        raw=dict(d),
    )


def _contract_from_dict(d: Mapping[str, Any]) -> Contract:
    return Contract(
        id=d["id"],
        status=d.get("status", ""),
        offer_id=d.get("offer_id", ""),
        customer_id=d.get("customer_id", ""),
        sim_request_id=d.get("sim_request_id"),
        signature_url=d.get("signature_url", ""),
        expires_at=d.get("expires_at"),
        raw=dict(d),
    )


def _identity_from_dict(d: Mapping[str, Any]) -> MobileIdentity:
    return MobileIdentity(
        id=d["id"],
        status=d.get("status", ""),
        phone_number=d.get("phone_number", ""),
        sim_type=d.get("sim_type", ""),
        capabilities=list(d.get("capabilities", [])),
        contract_id=d.get("contract_id", ""),
        customer_id=d.get("customer_id", ""),
        agent_token=d.get("agent_token"),
        activated_at=d.get("activated_at"),
        raw=dict(d),
    )


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class AmiClient:
    """High-level client for the AMI protocol, authenticated as the customer.

    Authentication uses the customer's API key
    (``Authorization: Bearer <AMI_API_KEY>``). This client covers two
    surfaces:

    1. **Contracting**: request a number, accept the offer, submit customer
       data, generate a contract, sign it, activate the MobileIdentity.
    2. **Administration**: limits, webhooks, inbound SIP routing, agent
       token rotation.

    The operations plane (SMS / voice) is served by
    :class:`ami.agent.AmiAgent` and is scoped to a single MobileIdentity via
    its agent_token.

    Example
    -------
    ::

        from ami import AmiClient

        client = AmiClient(
            api_key="ami_live_...",
            base_url="https://api.protocolami.com",
        )
        creds = client.provision_number(
            country="ES",
            customer={
                "legal_name": "Acme S.L.",
                "tax_id": "B12345678",
                "billing_email": "billing@acme.test",
                "address": "Madrid, Spain",
                "representative_name": "Ada Lovelace",
            },
        )
        agent = client.as_agent(creds.agent_token)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.protocolami.com",
        *,
        timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._http = _HttpClient(
            base_url=base_url,
            token=api_key,
            timeout=timeout,
            session=session,
        )

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._http.close()

    def __enter__(self) -> "AmiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return self._http.base_url

    # -- contracting flow ---------------------------------------------------

    def request_number(
        self,
        *,
        country: str,
        capabilities: List[str],
        agent_name: str,
        sim_type: str = "eSIM",
        purpose: str = "agent_identity",
        max_monthly_price: Optional[float] = None,
        currency: str = "EUR",
    ) -> Offer:
        """Create a SIMRequest and receive the offer.

        Returns the :class:`Offer`. The corresponding ``sim_request_id`` is
        on the offer and is needed by :meth:`submit_customer_data`.
        """
        body: Dict[str, Any] = {
            "country": country,
            "sim_type": sim_type,
            "capabilities": list(capabilities),
            "agent": {"name": agent_name, "purpose": purpose},
        }
        if max_monthly_price is not None:
            body["commercial_constraints"] = {
                "max_monthly_price": max_monthly_price,
                "currency": currency,
            }
        data = self._http.request("POST", "/v1/sim-requests", json=body)
        offer = data["offer"]
        # The backend returns the SIMRequest separately; merge its id onto the
        # offer so the caller has everything in one place.
        offer.setdefault("sim_request_id", data["sim_request"]["id"])
        return _offer_from_dict(offer)

    def accept_offer(self, offer_id: str) -> Offer:
        """Accept an offer. Required before creating a contract."""
        data = self._http.request("POST", f"/v1/offers/{offer_id}/accept")
        return _offer_from_dict(data)

    def submit_customer_data(
        self,
        *,
        sim_request_id: str,
        legal_name: str,
        tax_id: str,
        billing_email: str,
        address: str,
        representative_name: str,
    ) -> Customer:
        """Attach the legal/fiscal data of the contracting customer to the
        SIMRequest. Must be called after :meth:`accept_offer`."""
        data = self._http.request(
            "POST",
            f"/v1/sim-requests/{sim_request_id}/customer-data",
            json={"customer": {
                "legal_name": legal_name,
                "tax_id": tax_id,
                "billing_email": billing_email,
                "address": address,
                "representative_name": representative_name,
            }},
        )
        return _customer_from_dict(data["customer"])

    def create_contract(self, *, offer_id: str, customer_id: str) -> Contract:
        """Generate the contract document. The returned :attr:`Contract.signature_url`
        is where the signer must go to sign in the browser."""
        data = self._http.request(
            "POST",
            "/v1/contracts",
            json={"offer_id": offer_id, "customer_id": customer_id},
        )
        return _contract_from_dict(data)

    def mock_sign(self, contract_id: str) -> Contract:
        """Sign a contract programmatically. Useful for tests and automated
        flows; in production the signer opens :attr:`Contract.signature_url`
        in a browser."""
        data = self._http.request(
            "POST",
            f"/v1/contracts/{contract_id}/mock-sign",
        )
        return _contract_from_dict(data)

    def activate_identity(self, contract_id: str) -> MobileIdentity:
        """Activate the MobileIdentity tied to a signed contract.

        The returned :attr:`MobileIdentity.agent_token` is **only available
        in this response**. Persist it immediately in a secure store; AMI
        only keeps the hash. Use :meth:`rotate_agent_token` if it leaks.
        """
        data = self._http.request(
            "POST",
            "/v1/mobile-identities/activate",
            json={"contract_id": contract_id},
        )
        return _identity_from_dict(data)

    def get_identity(self, mid: str) -> MobileIdentity:
        """Fetch the current state of a MobileIdentity. ``agent_token`` is
        never returned here."""
        data = self._http.request("GET", f"/v1/mobile-identities/{mid}")
        return _identity_from_dict(data)

    def cancel_request(self, sim_request_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a SIMRequest before it has reached an active MobileIdentity."""
        body: Dict[str, Any] = {}
        if reason:
            body["reason"] = reason
        return self._http.request(
            "POST",
            f"/v1/sim-requests/{sim_request_id}/cancel",
            json=body or None,
        )

    # -- convenience: one-shot provisioning --------------------------------

    def provision_number(
        self,
        *,
        country: str,
        customer: Mapping[str, str],
        capabilities: Optional[List[str]] = None,
        agent_name: str = "agent01",
        sim_type: str = "eSIM",
    ) -> ProvisionedCredentials:
        """Run the full contracting flow and return everything needed to
        operate the new MobileIdentity.

        This is the recommended entry point for tests, demos, and automated
        agent onboarding. In production the signing step often involves a
        human opening the signature URL in the browser; in that case run the
        flow step by step instead.
        """
        offer = self.request_number(
            country=country,
            capabilities=list(capabilities or ["sms", "voice"]),
            agent_name=agent_name,
            sim_type=sim_type,
        )
        self.accept_offer(offer.id)
        cust = self.submit_customer_data(
            sim_request_id=offer.sim_request_id,
            legal_name=customer["legal_name"],
            tax_id=customer["tax_id"],
            billing_email=customer["billing_email"],
            address=customer["address"],
            representative_name=customer["representative_name"],
        )
        contract = self.create_contract(offer_id=offer.id, customer_id=cust.id)
        self.mock_sign(contract.id)
        identity = self.activate_identity(contract.id)
        if not identity.agent_token:
            # The backend is supposed to return the token on activation.
            # Surface a clear error rather than silently writing ``None``.
            raise RuntimeError(
                "activation response did not include agent_token; check backend version",
            )
        return ProvisionedCredentials(
            mid=identity.id,
            phone_number=identity.phone_number,
            agent_token=identity.agent_token,
            customer_id=cust.id,
            contract_id=contract.id,
            raw=identity.raw,
        )

    def as_agent(self, agent_token: str) -> "AmiAgent":
        """Return an :class:`ami.agent.AmiAgent` bound to ``agent_token``,
        pointed at the same backend as this client. Convenient when you have
        just provisioned a number and want to use it immediately."""
        # Imported here to avoid a circular dependency at module load.
        from .agent import AmiAgent
        return AmiAgent(agent_token=agent_token, base_url=self.base_url)

    # -- administration -----------------------------------------------------

    def get_limits(self, mid: str) -> Dict[str, Any]:
        """Read the spending and rate limits for a MID."""
        data = self._http.request("GET", f"/v1/mobile-identities/{mid}/limits")
        return data.get("limits", data)

    def update_limits(
        self,
        mid: str,
        *,
        sms_per_hour: Optional[int] = None,
        sms_per_day: Optional[int] = None,
        calls_per_hour: Optional[int] = None,
        calls_per_day: Optional[int] = None,
        monthly_budget_eur: Optional[float] = None,
        allowed_country_prefixes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Partial-update the policy limits of a MID.

        Only the fields passed are changed. Defaults are seeded when the MID
        is activated.
        """
        patch: Dict[str, Any] = {}
        if sms_per_hour is not None:
            patch["sms_per_hour"] = sms_per_hour
        if sms_per_day is not None:
            patch["sms_per_day"] = sms_per_day
        if calls_per_hour is not None:
            patch["calls_per_hour"] = calls_per_hour
        if calls_per_day is not None:
            patch["calls_per_day"] = calls_per_day
        if monthly_budget_eur is not None:
            patch["monthly_budget_eur"] = monthly_budget_eur
        if allowed_country_prefixes is not None:
            patch["allowed_country_prefixes"] = list(allowed_country_prefixes)
        if not patch:
            raise ValueError("update_limits requires at least one field to update")
        data = self._http.request(
            "POST",
            f"/v1/mobile-identities/{mid}/limits",
            json=patch,
        )
        return data.get("limits", data)

    def get_usage(self, mid: str) -> Dict[str, Any]:
        """Read the usage counters and tariff snapshot for a MID."""
        return self._http.request("GET", f"/v1/mobile-identities/{mid}/usage")

    def rotate_agent_token(self, mid: str) -> str:
        """Hard-rotate the agent_token of a MID. The previous token is
        invalidated immediately. Returns the new token in plaintext (only
        available in this response)."""
        data = self._http.request(
            "POST",
            f"/v1/mobile-identities/{mid}/rotate-token",
        )
        return data["agent_token"]

    def set_inbound_sip_uri(self, mid: str, sip_uri: Optional[str]) -> Dict[str, Any]:
        """Configure where inbound calls to ``mid`` are SIP-forwarded.

        Pass ``None`` (or an empty string) to clear the routing and reject
        all inbound calls.
        """
        data = self._http.request(
            "POST",
            f"/v1/mobile-identities/{mid}/inbound-config",
            json={"inbound_sip_uri": sip_uri},
        )
        return data

    def create_webhook(
        self,
        mid: str,
        *,
        url: str,
        events: Optional[List[str]] = None,
    ) -> WebhookCreated:
        """Register an outbound webhook for a MID.

        ``events`` is a list of event names or ``["*"]`` to receive every
        supported event. The returned :attr:`WebhookCreated.secret` is the
        HMAC key for :func:`ami.webhooks.verify_signature` and is only
        returned by this call.
        """
        body: Dict[str, Any] = {
            "url": url,
            "events": list(events) if events is not None else ["*"],
        }
        data = self._http.request(
            "POST",
            f"/v1/mobile-identities/{mid}/webhooks",
            json=body,
        )
        return WebhookCreated(
            id=data["id"],
            mid=data.get("mid", mid),
            url=data.get("url", url),
            events=list(data.get("events", body["events"])),
            status=data.get("status", "active"),
            secret=data["secret"],
            raw=dict(data),
        )

    def list_webhooks(self, mid: str) -> List[WebhookInfo]:
        """List the webhooks registered for a MID. Secrets are not returned;
        only the prefix to identify which secret is which."""
        data = self._http.request("GET", f"/v1/mobile-identities/{mid}/webhooks")
        return [
            WebhookInfo(
                id=w["id"],
                mid=w.get("mid", mid),
                url=w.get("url", ""),
                events=list(w.get("events", [])),
                status=w.get("status", ""),
                failure_count=int(w.get("failure_count", 0)),
                secret_prefix=w.get("secret_prefix", ""),
                raw=dict(w),
            )
            for w in data.get("webhooks", [])
        ]

    def delete_webhook(self, mid: str, webhook_id: str) -> None:
        """Delete a webhook by id."""
        self._http.request(
            "POST",
            f"/v1/mobile-identities/{mid}/webhooks/{webhook_id}/delete",
        )


# Re-export for friendlier ``ami.client.asdict`` access in user notebooks.
__all__ = [
    "AmiClient",
    "Offer",
    "Customer",
    "Contract",
    "MobileIdentity",
    "WebhookCreated",
    "WebhookInfo",
    "ProvisionedCredentials",
    "asdict",
]
