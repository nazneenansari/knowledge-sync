"""Tests for middleware/s3_client.py — S3 payload fetching."""
import json
from unittest.mock import patch, MagicMock
import pytest

import middleware.s3_client as s3_module
from middleware.s3_client import fetch_payload


@pytest.fixture
def mock_s3():
    with patch.object(s3_module, "s3") as mock_client:
        yield mock_client


class TestFetchPayload:
    def test_returns_parsed_json_dict(self, mock_s3):
        data = {"tenantId": "t-1", "event": "UserCreated"}
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(data).encode())}
        result = fetch_payload("path/key.json", "my-bucket")
        assert result == data

    def test_calls_get_object_with_correct_bucket_and_key(self, mock_s3):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"{}")}
        fetch_payload("events/2024/key.json", "event-store-bucket")
        mock_s3.get_object.assert_called_once_with(
            Bucket="event-store-bucket",
            Key="events/2024/key.json",
        )

    def test_returns_nested_payload(self, mock_s3):
        data = {"outer": {"inner": [1, 2, 3]}}
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(data).encode())}
        result = fetch_payload("key", "bucket")
        assert result["outer"]["inner"] == [1, 2, 3]

    def test_propagates_s3_exception(self, mock_s3):
        mock_s3.get_object.side_effect = Exception("Access Denied")
        with pytest.raises(Exception, match="Access Denied"):
            fetch_payload("key", "bucket")

    def test_propagates_json_decode_error_on_invalid_body(self, mock_s3):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"not valid json")}
        with pytest.raises(json.JSONDecodeError):
            fetch_payload("key", "bucket")
