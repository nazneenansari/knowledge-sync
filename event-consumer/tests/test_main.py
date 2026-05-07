"""Tests for main.py — lambda_handler orchestration across all processing paths."""
from unittest.mock import patch, call
import pytest

import main


def _make_msg(
    event_id: str = "evt-1",
    app_id: str = "app-1",
    tenant_id: str = "t-1",
    receipt: str = "rh-1",
) -> dict:
    return {
        "tenantId": tenant_id,
        "eventId": event_id,
        "appId": app_id,
        "event": "TestEvent",
        "s3Key": "path/key.json",
        "s3Bucket": "my-bucket",
        "receipt_handle": receipt,
    }


@pytest.fixture(autouse=True)
def deps():
    """Patches all external dependencies used by lambda_handler."""
    with patch("main.drain_sqs") as mock_drain, \
         patch("main.acquire_idempotency") as mock_acquire, \
         patch("main.update_status") as mock_update, \
         patch("main.fetch_payload") as mock_fetch, \
         patch("main.call_external_api_with_retry") as mock_api, \
         patch("main.delete_message") as mock_delete, \
         patch("main.send_to_dlq") as mock_dlq:
        yield {
            "drain_sqs": mock_drain,
            "acquire_idempotency": mock_acquire,
            "update_status": mock_update,
            "fetch_payload": mock_fetch,
            "call_external_api_with_retry": mock_api,
            "delete_message": mock_delete,
            "send_to_dlq": mock_dlq,
        }


class TestNoMessages:
    def test_returns_all_zeros_when_queue_empty(self, deps):
        deps["drain_sqs"].return_value = []
        result = main.lambda_handler({}, {})
        assert result == {"processed": 0, "skipped": 0, "failed": 0}

    def test_does_not_call_idempotency_when_empty(self, deps):
        deps["drain_sqs"].return_value = []
        main.lambda_handler({}, {})
        deps["acquire_idempotency"].assert_not_called()

    def test_does_not_call_api_when_empty(self, deps):
        deps["drain_sqs"].return_value = []
        main.lambda_handler({}, {})
        deps["call_external_api_with_retry"].assert_not_called()


class TestSuccessfulProcessing:
    def test_increments_processed_on_2xx(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {"data": "value"}
        deps["call_external_api_with_retry"].return_value = (200, '{"ok": true}')
        result = main.lambda_handler({}, {})
        assert result == {"processed": 1, "skipped": 0, "failed": 0}

    def test_201_also_counts_as_processed(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (201, "created")
        result = main.lambda_handler({}, {})
        assert result["processed"] == 1

    def test_updates_status_to_completed(self, deps):
        deps["drain_sqs"].return_value = [_make_msg("evt-1", "app-1")]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (200, "ok")
        main.lambda_handler({}, {})
        deps["update_status"].assert_called_once_with("evt-1", "app-1", "COMPLETED")

    def test_deletes_message_on_success(self, deps):
        deps["drain_sqs"].return_value = [_make_msg(receipt="rh-success")]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (200, "ok")
        main.lambda_handler({}, {})
        deps["delete_message"].assert_called_once_with("rh-success")

    def test_fetches_payload_with_s3_key_and_bucket(self, deps):
        msg = _make_msg()
        deps["drain_sqs"].return_value = [msg]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (200, "ok")
        main.lambda_handler({}, {})
        deps["fetch_payload"].assert_called_once_with("path/key.json", "my-bucket")


class TestIdempotencySkip:
    def test_already_completed_increments_skipped(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = False
        result = main.lambda_handler({}, {})
        assert result == {"processed": 0, "skipped": 1, "failed": 0}

    def test_already_completed_deletes_message(self, deps):
        deps["drain_sqs"].return_value = [_make_msg(receipt="rh-done")]
        deps["acquire_idempotency"].return_value = False
        main.lambda_handler({}, {})
        deps["delete_message"].assert_called_once_with("rh-done")

    def test_already_completed_does_not_call_api(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = False
        main.lambda_handler({}, {})
        deps["call_external_api_with_retry"].assert_not_called()

    def test_in_flight_increments_skipped(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = None
        result = main.lambda_handler({}, {})
        assert result == {"processed": 0, "skipped": 1, "failed": 0}

    def test_in_flight_does_not_delete_message(self, deps):
        deps["drain_sqs"].return_value = [_make_msg(receipt="rh-inflight")]
        deps["acquire_idempotency"].return_value = None
        main.lambda_handler({}, {})
        deps["delete_message"].assert_not_called()

    def test_in_flight_does_not_call_api(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = None
        main.lambda_handler({}, {})
        deps["call_external_api_with_retry"].assert_not_called()


class TestClientErrorResponse:
    def test_4xx_increments_failed(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (400, "bad request")
        result = main.lambda_handler({}, {})
        assert result == {"processed": 0, "skipped": 0, "failed": 1}

    def test_4xx_sends_to_dlq(self, deps):
        msg = _make_msg("evt-x", "app-x", receipt="rh-x")
        deps["drain_sqs"].return_value = [msg]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (422, "unprocessable")
        main.lambda_handler({}, {})
        deps["send_to_dlq"].assert_called_once_with(msg, 422, "Client error")

    def test_4xx_deletes_message(self, deps):
        deps["drain_sqs"].return_value = [_make_msg(receipt="rh-4xx")]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (404, "not found")
        main.lambda_handler({}, {})
        deps["delete_message"].assert_called_once_with("rh-4xx")

    def test_4xx_does_not_call_update_status(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (400, "bad")
        main.lambda_handler({}, {})
        deps["update_status"].assert_not_called()


class TestExceptionHandling:
    def test_fetch_payload_exception_increments_failed(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].side_effect = Exception("S3 access denied")
        result = main.lambda_handler({}, {})
        assert result == {"processed": 0, "skipped": 0, "failed": 1}

    def test_api_exception_increments_failed(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].side_effect = RuntimeError("network error")
        result = main.lambda_handler({}, {})
        assert result == {"processed": 0, "skipped": 0, "failed": 1}

    def test_exception_sends_to_dlq_with_exception_reason(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].side_effect = RuntimeError("fetch failed")
        main.lambda_handler({}, {})
        _, kwargs = deps["send_to_dlq"].call_args
        assert kwargs["reason"] == "RuntimeError: fetch failed"

    def test_exception_sends_to_dlq_with_none_status_code(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].side_effect = Exception("generic error")
        main.lambda_handler({}, {})
        _, kwargs = deps["send_to_dlq"].call_args
        assert kwargs["status_code"] is None

    def test_exception_uses_exception_status_code_if_available(self, deps):
        from service.api_client import ExternalAPIException
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].side_effect = ExternalAPIException(503, "retries exceeded")
        main.lambda_handler({}, {})
        _, kwargs = deps["send_to_dlq"].call_args
        assert kwargs["status_code"] == 503

    def test_exception_still_deletes_message(self, deps):
        deps["drain_sqs"].return_value = [_make_msg(receipt="rh-exc")]
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].side_effect = Exception("S3 error")
        main.lambda_handler({}, {})
        deps["delete_message"].assert_called_once_with("rh-exc")

    def test_acquire_idempotency_exception_increments_failed(self, deps):
        deps["drain_sqs"].return_value = [_make_msg()]
        deps["acquire_idempotency"].side_effect = Exception("DynamoDB unreachable")
        result = main.lambda_handler({}, {})
        assert result["failed"] == 1


class TestMultipleMessages:
    def test_counts_mixed_outcomes_correctly(self, deps):
        msgs = [
            _make_msg("evt-1", receipt="rh-1"),
            _make_msg("evt-2", receipt="rh-2"),
            _make_msg("evt-3", receipt="rh-3"),
            _make_msg("evt-4", receipt="rh-4"),
        ]
        deps["drain_sqs"].return_value = msgs
        deps["acquire_idempotency"].side_effect = [True, False, None, True]
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (200, "ok")
        result = main.lambda_handler({}, {})
        assert result == {"processed": 2, "skipped": 2, "failed": 0}

    def test_failure_in_one_message_does_not_stop_others(self, deps):
        msgs = [_make_msg("evt-1"), _make_msg("evt-2"), _make_msg("evt-3")]
        deps["drain_sqs"].return_value = msgs
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].side_effect = [Exception("boom"), {}, {}]
        deps["call_external_api_with_retry"].return_value = (200, "ok")
        result = main.lambda_handler({}, {})
        assert result == {"processed": 2, "skipped": 0, "failed": 1}

    def test_all_messages_processed_when_all_succeed(self, deps):
        msgs = [_make_msg(f"evt-{i}", receipt=f"rh-{i}") for i in range(5)]
        deps["drain_sqs"].return_value = msgs
        deps["acquire_idempotency"].return_value = True
        deps["fetch_payload"].return_value = {}
        deps["call_external_api_with_retry"].return_value = (200, "ok")
        result = main.lambda_handler({}, {})
        assert result == {"processed": 5, "skipped": 0, "failed": 0}
