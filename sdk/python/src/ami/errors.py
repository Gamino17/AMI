"""Typed exceptions raised by the AMI SDK.

Every error inherits from ``AmiError``. HTTP responses returned by the AMI
backend are mapped onto a specific subclass so callers can catch the case they
care about without parsing status codes themselves.

Mapping:

- 400 -> :class:`AmiValidationError`
- 401 -> :class:`AmiAuthError`
- 403 -> :class:`AmiAuthError`
- 404 -> :class:`AmiNotFound`
- 409 -> :class:`AmiConflictError`
- 429 -> :class:`AmiRateLimitError`
- 5xx -> :class:`AmiServerError`
- transport / DNS / timeout -> :class:`AmiTransportError`
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AmiError(Exception):
    """Base class for every error the SDK raises.

    All HTTP-derived errors expose the parsed JSON body via ``detail`` (or
    ``None`` if the backend returned non-JSON) and the HTTP status via
    ``status_code``. ``reason`` is the machine-readable ``error`` field that
    AMI puts in every error body (e.g. ``"sms_hourly_limit_exceeded"``).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        detail: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code: Optional[int] = status_code
        self.detail: Optional[Dict[str, Any]] = detail
        self.reason: Optional[str] = reason


class AmiAuthError(AmiError):
    """Raised on 401 / 403. The API key or agent_token is missing or invalid."""


class AmiNotFound(AmiError):
    """Raised on 404. The referenced resource does not exist."""


class AmiConflictError(AmiError):
    """Raised on 409. The resource is in a state that does not allow the
    requested transition (e.g. activating a MobileIdentity from an unsigned
    contract)."""


class AmiValidationError(AmiError):
    """Raised on 400. The request body or query did not pass validation."""


class AmiRateLimitError(AmiError):
    """Raised on 429. A policy limit was hit (per-hour / per-day / monthly
    budget / blocked country prefix). ``reason`` carries the specific code
    (``sms_hourly_limit_exceeded``, ``monthly_budget_exceeded``, ...)."""


class AmiServerError(AmiError):
    """Raised on 5xx. The backend failed; retrying after a short delay is
    usually appropriate."""


class AmiTransportError(AmiError):
    """Raised when the HTTP call never produced a response (timeout, DNS
    failure, refused connection, TLS error)."""
