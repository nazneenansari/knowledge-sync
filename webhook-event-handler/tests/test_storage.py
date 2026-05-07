"""Unit tests for middleware/storage.py — store_webhook()."""

import json
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from middleware.storage import DuplicateEventError, store_webhook


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test error"}}, "PutObject")


_BODY = {
    "tenantId": "acme-corp",
    "appId": "egain",
    "event": "article.published",
    "eventId": "evt-001",
    "data": {"articleId": "ART-1", "title": "Test"},
}


class TestStoreWebhook:

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    @patch("middleware.storage.s3")
    def test_returns_correct_s3_key(self, mock_s3):
        mock_s3.put_object.return_value = {}
        key = store_webhook("test-bucket", _BODY)
        assert key == "webhooks/raw/acme-corp/egain/article_published/evt-001.json"

    @patch("middleware.storage.s3")
    def test_event_dots_replaced_with_underscores_in_key(self, mock_s3):
        mock_s3.put_object.return_value = {}
        body = {**_BODY, "event": "egain.article.published"}
        key = store_webhook("test-bucket", body)
        assert "egain_article_published" in key

    @patch("middleware.storage.s3")
    def test_key_path_structure(self, mock_s3):
        mock_s3.put_object.return_value = {}
        key = store_webhook("test-bucket", _BODY)
        parts = key.split("/")
        # webhooks/raw/{tenantId}/{appId}/{event_name}/{eventId}.json
        assert parts[0] == "webhooks"
        assert parts[1] == "raw"
        assert parts[2] == "acme-corp"
        assert parts[3] == "egain"
        assert parts[4] == "article_published"
        assert parts[5] == "evt-001.json"

    @patch("middleware.storage.s3")
    def test_key_ends_with_event_id_json(self, mock_s3):
        mock_s3.put_object.return_value = {}
        key = store_webhook("test-bucket", _BODY)
        assert key.endswith("/evt-001.json")

    @patch("middleware.storage.s3")
    def test_key_reflects_tenant_and_app(self, mock_s3):
        mock_s3.put_object.return_value = {}
        body = {**_BODY, "tenantId": "tenant-x", "appId": "app-y"}
        key = store_webhook("test-bucket", body)
        assert "tenant-x" in key
        assert "app-y" in key

    # ------------------------------------------------------------------
    # put_object call args
    # ------------------------------------------------------------------

    @patch("middleware.storage.s3")
    def test_put_object_called_with_correct_bucket(self, mock_s3):
        mock_s3.put_object.return_value = {}
        store_webhook("my-special-bucket", _BODY)
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "my-special-bucket"

    @patch("middleware.storage.s3")
    def test_put_object_uses_if_none_match_star(self, mock_s3):
        mock_s3.put_object.return_value = {}
        store_webhook("test-bucket", _BODY)
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["IfNoneMatch"] == "*"

    @patch("middleware.storage.s3")
    def test_put_object_content_type_is_json(self, mock_s3):
        mock_s3.put_object.return_value = {}
        store_webhook("test-bucket", _BODY)
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "application/json"

    @patch("middleware.storage.s3")
    def test_put_object_body_is_json_serialised(self, mock_s3):
        mock_s3.put_object.return_value = {}
        store_webhook("test-bucket", _BODY)
        call_kwargs = mock_s3.put_object.call_args[1]
        parsed = json.loads(call_kwargs["Body"])
        assert parsed["tenantId"] == "acme-corp"
        assert parsed["eventId"] == "evt-001"

    @patch("middleware.storage.s3")
    def test_put_object_called_exactly_once(self, mock_s3):
        mock_s3.put_object.return_value = {}
        store_webhook("test-bucket", _BODY)
        mock_s3.put_object.assert_called_once()

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    @patch("middleware.storage.s3")
    def test_precondition_failed_raises_duplicate_event_error(self, mock_s3):
        mock_s3.put_object.side_effect = _client_error("PreconditionFailed")
        with pytest.raises(DuplicateEventError):
            store_webhook("test-bucket", _BODY)

    @patch("middleware.storage.s3")
    def test_duplicate_error_message_contains_event_id(self, mock_s3):
        mock_s3.put_object.side_effect = _client_error("PreconditionFailed")
        with pytest.raises(DuplicateEventError, match="evt-001"):
            store_webhook("test-bucket", _BODY)

    # ------------------------------------------------------------------
    # Other AWS errors
    # ------------------------------------------------------------------

    @patch("middleware.storage.s3")
    def test_access_denied_error_is_reraised(self, mock_s3):
        mock_s3.put_object.side_effect = _client_error("AccessDenied")
        with pytest.raises(ClientError):
            store_webhook("test-bucket", _BODY)

    @patch("middleware.storage.s3")
    def test_no_such_bucket_error_is_reraised(self, mock_s3):
        mock_s3.put_object.side_effect = _client_error("NoSuchBucket")
        with pytest.raises(ClientError):
            store_webhook("test-bucket", _BODY)

    @patch("middleware.storage.s3")
    def test_other_client_error_is_not_wrapped_as_duplicate(self, mock_s3):
        mock_s3.put_object.side_effect = _client_error("InternalError")
        with pytest.raises(ClientError):
            store_webhook("test-bucket", _BODY)
