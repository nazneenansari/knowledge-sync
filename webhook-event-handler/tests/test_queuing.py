"""Unit tests for middleware/queuing.py — enqueue_event()."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from middleware.queuing import enqueue_event


_BODY = {
    "tenantId": "acme-corp",
    "appId": "egain",
    "event": "article.published",
    "eventId": "evt-001",
}
_S3_KEY = "webhooks/raw/acme-corp/egain/article_published/evt-001.json"
_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789/test-queue"
_BUCKET = "test-bucket"


class TestEnqueueEvent:

    # ------------------------------------------------------------------
    # SQS call contract
    # ------------------------------------------------------------------

    @patch("middleware.queuing.sqs")
    def test_send_message_called_exactly_once(self, mock_sqs):
        mock_sqs.send_message.return_value = {"MessageId": "msg-001"}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        mock_sqs.send_message.assert_called_once()

    @patch("middleware.queuing.sqs")
    def test_message_sent_to_correct_queue_url(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event("https://sqs.test/my-queue", _BUCKET, _S3_KEY, _BODY)
        assert mock_sqs.send_message.call_args[1]["QueueUrl"] == "https://sqs.test/my-queue"

    @patch("middleware.queuing.sqs")
    def test_message_body_is_valid_json(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        raw = mock_sqs.send_message.call_args[1]["MessageBody"]
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    # ------------------------------------------------------------------
    # Message content
    # ------------------------------------------------------------------

    @patch("middleware.queuing.sqs")
    def _parsed_message(self, mock_sqs) -> dict:
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        return json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])

    @patch("middleware.queuing.sqs")
    def test_message_contains_tenant_id(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["tenantId"] == "acme-corp"

    @patch("middleware.queuing.sqs")
    def test_message_contains_app_id(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["appId"] == "egain"

    @patch("middleware.queuing.sqs")
    def test_message_contains_event_name(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["event"] == "article.published"

    @patch("middleware.queuing.sqs")
    def test_message_contains_event_id(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["eventId"] == "evt-001"

    @patch("middleware.queuing.sqs")
    def test_message_contains_s3_bucket(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, "my-special-bucket", _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["s3Bucket"] == "my-special-bucket"

    @patch("middleware.queuing.sqs")
    def test_message_contains_s3_key(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["s3Key"] == _S3_KEY

    @patch("middleware.queuing.sqs")
    def test_message_contains_received_at(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert "receivedAt" in msg

    # ------------------------------------------------------------------
    # receivedAt timestamp
    # ------------------------------------------------------------------

    @patch("middleware.queuing.sqs")
    def test_received_at_is_parseable_iso8601(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        received_at = datetime.fromisoformat(msg["receivedAt"])
        assert isinstance(received_at, datetime)

    @patch("middleware.queuing.sqs")
    def test_received_at_is_utc(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        enqueue_event(_QUEUE_URL, _BUCKET, _S3_KEY, _BODY)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        received_at = datetime.fromisoformat(msg["receivedAt"])
        assert received_at.tzinfo is not None
        assert received_at.utcoffset().total_seconds() == 0

    # ------------------------------------------------------------------
    # Message passes through correct field values from body
    # ------------------------------------------------------------------

    @patch("middleware.queuing.sqs")
    def test_different_event_type_reflected_in_message(self, mock_sqs):
        mock_sqs.send_message.return_value = {}
        body = {**_BODY, "event": "case.closed", "eventId": "evt-case-001"}
        enqueue_event(_QUEUE_URL, _BUCKET, "some/key.json", body)
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["event"] == "case.closed"
        assert msg["eventId"] == "evt-case-001"
