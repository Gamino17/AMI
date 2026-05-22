"""Helpers for verifying outbound webhooks delivered by AMI.

When the customer registers a webhook URL via
:meth:`ami.AmiClient.create_webhook`, AMI returns a ``secret`` (only once).
Every subsequent delivery to that URL carries the header
``X-Ami-Signature: sha256=<hex>`` computed as
``HMAC-SHA256(secret, raw_request_body)``.

The receiver MUST verify the signature before trusting the payload. Reject
the request when verification fails: that means either the secret is wrong
or the request did not originate from AMI.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Union


def verify_signature(
    secret: str,
    body: Union[bytes, bytearray, memoryview, str],
    signature_header: str,
) -> bool:
    """Verify the ``X-Ami-Signature`` header against the raw request body.

    Parameters
    ----------
    secret:
        The webhook secret that AMI returned on creation. Starts with
        ``whsec_``.
    body:
        The exact bytes the HTTP request was delivered with. Do not decode,
        re-serialize, or normalise the JSON before passing it here — the HMAC
        is computed over the wire body.
    signature_header:
        Value of the ``X-Ami-Signature`` header, of the form
        ``sha256=<hex>``.

    Returns
    -------
    bool
        ``True`` when the signature matches, ``False`` otherwise. Comparison
        is constant-time.
    """
    if not isinstance(secret, str) or not secret:
        return False
    if not isinstance(signature_header, str) or not signature_header.startswith("sha256="):
        return False
    if isinstance(body, str):
        body = body.encode("utf-8")
    expected = hmac.new(
        secret.encode("utf-8"),
        bytes(body),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])
