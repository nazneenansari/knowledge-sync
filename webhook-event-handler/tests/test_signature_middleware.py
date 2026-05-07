"""
Integration tests for HMAC signature verification through the HTTP layer.

These tests use `unauthed_client` (no dependency bypass) and mock AWS calls
at the boto3 level, since the `get_secret` function is called inside the
`verify` closure and creates a fresh boto3 client on each invocation.
"""

import hashlib
import hmac as _hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Shared payload + helpers
# ---------------------------------------------------------------------------

ARTICLE_PUBLISHED = {
    "event": "article.published",
    "tenantId": "acme-corp",
    "appId": "egain",
    "timestamp": "2024-06-01T12:00:00Z",
    "data": {
        "articleId": "ARTICLE-1001",
        "title": "How to reset your password",
        "urlName": "how-to-reset-password",
        "content": "<p>Reset steps here</p>",
    },
}


def _sign(secret: str, ts: str, body: bytes) -> str:
    payload = ts.encode("utf-8") + b"\n" + body
    return _hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _mock_sm_client(mock_boto, secret_value: str) -> MagicMock:
    mock_sm = MagicMock()
    mock_boto.return_value = mock_sm
    mock_sm.get_secret_value.return_value = {
        "SecretString": json.dumps({"EGAIN_WEBHOOK_HMAC_SECRET": secret_value})
    }
    return mock_sm


# ---------------------------------------------------------------------------
# Missing / malformed headers (FastAPI rejects before the dependency runs)
# ---------------------------------------------------------------------------

class TestMissingHeaders:

    def test_422_missing_x_signature_header(self, unauthed_client):
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=ARTICLE_PUBLISHED,
            headers={"x-timestamp": str(int(time.time()))},
        )
        assert resp.status_code == 422

    def test_422_missing_x_timestamp_header(self, unauthed_client):
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=ARTICLE_PUBLISHED,
            headers={"x-signature": "somesig"},
        )
        assert resp.status_code == 422

    def test_422_both_auth_headers_missing(self, unauthed_client):
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=ARTICLE_PUBLISHED,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Signature content errors (dependency returns 401)
# ---------------------------------------------------------------------------

class TestSignatureErrors:

    def test_401_missing_app_id_in_body(self, unauthed_client):
        """appId is required to resolve the HMAC secret — absence means 401."""
        payload = {k: v for k, v in ARTICLE_PUBLISHED.items() if k != "appId"}
        ts = str(int(time.time()))
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=payload,
            headers={"x-signature": "sig", "x-timestamp": ts},
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_on_unknown_app_id(self, mock_boto, unauthed_client):
        """appId not in app_secret_keys mapping → KeyError → 401."""
        mock_sm = MagicMock()
        mock_boto.return_value = mock_sm
        mock_sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"EGAIN_WEBHOOK_HMAC_SECRET": "secret"})
        }
        payload = {**ARTICLE_PUBLISHED, "appId": "not-registered"}
        ts = str(int(time.time()))
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=payload,
            headers={"x-signature": "sig", "x-timestamp": ts},
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_on_wrong_signature(self, mock_boto, unauthed_client):
        _mock_sm_client(mock_boto, "real-secret")
        ts = str(int(time.time()))
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=ARTICLE_PUBLISHED,
            headers={"x-signature": "completely-wrong-value", "x-timestamp": ts},
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_on_tampered_body_after_signing(self, mock_boto, unauthed_client):
        """Signature was computed over the original body; sending different body must fail."""
        secret = "my-secret"
        _mock_sm_client(mock_boto, secret)
        original = json.dumps(ARTICLE_PUBLISHED).encode()
        ts = str(int(time.time()))
        sig = _sign(secret, ts, original)
        tampered = {**ARTICLE_PUBLISHED, "tenantId": "evil-corp"}
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=tampered,
            headers={"x-signature": sig, "x-timestamp": ts},
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_on_stale_old_timestamp(self, mock_boto, unauthed_client):
        _mock_sm_client(mock_boto, "secret")
        body = json.dumps(ARTICLE_PUBLISHED).encode()
        old_ts = str(int(time.time()) - 9999)
        sig = _sign("secret", old_ts, body)
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-signature": sig,
                "x-timestamp": old_ts,
            },
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_on_future_timestamp_beyond_tolerance(self, mock_boto, unauthed_client):
        _mock_sm_client(mock_boto, "secret")
        body = json.dumps(ARTICLE_PUBLISHED).encode()
        future_ts = str(int(time.time()) + 9999)
        sig = _sign("secret", future_ts, body)
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-signature": sig,
                "x-timestamp": future_ts,
            },
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_on_non_integer_timestamp(self, mock_boto, unauthed_client):
        _mock_sm_client(mock_boto, "secret")
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=ARTICLE_PUBLISHED,
            headers={"x-signature": "sig", "x-timestamp": "not-a-number"},
        )
        assert resp.status_code == 401

    # ------------------------------------------------------------------
    # 500 — Secrets Manager failure
    # ------------------------------------------------------------------

    @patch("middleware.secret_manager.boto3.client")
    def test_500_on_secrets_manager_aws_error(self, mock_boto, unauthed_client):
        mock_sm = MagicMock()
        mock_boto.return_value = mock_sm
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError", "Message": "AWS error"}},
            "GetSecretValue",
        )
        ts = str(int(time.time()))
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=ARTICLE_PUBLISHED,
            headers={"x-signature": "sig", "x-timestamp": ts},
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Valid signature — full happy path through auth layer
# ---------------------------------------------------------------------------

class TestValidSignature:

    @patch("middleware.secret_manager.boto3.client")
    def test_202_on_correct_hmac_signature(self, mock_boto, unauthed_client):
        secret = "valid-secret"
        _mock_sm_client(mock_boto, secret)
        body = json.dumps(ARTICLE_PUBLISHED).encode()
        ts = str(int(time.time()))
        sig = _sign(secret, ts, body)
        with patch("webhooks.store_webhook", return_value="s3-key"), \
             patch("webhooks.enqueue_event"):
            resp = unauthed_client.post(
                "/dev/webhooks/article-published",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "x-signature": sig,
                    "x-timestamp": ts,
                },
            )
        assert resp.status_code == 202

    @patch("middleware.secret_manager.boto3.client")
    def test_401_when_same_sig_reused_with_different_body(self, mock_boto, unauthed_client):
        """Signature is bound to the original body bytes — reuse on different body fails."""
        secret = "valid-secret"
        _mock_sm_client(mock_boto, secret)
        original_body = json.dumps(ARTICLE_PUBLISHED).encode()
        ts = str(int(time.time()))
        sig = _sign(secret, ts, original_body)

        different_body = {**ARTICLE_PUBLISHED, "tenantId": "different-tenant"}
        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            json=different_body,
            headers={"x-signature": sig, "x-timestamp": ts},
        )
        assert resp.status_code == 401

    @patch("middleware.secret_manager.boto3.client")
    def test_401_when_same_sig_reused_with_different_timestamp(self, mock_boto, unauthed_client):
        """Signature covers the timestamp — changing the timestamp header invalidates it."""
        secret = "valid-secret"
        _mock_sm_client(mock_boto, secret)
        body = json.dumps(ARTICLE_PUBLISHED).encode()
        ts = str(int(time.time()))
        sig = _sign(secret, ts, body)

        resp = unauthed_client.post(
            "/dev/webhooks/article-published",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-signature": sig,
                "x-timestamp": str(int(ts) - 1),  # different ts in header
            },
        )
        assert resp.status_code == 401
