"""Unit tests for middleware/signature.py — verify_signature()."""

import hashlib
import hmac as _hmac
import time

import pytest

from middleware.signature import verify_signature


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    payload = timestamp.encode("utf-8") + b"\n" + body
    return _hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class TestVerifySignature:

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_valid_signature_returns_true(self):
        secret = "my-secret"
        ts = str(int(time.time()))
        body = b'{"event":"article.published"}'
        sig = _sign(secret, ts, body)
        assert verify_signature(secret, ts, body, sig) is True

    def test_returns_false_for_wrong_secret(self):
        ts = str(int(time.time()))
        body = b'{"event":"article.published"}'
        sig = _sign("correct-secret", ts, body)
        assert verify_signature("wrong-secret", ts, body, sig) is False

    def test_returns_false_for_tampered_body(self):
        secret = "my-secret"
        ts = str(int(time.time()))
        original_body = b'{"event":"article.published"}'
        sig = _sign(secret, ts, original_body)
        assert verify_signature(secret, ts, b'{"event":"tampered"}', sig) is False

    def test_returns_false_for_tampered_signature(self):
        secret = "my-secret"
        ts = str(int(time.time()))
        body = b'{"event":"article.published"}'
        assert verify_signature(secret, ts, body, "deadbeef" * 8) is False

    def test_returns_false_for_entirely_wrong_signature(self):
        secret = "my-secret"
        ts = str(int(time.time()))
        body = b'{"event":"article.published"}'
        assert verify_signature(secret, ts, body, "") is False

    # ------------------------------------------------------------------
    # Timestamp validation
    # ------------------------------------------------------------------

    def test_stale_old_timestamp_raises_value_error(self):
        secret = "my-secret"
        old_ts = str(int(time.time()) - 3001)
        body = b'{"test":"body"}'
        sig = _sign(secret, old_ts, body)
        with pytest.raises(ValueError, match="stale"):
            verify_signature(secret, old_ts, body, sig)

    def test_future_timestamp_beyond_tolerance_raises_value_error(self):
        secret = "my-secret"
        future_ts = str(int(time.time()) + 3001)
        body = b'{"test":"body"}'
        sig = _sign(secret, future_ts, body)
        with pytest.raises(ValueError, match="stale"):
            verify_signature(secret, future_ts, body, sig)

    def test_non_integer_timestamp_raises_value_error(self):
        with pytest.raises(ValueError, match="Unix epoch"):
            verify_signature("secret", "not-a-number", b"body", "sig")

    def test_float_string_timestamp_raises_value_error(self):
        with pytest.raises(ValueError, match="Unix epoch"):
            verify_signature("secret", "1700000000.5", b"body", "sig")

    def test_empty_timestamp_raises_value_error(self):
        with pytest.raises(ValueError, match="Unix epoch"):
            verify_signature("secret", "", b"body", "sig")

    def test_timestamp_just_within_tolerance_accepted(self):
        secret = "my-secret"
        ts = str(int(time.time()) - 2999)
        body = b'{"test":"body"}'
        sig = _sign(secret, ts, body)
        assert verify_signature(secret, ts, body, sig) is True

    def test_timestamp_just_over_tolerance_rejected(self):
        secret = "my-secret"
        ts = str(int(time.time()) - 3001)
        body = b'{"test":"body"}'
        sig = _sign(secret, ts, body)
        with pytest.raises(ValueError, match="stale"):
            verify_signature(secret, ts, body, sig)

    # ------------------------------------------------------------------
    # Custom tolerance
    # ------------------------------------------------------------------

    def test_custom_tolerance_rejects_within_default_but_outside_custom(self):
        secret = "my-secret"
        ts = str(int(time.time()) - 10)
        body = b'{"test":"body"}'
        sig = _sign(secret, ts, body)
        with pytest.raises(ValueError, match="stale"):
            verify_signature(secret, ts, body, sig, tolerance_seconds=5)

    def test_custom_tolerance_accepts_within_range(self):
        secret = "my-secret"
        ts = str(int(time.time()) - 3)
        body = b'{"test":"body"}'
        sig = _sign(secret, ts, body)
        assert verify_signature(secret, ts, body, sig, tolerance_seconds=5) is True

    # ------------------------------------------------------------------
    # Signing payload construction
    # ------------------------------------------------------------------

    def test_signing_payload_uses_newline_separator(self):
        """Signature must cover timestamp + \n + body, not any other separator."""
        secret = "my-secret"
        ts = str(int(time.time()))
        body = b'{"event":"test"}'
        # Build a signature with "." separator instead of "\n" — must NOT match
        payload_with_dot = ts.encode() + b"." + body
        wrong_sig = _hmac.new(secret.encode(), payload_with_dot, hashlib.sha256).hexdigest()
        assert verify_signature(secret, ts, body, wrong_sig) is False
