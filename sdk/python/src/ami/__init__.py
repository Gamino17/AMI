"""ami-protocol — Official Python SDK for the AMI protocol.

The AMI protocol standardises how an AI agent requests, contracts, activates
and operates a mobile identity (SIM / eSIM / phone number). The SDK exposes
two clients matching the two auth planes:

- :class:`AmiClient` — authenticated with the customer API key (Level 1).
  Covers the contracting flow and the administrative plane.
- :class:`AmiAgent` — authenticated with the per-MID ``agent_token``
  (Level 2). Covers SMS and voice operations scoped to one MobileIdentity.

Webhook signatures are verified with :func:`ami.webhooks.verify_signature`.

Quickstart
----------

::

    from ami import AmiClient

    client = AmiClient(api_key="ami_live_...", base_url="https://api.protocolami.com")
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
    agent.send_sms(to="+34600111222", body="hello from my agent")
"""
from __future__ import annotations

from .agent import AgentSelf, AmiAgent, Call, SmsMessage, UsageSnapshot
from .client import (
    AmiClient,
    Contract,
    Customer,
    MobileIdentity,
    Offer,
    ProvisionedCredentials,
    WebhookCreated,
    WebhookInfo,
)
from .errors import (
    AmiAuthError,
    AmiConflictError,
    AmiError,
    AmiNotFound,
    AmiRateLimitError,
    AmiServerError,
    AmiTransportError,
    AmiValidationError,
)
from .webhooks import verify_signature

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Clients
    "AmiClient",
    "AmiAgent",
    # Client return types
    "Offer",
    "Customer",
    "Contract",
    "MobileIdentity",
    "ProvisionedCredentials",
    "WebhookCreated",
    "WebhookInfo",
    # Agent return types
    "AgentSelf",
    "SmsMessage",
    "Call",
    "UsageSnapshot",
    # Errors
    "AmiError",
    "AmiAuthError",
    "AmiNotFound",
    "AmiConflictError",
    "AmiValidationError",
    "AmiRateLimitError",
    "AmiServerError",
    "AmiTransportError",
    # Webhooks
    "verify_signature",
]
