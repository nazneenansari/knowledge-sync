"""Tests for middleware/sqs_client.py — queue draining, message deletion, and DLQ routing."""
import json
from unittest.mock import patch, call
import pytest

import middleware.sqs_client as sqs_module
from middleware.sqs_client import drain_sqs, delete_message, send_to_dlq


def _sqs_raw(body: dict, receipt_handle: str = "rh-1") -> dict:
    return {"Body": json.dumps(body), "ReceiptHandle": receipt_handle}


def _valid_body(
    event_id: str = "evt-1",
    app_id: str = "app-1",
    tenant_id: str = "t-1",
) -> dict:
    return {
        "tenantId": tenant_id,
        "eventId": event_id,
        "appId": app_id,
        "event": "TestEvent",
        "s3Key": "path/key.json",
        "s3Bucket": "my-bucket",
    }


@pytest.fixture
def mock_sqs():
    with patch.object(sqs_module, "sqs") as mock_client:
        yield mock_client


@pytest.fixture
def mock_update_status():
    with patch("middleware.sqs_client.update_status") as mock_fn:
        yield mock_fn


class TestDeleteMessage:
    def test_calls_sqs_delete_message(self, mock_sqs):
        with patch.object(sqs_module, "QUEUE_URL", "https://sqs.test/queue"):
            delete_message("receipt-abc")
        mock_sqs.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.test/queue",
            ReceiptHandle="receipt-abc",
        )

    def test_uses_queue_url_not_dlq(self, mock_sqs):
        with patch.object(sqs_module, "QUEUE_URL", "https://sqs.test/main-queue"), \
             patch.object(sqs_module, "DLQ_URL", "https://sqs.test/dlq"):
            delete_message("rh-1")
        kwargs = mock_sqs.delete_message.call_args[1]
        assert kwargs["QueueUrl"] == "https://sqs.test/main-queue"


class TestDrainSqs:
    def test_empty_response_messages_key_returns_empty_list(self, mock_sqs):
        mock_sqs.receive_message.return_value = {"Messages": []}
        result = drain_sqs()
        assert result == []

    def test_missing_messages_key_returns_empty_list(self, mock_sqs):
        mock_sqs.receive_message.return_value = {}
        result = drain_sqs()
        assert result == []

    def test_parses_single_valid_message(self, mock_sqs):
        body = _valid_body("evt-1", "app-1")
        mock_sqs.receive_message.side_effect = [
            {"Messages": [_sqs_raw(body, "rh-1")]},
            {"Messages": []},
        ]
        result = drain_sqs()
        assert len(result) == 1
        assert result[0]["eventId"] == "evt-1"
        assert result[0]["appId"] == "app-1"
        assert result[0]["tenantId"] == "t-1"
        assert result[0]["receipt_handle"] == "rh-1"
        assert result[0]["s3Key"] == "path/key.json"
        assert result[0]["s3Bucket"] == "my-bucket"

    def test_collects_messages_across_multiple_batches(self, mock_sqs):
        batch1 = [_sqs_raw(_valid_body(f"evt-{i}")) for i in range(10)]
        batch2 = [_sqs_raw(_valid_body(f"evt-{i + 10}")) for i in range(5)]
        mock_sqs.receive_message.side_effect = [
            {"Messages": batch1},
            {"Messages": batch2},
            {"Messages": []},
        ]
        result = drain_sqs()
        assert len(result) == 15

    def test_receive_message_called_with_correct_params(self, mock_sqs):
        mock_sqs.receive_message.return_value = {"Messages": []}
        with patch.object(sqs_module, "QUEUE_URL", "https://sqs.test/queue"):
            drain_sqs()
        mock_sqs.receive_message.assert_called_with(
            QueueUrl="https://sqs.test/queue",
            MaxNumberOfMessages=10,
            WaitTimeSeconds=2,
            VisibilityTimeout=900,
        )

    def test_stops_polling_at_max_batches(self, mock_sqs):
        mock_sqs.receive_message.return_value = {"Messages": [_sqs_raw(_valid_body())]}
        drain_sqs(max_batches=3)
        assert mock_sqs.receive_message.call_count == 3

    def test_malformed_message_excluded_from_result(self, mock_sqs):
        bad = {"Body": "not {{ valid json", "ReceiptHandle": "rh-bad"}
        mock_sqs.receive_message.side_effect = [
            {"Messages": [bad]},
            {"Messages": []},
        ]
        result = drain_sqs()
        assert result == []

    def test_malformed_message_sent_to_dlq(self, mock_sqs):
        bad = {"Body": "not json", "ReceiptHandle": "rh-bad"}
        mock_sqs.receive_message.side_effect = [
            {"Messages": [bad]},
            {"Messages": []},
        ]
        with patch.object(sqs_module, "DLQ_URL", "https://sqs.test/dlq"):
            drain_sqs()
        mock_sqs.send_message.assert_called_once()
        kwargs = mock_sqs.send_message.call_args[1]
        assert kwargs["QueueUrl"] == "https://sqs.test/dlq"

    def test_malformed_message_deleted_after_dlq(self, mock_sqs):
        bad = {"Body": "bad body", "ReceiptHandle": "rh-del"}
        mock_sqs.receive_message.side_effect = [
            {"Messages": [bad]},
            {"Messages": []},
        ]
        drain_sqs()
        mock_sqs.delete_message.assert_called_once()
        kwargs = mock_sqs.delete_message.call_args[1]
        assert kwargs["ReceiptHandle"] == "rh-del"

    def test_malformed_message_still_deleted_when_dlq_send_fails(self, mock_sqs):
        bad = {"Body": "bad body", "ReceiptHandle": "rh-failsend"}
        mock_sqs.receive_message.side_effect = [
            {"Messages": [bad]},
            {"Messages": []},
        ]
        mock_sqs.send_message.side_effect = Exception("DLQ unreachable")
        drain_sqs()
        mock_sqs.delete_message.assert_called_once()

    def test_dlq_payload_includes_failure_reason(self, mock_sqs):
        bad = {"Body": "not json", "ReceiptHandle": "rh-1"}
        mock_sqs.receive_message.side_effect = [
            {"Messages": [bad]},
            {"Messages": []},
        ]
        drain_sqs()
        sent_body = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert "failureReason" in sent_body
        assert "originalMessage" in sent_body


class TestSendToDlq:
    def test_marks_event_as_failed_in_idempotency(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-1", "appId": "app-1", "event": "E1"}
        send_to_dlq(msg, 503, "Server Error")
        mock_update_status.assert_called_once_with("evt-1", "app-1", "FAILED")

    def test_sends_message_to_dlq_url(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-2", "appId": "app-2", "event": "E2"}
        with patch.object(sqs_module, "DLQ_URL", "https://sqs.test/dlq"):
            send_to_dlq(msg, 500, "Error")
        kwargs = mock_sqs.send_message.call_args[1]
        assert kwargs["QueueUrl"] == "https://sqs.test/dlq"

    def test_dlq_payload_contains_event_id(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-3", "appId": "app-3", "event": "E3"}
        send_to_dlq(msg, 422, "Unprocessable")
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert payload["eventId"] == "evt-3"

    def test_dlq_payload_status_is_failed(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-4", "appId": "app-4", "event": "E4"}
        send_to_dlq(msg, 500, "err")
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert payload["status"] == "FAILED"

    def test_dlq_payload_includes_status_code(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-5", "appId": "app-5", "event": "E5"}
        send_to_dlq(msg, 503, "Service unavailable")
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert payload["statusCode"] == 503

    def test_dlq_payload_includes_error_reason(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-6", "appId": "app-6", "event": "E6"}
        send_to_dlq(msg, 500, "boom")
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert payload["error"] == "boom"

    def test_dlq_payload_error_is_none_when_reason_is_none(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-7", "appId": "app-7", "event": "E7"}
        send_to_dlq(msg, 500, reason=None)
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert payload["error"] is None

    def test_dlq_payload_contains_all_required_fields(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-8", "appId": "app-8", "event": "E8", "receivedAt": "2024-01-01T00:00:00Z"}
        send_to_dlq(msg, 500, "reason")
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        for field in ("eventId", "event", "status", "attempts", "createdAt", "updatedAt", "statusCode", "error", "dlqReason", "dlqAt"):
            assert field in payload, f"Missing field: {field}"

    def test_dlq_payload_uses_received_at_as_created_at(self, mock_sqs, mock_update_status):
        msg = {"eventId": "evt-9", "appId": "app-9", "event": "E9", "receivedAt": "2024-06-01T12:00:00Z"}
        send_to_dlq(msg, 500, "err")
        payload = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert payload["createdAt"] == "2024-06-01T12:00:00Z"
