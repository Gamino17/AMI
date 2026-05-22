"""Minimal HTTP layer backed by ``requests``.

The SDK keeps a single ``requests.Session`` per client so connection pooling
and the Authorization header are reused across calls. The class is internal:
public surface lives in :mod:`ami.client` and :mod:`ami.agent`.

Retry policy: idempotent verbs (``GET``) are retried on ``5xx`` and transport
errors with exponential backoff. ``POST`` is **not** retried by default to
avoid double-billing side effects (sending an SMS twice, creating two
contracts). Callers that need at-least-once semantics should rely on the
SDK's typed error and re-try at a higher level where they can dedupe.
"""
from __future__ import annotations

import json as _json
import time
from typing import Any, Dict, Mapping, Optional, Tuple

import requests

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


# Default timeouts: (connect, read). Read is longer because the activation
# endpoint can synchronously wait on the partner telco adapter.
DEFAULT_TIMEOUT: Tuple[float, float] = (5.0, 30.0)

# Retry policy for idempotent verbs only.
_RETRY_BACKOFF_S = (0.5, 1.5, 4.0)
_RETRY_STATUS = {500, 502, 503, 504}
_IDEMPOTENT_VERBS = {"GET", "HEAD"}

_USER_AGENT = "ami-protocol-python/0.1.0"


def _map_error(status: int, body: Optional[Dict[str, Any]], path: str) -> AmiError:
    """Map an HTTP error response onto a typed AMI exception."""
    reason: Optional[str] = None
    if isinstance(body, dict):
        # Backend convention: ``{"error": "<machine_code>", "detail": "..."}``.
        if isinstance(body.get("error"), str):
            reason = body["error"]
        elif isinstance(body.get("reason"), str):
            reason = body["reason"]
    msg = f"{status} on {path}" + (f": {reason}" if reason else "")
    kwargs = dict(status_code=status, detail=body, reason=reason)
    if status in (401, 403):
        return AmiAuthError(msg, **kwargs)
    if status == 404:
        return AmiNotFound(msg, **kwargs)
    if status == 409:
        return AmiConflictError(msg, **kwargs)
    if status == 429:
        return AmiRateLimitError(msg, **kwargs)
    if status == 400:
        return AmiValidationError(msg, **kwargs)
    if 500 <= status < 600:
        return AmiServerError(msg, **kwargs)
    return AmiError(msg, **kwargs)


class _HttpClient:
    """Thin wrapper around ``requests.Session`` that returns parsed JSON or
    raises a typed :class:`ami.errors.AmiError` subclass.

    Parameters
    ----------
    base_url:
        Root URL of the AMI deployment, e.g. ``https://api.protocolami.com``.
    token:
        Bearer token. Either a customer API key (Level 1) or an agent_token
        (Level 2) depending on which client wraps this layer.
    timeout:
        ``(connect, read)`` tuple in seconds. Defaults to ``(5, 30)``.
    session:
        Optional ``requests.Session`` to inject (mainly for tests). When
        ``None``, a fresh session is created and owned by this instance.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not token:
            raise ValueError("token is required")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        })

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP call and return the decoded JSON body.

        Empty bodies (e.g. 204) decode to ``{}``. Non-JSON bodies raise
        :class:`AmiServerError` so the caller always sees a typed exception.
        """
        method = method.upper()
        url = self._base_url + path
        headers: Dict[str, str] = {}
        body: Optional[bytes] = None
        if json is not None:
            body = _json.dumps(json, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempt = 0
        while True:
            try:
                resp = self._session.request(
                    method,
                    url,
                    data=body,
                    params=params,
                    headers=headers or None,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                if method in _IDEMPOTENT_VERBS and attempt < len(_RETRY_BACKOFF_S):
                    time.sleep(_RETRY_BACKOFF_S[attempt])
                    attempt += 1
                    continue
                raise AmiTransportError(
                    f"transport error on {method} {path}: {exc!r}",
                ) from exc

            status = resp.status_code

            # Retry 5xx on idempotent verbs.
            if (
                status in _RETRY_STATUS
                and method in _IDEMPOTENT_VERBS
                and attempt < len(_RETRY_BACKOFF_S)
            ):
                time.sleep(_RETRY_BACKOFF_S[attempt])
                attempt += 1
                continue

            parsed: Optional[Dict[str, Any]] = None
            raw = resp.content or b""
            if raw:
                try:
                    parsed = resp.json()
                except ValueError:
                    parsed = None

            if 200 <= status < 300:
                if parsed is None:
                    return {}
                if not isinstance(parsed, dict):
                    # The AMI REST surface always returns objects, never bare
                    # arrays. Wrap defensively so the caller sees a dict.
                    return {"data": parsed}
                return parsed

            raise _map_error(status, parsed, path)
