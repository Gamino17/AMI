"""Tests for :func:`ami.verify_signature`."""
from __future__ import annotations

import hashlib
import hmac

from ami import verify_signature


SECRET = "whsec_" + "b" * 64
BODY = b'{"event":"sms.delivered","mid":"mid_001","data":{"id":"msg_42"}}'


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_valid_signature_returns_true():
    assert verify_signature(SECRET, BODY, _sign(SECRET, BODY)) is True


def test_string_body_is_encoded_as_utf8():
    text = '{"event":"sms.delivered"}'
    sig = _sign(SECRET, text.encode("utf-8"))
    assert verify_signature(SECRET, text, sig) is True


def test_tampered_body_returns_false():
    sig = _sign(SECRET, BODY)
    assert verify_signature(SECRET, BODY + b"!", sig) is False


def test_wrong_secret_returns_false():
    sig = _sign(SECRET, BODY)
    assert verify_signature("whsec_other", BODY, sig) is False


def test_missing_scheme_prefix_returns_false():
    expected = hmac.new(SECRET.encode("utf-8"), BODY, hashlib.sha256).hexdigest()
    # No "sha256=" prefix.
    assert verify_signature(SECRET, BODY, expected) is False


def test_empty_inputs_return_false():
    assert verify_signature("", BODY, _sign(SECRET, BODY)) is False
    assert verify_signature(SECRET, BODY, "") is False


def test_bytearray_body_is_supported():
    body = bytearray(BODY)
    sig = _sign(SECRET, bytes(body))
    assert verify_signature(SECRET, body, sig) is True
