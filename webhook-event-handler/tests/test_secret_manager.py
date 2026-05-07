"""Unit tests for middleware/secret_manager.py — get_secret()."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from middleware.secret_manager import get_secret


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test error"}}, "GetSecretValue")


def _mock_boto(mock_boto, secret_payload: dict) -> MagicMock:
    mock_sm = MagicMock()
    mock_boto.return_value = mock_sm
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(secret_payload)}
    return mock_sm


_FULL_SECRET = {
    "EGAIN_WEBHOOK_HMAC_SECRET": "egain-hmac-value",
    "TEST_WEBHOOK_HMAC_SECRET": "test-hmac-value",
}


class TestGetSecret:

    # ------------------------------------------------------------------
    # Known app IDs
    # ------------------------------------------------------------------

    @patch("middleware.secret_manager.boto3.client")
    def test_egain_app_id_returns_correct_secret(self, mock_boto):
        _mock_boto(mock_boto, _FULL_SECRET)
        assert get_secret("event-producer-secret", "egain") == "egain-hmac-value"

    @patch("middleware.secret_manager.boto3.client")
    def test_test_app_id_returns_correct_secret(self, mock_boto):
        _mock_boto(mock_boto, _FULL_SECRET)
        assert get_secret("event-producer-secret", "test") == "test-hmac-value"

    @patch("middleware.secret_manager.boto3.client")
    def test_correct_secret_name_is_passed_to_aws(self, mock_boto):
        mock_sm = _mock_boto(mock_boto, _FULL_SECRET)
        get_secret("my-custom-secret", "egain")
        mock_sm.get_secret_value.assert_called_once_with(SecretId="my-custom-secret")

    # ------------------------------------------------------------------
    # Unknown / missing app IDs
    # ------------------------------------------------------------------

    @patch("middleware.secret_manager.boto3.client")
    def test_unknown_app_id_raises_key_error(self, mock_boto):
        _mock_boto(mock_boto, _FULL_SECRET)
        with pytest.raises(KeyError):
            get_secret("event-producer-secret", "unknown-app")

    @patch("middleware.secret_manager.boto3.client")
    def test_known_app_id_but_key_missing_from_secret_raises_key_error(self, mock_boto):
        # The mapping resolves to EGAIN_WEBHOOK_HMAC_SECRET but that key is absent
        _mock_boto(mock_boto, {"SOME_OTHER_KEY": "value"})
        with pytest.raises(KeyError):
            get_secret("event-producer-secret", "egain")

    # ------------------------------------------------------------------
    # AWS errors
    # ------------------------------------------------------------------

    @patch("middleware.secret_manager.boto3.client")
    def test_resource_not_found_error_is_reraised(self, mock_boto):
        mock_sm = MagicMock()
        mock_boto.return_value = mock_sm
        mock_sm.get_secret_value.side_effect = _client_error("ResourceNotFoundException")
        with pytest.raises(ClientError):
            get_secret("event-producer-secret", "egain")

    @patch("middleware.secret_manager.boto3.client")
    def test_access_denied_error_is_reraised(self, mock_boto):
        mock_sm = MagicMock()
        mock_boto.return_value = mock_sm
        mock_sm.get_secret_value.side_effect = _client_error("AccessDeniedException")
        with pytest.raises(ClientError):
            get_secret("event-producer-secret", "egain")

    # ------------------------------------------------------------------
    # SecretBinary fallback
    # ------------------------------------------------------------------

    @patch("middleware.secret_manager.boto3.client")
    def test_secret_binary_fallback_decoded_correctly(self, mock_boto):
        mock_sm = MagicMock()
        mock_boto.return_value = mock_sm
        mock_sm.get_secret_value.return_value = {
            "SecretBinary": json.dumps(_FULL_SECRET).encode("utf-8")
        }
        assert get_secret("event-producer-secret", "egain") == "egain-hmac-value"

    @patch("middleware.secret_manager.boto3.client")
    def test_secret_string_takes_priority_over_binary(self, mock_boto):
        mock_sm = MagicMock()
        mock_boto.return_value = mock_sm
        mock_sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"EGAIN_WEBHOOK_HMAC_SECRET": "from-string"}),
            "SecretBinary": json.dumps({"EGAIN_WEBHOOK_HMAC_SECRET": "from-binary"}).encode(),
        }
        assert get_secret("event-producer-secret", "egain") == "from-string"
