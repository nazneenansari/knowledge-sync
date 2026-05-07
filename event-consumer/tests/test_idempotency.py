"""Tests for middleware/idempotency.py — DynamoDB-backed idempotency guard."""
import time
from unittest.mock import patch
from botocore.exceptions import ClientError
import pytest

import middleware.idempotency as idempotency_module
from middleware.idempotency import acquire_idempotency, update_status


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test error"}}, "put_item")


def _make_msg(
    event_id: str = "evt-001",
    app_id: str = "app-001",
    tenant_id: str = "tenant-001",
    event: str = "UserCreated",
) -> dict:
    return {
        "eventId": event_id,
        "appId": app_id,
        "tenantId": tenant_id,
        "event": event,
        "receivedAt": "2024-01-01T00:00:00+00:00",
        "s3Key": "events/key.json",
        "s3Bucket": "events-bucket",
        "receipt_handle": "rh-001",
    }


@pytest.fixture
def mock_dynamodb():
    with patch.object(idempotency_module, "dynamodb") as mock_db:
        yield mock_db


class TestAcquireIdempotencyNewEvent:
    def test_new_event_returns_true(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        result = acquire_idempotency(_make_msg())
        assert result is True

    def test_new_event_calls_put_item(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        acquire_idempotency(_make_msg())
        mock_dynamodb.put_item.assert_called_once()

    def test_put_item_uses_correct_table(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        with patch.object(idempotency_module, "IDEMPOTENCY_TABLE", "my-table"):
            acquire_idempotency(_make_msg())
        kwargs = mock_dynamodb.put_item.call_args[1]
        assert kwargs["TableName"] == "my-table"

    def test_put_item_sets_status_to_processing(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        acquire_idempotency(_make_msg())
        kwargs = mock_dynamodb.put_item.call_args[1]
        assert kwargs["Item"]["status"]["S"] == "PROCESSING"

    def test_put_item_sets_event_id_and_app_id(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        acquire_idempotency(_make_msg("evt-xyz", "app-xyz"))
        kwargs = mock_dynamodb.put_item.call_args[1]
        assert kwargs["Item"]["eventId"]["S"] == "evt-xyz"
        assert kwargs["Item"]["appId"]["S"] == "app-xyz"

    def test_put_item_uses_conditional_expression(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        acquire_idempotency(_make_msg())
        kwargs = mock_dynamodb.put_item.call_args[1]
        assert "attribute_not_exists" in kwargs["ConditionExpression"]

    def test_uses_now_as_received_at_when_missing(self, mock_dynamodb):
        mock_dynamodb.put_item.return_value = {}
        msg = {k: v for k, v in _make_msg().items() if k != "receivedAt"}
        acquire_idempotency(msg)
        kwargs = mock_dynamodb.put_item.call_args[1]
        assert "receivedAt" in kwargs["Item"]


class TestAcquireIdempotencyAlreadyCompleted:
    def test_completed_event_returns_false(self, mock_dynamodb):
        mock_dynamodb.put_item.side_effect = _client_error("ConditionalCheckFailedException")
        mock_dynamodb.get_item.return_value = {
            "Item": {"status": {"S": "COMPLETED"}, "updatedAt": {"N": str(int(time.time()))}}
        }
        result = acquire_idempotency(_make_msg())
        assert result is False

    def test_unknown_status_returns_false(self, mock_dynamodb):
        mock_dynamodb.put_item.side_effect = _client_error("ConditionalCheckFailedException")
        mock_dynamodb.get_item.return_value = {
            "Item": {"status": {"S": "INVALID_STATUS"}, "updatedAt": {"N": str(int(time.time()))}}
        }
        result = acquire_idempotency(_make_msg())
        assert result is False


class TestAcquireIdempotencyInFlight:
    def test_processing_with_fresh_lock_returns_none(self, mock_dynamodb):
        mock_dynamodb.put_item.side_effect = _client_error("ConditionalCheckFailedException")
        mock_dynamodb.get_item.return_value = {
            "Item": {"status": {"S": "PROCESSING"}, "updatedAt": {"N": str(int(time.time()))}}
        }
        result = acquire_idempotency(_make_msg())
        assert result is None

    def test_processing_with_stale_lock_returns_true(self, mock_dynamodb):
        stale_time = int(time.time()) - 1800
        mock_dynamodb.put_item.side_effect = _client_error("ConditionalCheckFailedException")
        mock_dynamodb.get_item.return_value = {
            "Item": {"status": {"S": "PROCESSING"}, "updatedAt": {"N": str(stale_time)}}
        }
        with patch.object(idempotency_module, "STALE_LOCK_TIMEOUT_SECONDS", 900):
            result = acquire_idempotency(_make_msg())
        assert result is True

    def test_item_missing_after_conflict_returns_true(self, mock_dynamodb):
        mock_dynamodb.put_item.side_effect = _client_error("ConditionalCheckFailedException")
        mock_dynamodb.get_item.return_value = {}
        result = acquire_idempotency(_make_msg())
        assert result is True


class TestAcquireIdempotencyErrors:
    def test_non_conditional_client_error_is_reraised(self, mock_dynamodb):
        mock_dynamodb.put_item.side_effect = _client_error("ProvisionedThroughputExceededException")
        with pytest.raises(ClientError):
            acquire_idempotency(_make_msg())

    def test_get_item_called_with_correct_key_after_conflict(self, mock_dynamodb):
        mock_dynamodb.put_item.side_effect = _client_error("ConditionalCheckFailedException")
        mock_dynamodb.get_item.return_value = {
            "Item": {"status": {"S": "COMPLETED"}, "updatedAt": {"N": str(int(time.time()))}}
        }
        with patch.object(idempotency_module, "IDEMPOTENCY_TABLE", "my-table"):
            acquire_idempotency(_make_msg("evt-abc", "app-abc"))
        kwargs = mock_dynamodb.get_item.call_args[1]
        assert kwargs["TableName"] == "my-table"
        assert kwargs["Key"]["eventId"]["S"] == "evt-abc"
        assert kwargs["Key"]["appId"]["S"] == "app-abc"


class TestUpdateStatus:
    def test_calls_update_item(self, mock_dynamodb):
        update_status("evt-1", "app-1", "COMPLETED")
        mock_dynamodb.update_item.assert_called_once()

    def test_update_item_uses_correct_table(self, mock_dynamodb):
        with patch.object(idempotency_module, "IDEMPOTENCY_TABLE", "target-table"):
            update_status("evt-1", "app-1", "COMPLETED")
        kwargs = mock_dynamodb.update_item.call_args[1]
        assert kwargs["TableName"] == "target-table"

    def test_update_item_sets_correct_key(self, mock_dynamodb):
        update_status("evt-123", "app-456", "COMPLETED")
        kwargs = mock_dynamodb.update_item.call_args[1]
        assert kwargs["Key"] == {"eventId": {"S": "evt-123"}, "appId": {"S": "app-456"}}

    def test_update_item_sets_status_value(self, mock_dynamodb):
        update_status("evt-1", "app-1", "COMPLETED")
        kwargs = mock_dynamodb.update_item.call_args[1]
        assert kwargs["ExpressionAttributeValues"][":s"]["S"] == "COMPLETED"

    def test_update_item_sets_failed_status(self, mock_dynamodb):
        update_status("evt-1", "app-1", "FAILED")
        kwargs = mock_dynamodb.update_item.call_args[1]
        assert kwargs["ExpressionAttributeValues"][":s"]["S"] == "FAILED"

    def test_update_item_uses_set_expression(self, mock_dynamodb):
        update_status("evt-1", "app-1", "COMPLETED")
        kwargs = mock_dynamodb.update_item.call_args[1]
        assert kwargs["UpdateExpression"] == "SET #s = :s"
        assert kwargs["ExpressionAttributeNames"] == {"#s": "status"}
