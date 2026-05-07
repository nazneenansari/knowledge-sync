"""
Tests for DLQ routes (GET /dev/admin/dlq, POST /dev/admin/dlq/{id}/replay)
and the underlying service functions.

Route tests mock at the `dlq.routes.*` import boundary.
Service tests mock at `dlq.service._sqs` / internal helpers.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from models.schemas import (
    DLQListResponse,
    EventStatusResponse,
    EventType,
    ReplayResponse,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_EVENT_ID_1 = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_EVENT_ID_2 = "7cb96a18-1234-4abc-9def-aabbccddeeff"
_NOW        = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_DLQ_URL    = os.environ["DLQ_URL"]
_QUEUE_URL  = os.environ["QUEUE_URL"]


def _make_event_status(
    event_id: str = _EVENT_ID_1,
    event_type: EventType = EventType.ARTICLE_PUBLISHED,
) -> EventStatusResponse:
    return EventStatusResponse(
        event_id=uuid.UUID(event_id),
        event=event_type,
        status_code=500,
        attempts=3,
        created_at=_NOW,
        updated_at=_NOW,
        dlq_reason="Max retry attempts exceeded",
        dlq_at=_NOW,
        meta={"tenantId": "acme-corp", "appId": "egain"},
    )


def _make_replay_response(
    original_id: str = _EVENT_ID_1,
    new_id: str = _EVENT_ID_2,
) -> ReplayResponse:
    return ReplayResponse(
        replayed_event_id=uuid.UUID(original_id),
        new_event_id=uuid.UUID(new_id),
        message="Event re-enqueued for reprocessing.",
    )


def _make_sqs_message(
    event_id: str = _EVENT_ID_1,
    event: str = "article.published",
    receipt_handle: str = "receipt-handle-1",
) -> dict:
    return {
        "MessageId": f"msg-{event_id[:8]}",
        "ReceiptHandle": receipt_handle,
        "Body": json.dumps({
            "eventId":    event_id,
            "event":      event,
            "tenantId":   "acme-corp",
            "appId":      "egain",
            "receivedAt": _NOW.isoformat(),
            "s3Bucket":   "test-bucket",
            "s3Key":      f"webhooks/raw/acme-corp/egain/article_published/{event_id}.json",
            "statusCode": "dead",
            "dlqReason":  "Max retry attempts exceeded",
        }),
        "Attributes": {
            "SentTimestamp":              str(int(_NOW.timestamp() * 1000)),
            "ApproximateReceiveCount":    "3",
        },
    }


# ---------------------------------------------------------------------------
# GET /dev/admin/dlq — route tests
# ---------------------------------------------------------------------------

class TestListDLQRoute:

    @patch("dlq.routes.list_dlq_events")
    def test_200_on_empty_queue(self, mock_list, client):
        mock_list.return_value = DLQListResponse(total=0, items=[])
        resp = client.get("/dev/admin/dlq")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    @patch("dlq.routes.list_dlq_events")
    def test_200_response_shape_with_items(self, mock_list, client):
        item = _make_event_status()
        mock_list.return_value = DLQListResponse(total=1, items=[item])
        resp = client.get("/dev/admin/dlq")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    @patch("dlq.routes.list_dlq_events")
    def test_no_event_filter_passes_none_to_service(self, mock_list, client):
        mock_list.return_value = DLQListResponse(total=0, items=[])
        client.get("/dev/admin/dlq")
        mock_list.assert_called_once_with(event_type=None)

    @patch("dlq.routes.list_dlq_events")
    def test_filter_article_published_passed_to_service(self, mock_list, client):
        mock_list.return_value = DLQListResponse(total=0, items=[])
        client.get("/dev/admin/dlq?event=egain.article.published")
        mock_list.assert_called_once_with(event_type=EventType.ARTICLE_PUBLISHED)

    @patch("dlq.routes.list_dlq_events")
    def test_filter_case_closed_passed_to_service(self, mock_list, client):
        mock_list.return_value = DLQListResponse(total=0, items=[])
        client.get("/dev/admin/dlq?event=salesforce.case.closed")
        mock_list.assert_called_once_with(event_type=EventType.CASE_CLOSED)

    @patch("dlq.routes.list_dlq_events")
    def test_filter_article_viewed_passed_to_service(self, mock_list, client):
        mock_list.return_value = DLQListResponse(total=0, items=[])
        client.get("/dev/admin/dlq?event=egain.article.viewed")
        mock_list.assert_called_once_with(event_type=EventType.ARTICLE_VIEWED)

    def test_422_on_invalid_event_type_value(self, client):
        resp = client.get("/dev/admin/dlq?event=completely.wrong")
        assert resp.status_code == 422

    @patch("dlq.routes.list_dlq_events")
    def test_multiple_items_returned_correctly(self, mock_list, client):
        items = [
            _make_event_status(_EVENT_ID_1, EventType.ARTICLE_PUBLISHED),
            _make_event_status(_EVENT_ID_2, EventType.CASE_CLOSED),
        ]
        mock_list.return_value = DLQListResponse(total=2, items=items)
        resp = client.get("/dev/admin/dlq")
        assert len(resp.json()["items"]) == 2

    @patch("dlq.routes.list_dlq_events", side_effect=Exception("SQS unavailable"))
    def test_500_on_service_exception(self, _mock, error_client):
        resp = error_client.get("/dev/admin/dlq")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /dev/admin/dlq/{event_id}/replay — route tests
# ---------------------------------------------------------------------------

class TestReplayDLQRoute:

    @patch("dlq.routes.replay_dlq_event")
    def test_202_on_successful_replay(self, mock_replay, client):
        mock_replay.return_value = _make_replay_response()
        resp = client.post(f"/dev/admin/dlq/{_EVENT_ID_1}/replay")
        assert resp.status_code == 202

    @patch("dlq.routes.replay_dlq_event")
    def test_response_body_has_correct_shape(self, mock_replay, client):
        mock_replay.return_value = _make_replay_response()
        body = client.post(f"/dev/admin/dlq/{_EVENT_ID_1}/replay").json()
        assert body["status"] == "accepted"
        assert body["replayedEventId"] == _EVENT_ID_1
        assert body["newEventId"] == _EVENT_ID_2
        assert body["message"] == "Event re-enqueued for reprocessing."

    @patch("dlq.routes.replay_dlq_event")
    def test_replayed_event_id_matches_path_param(self, mock_replay, client):
        target = "aaaabbbb-cccc-dddd-eeee-ffff00001111"
        mock_replay.return_value = _make_replay_response(original_id=target)
        body = client.post(f"/dev/admin/dlq/{target}/replay").json()
        assert body["replayedEventId"] == target

    @patch("dlq.routes.replay_dlq_event")
    def test_new_event_id_differs_from_replayed_id(self, mock_replay, client):
        mock_replay.return_value = _make_replay_response(_EVENT_ID_1, _EVENT_ID_2)
        body = client.post(f"/dev/admin/dlq/{_EVENT_ID_1}/replay").json()
        assert body["replayedEventId"] != body["newEventId"]

    @patch("dlq.routes.replay_dlq_event")
    def test_404_when_event_not_found_in_dlq(self, mock_replay, client):
        mock_replay.return_value = None
        resp = client.post(f"/dev/admin/dlq/{_EVENT_ID_1}/replay")
        assert resp.status_code == 404

    def test_422_on_non_uuid_event_id_in_path(self, client):
        resp = client.post("/dev/admin/dlq/not-a-uuid/replay")
        assert resp.status_code == 422

    @patch("dlq.routes.replay_dlq_event", side_effect=Exception("SQS unavailable"))
    def test_500_on_service_exception(self, _mock, error_client):
        resp = error_client.post(f"/dev/admin/dlq/{_EVENT_ID_1}/replay")
        assert resp.status_code == 500

    @patch("dlq.routes.replay_dlq_event")
    def test_service_receives_uuid_object_not_string(self, mock_replay, client):
        mock_replay.return_value = _make_replay_response()
        client.post(f"/dev/admin/dlq/{_EVENT_ID_1}/replay")
        received_id = mock_replay.call_args[1]["event_id"]
        assert isinstance(received_id, uuid.UUID)
        assert str(received_id) == _EVENT_ID_1


# ---------------------------------------------------------------------------
# Service unit tests — list_dlq_events
# ---------------------------------------------------------------------------

class TestListDLQService:

    @patch("dlq.service._parse_message")
    @patch("dlq.service._approximate_total", return_value=0)
    @patch("dlq.service._peek_messages", return_value=[])
    def test_empty_queue_returns_zero_items(self, _peek, _total, _parse):
        from dlq.service import list_dlq_events
        result = list_dlq_events()
        assert result.total == 0
        assert result.items == []
        _parse.assert_not_called()

    @patch("dlq.service._parse_message")
    @patch("dlq.service._approximate_total", return_value=2)
    @patch("dlq.service._peek_messages")
    def test_returns_all_parsed_items_when_no_filter(self, mock_peek, _total, mock_parse):
        from dlq.service import list_dlq_events
        mock_peek.return_value = [
            _make_sqs_message(_EVENT_ID_1, "article.published"),
            _make_sqs_message(_EVENT_ID_2, "case.closed", "r2"),
        ]
        mock_parse.side_effect = [
            _make_event_status(_EVENT_ID_1, EventType.ARTICLE_PUBLISHED),
            _make_event_status(_EVENT_ID_2, EventType.CASE_CLOSED),
        ]
        result = list_dlq_events()
        assert len(result.items) == 2
        assert result.total == 2

    @patch("dlq.service._parse_message")
    @patch("dlq.service._approximate_total", return_value=2)
    @patch("dlq.service._peek_messages")
    def test_filter_keeps_only_matching_event_type(self, mock_peek, _total, mock_parse):
        from dlq.service import list_dlq_events
        mock_peek.return_value = [
            _make_sqs_message(_EVENT_ID_1, "article.published"),
            _make_sqs_message(_EVENT_ID_2, "case.closed", "r2"),
        ]
        mock_parse.side_effect = [
            _make_event_status(_EVENT_ID_1, EventType.ARTICLE_PUBLISHED),
            _make_event_status(_EVENT_ID_2, EventType.CASE_CLOSED),
        ]
        result = list_dlq_events(event_type=EventType.ARTICLE_PUBLISHED)
        assert len(result.items) == 1
        assert result.items[0].event == EventType.ARTICLE_PUBLISHED

    @patch("dlq.service._parse_message")
    @patch("dlq.service._approximate_total", return_value=1)
    @patch("dlq.service._peek_messages")
    def test_filter_with_no_matches_returns_empty_items(self, mock_peek, _total, mock_parse):
        from dlq.service import list_dlq_events
        mock_peek.return_value = [_make_sqs_message(_EVENT_ID_1, "article.published")]
        mock_parse.return_value = _make_event_status(_EVENT_ID_1, EventType.ARTICLE_PUBLISHED)
        result = list_dlq_events(event_type=EventType.CASE_CLOSED)
        assert result.items == []

    @patch("dlq.service._parse_message")
    @patch("dlq.service._approximate_total", return_value=3)
    @patch("dlq.service._peek_messages")
    def test_total_reflects_approximate_queue_depth_not_filtered_count(self, mock_peek, _total, mock_parse):
        from dlq.service import list_dlq_events
        mock_peek.return_value = [_make_sqs_message(_EVENT_ID_1, "article.published")]
        mock_parse.return_value = _make_event_status(_EVENT_ID_1, EventType.ARTICLE_PUBLISHED)
        result = list_dlq_events(event_type=EventType.CASE_CLOSED)
        assert result.total == 3  # approximate total from SQS, not filtered count

    @patch("dlq.service._parse_message")
    @patch("dlq.service._approximate_total", return_value=1)
    @patch("dlq.service._peek_messages")
    def test_peek_called_with_max_sqs_batch(self, mock_peek, _total, _parse):
        from dlq.service import list_dlq_events, _MAX_SQS_BATCH
        mock_peek.return_value = []
        list_dlq_events()
        mock_peek.assert_called_once_with(_MAX_SQS_BATCH)


# ---------------------------------------------------------------------------
# Service unit tests — replay_dlq_event
# ---------------------------------------------------------------------------

class TestReplayDLQService:

    @patch("dlq.service._sqs")
    def test_returns_none_when_dlq_is_empty(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.return_value = {"Messages": []}
        assert replay_dlq_event(uuid.UUID(_EVENT_ID_1)) is None

    @patch("dlq.service._sqs")
    def test_returns_replay_response_when_event_found(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.return_value = {"Messages": [_make_sqs_message(_EVENT_ID_1)]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}

        result = replay_dlq_event(uuid.UUID(_EVENT_ID_1))

        assert result is not None
        assert str(result.replayed_event_id) == _EVENT_ID_1
        assert result.message == "Event re-enqueued for reprocessing."

    @patch("dlq.service._sqs")
    def test_new_event_id_is_a_fresh_uuid(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.return_value = {"Messages": [_make_sqs_message(_EVENT_ID_1)]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}

        result = replay_dlq_event(uuid.UUID(_EVENT_ID_1))

        assert isinstance(result.new_event_id, uuid.UUID)
        assert result.new_event_id != uuid.UUID(_EVENT_ID_1)

    @patch("dlq.service._sqs")
    def test_deletes_original_message_from_dlq(self, mock_sqs):
        from dlq.service import replay_dlq_event
        msg = _make_sqs_message(_EVENT_ID_1, receipt_handle="my-receipt")
        mock_sqs.receive_message.return_value = {"Messages": [msg]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}

        replay_dlq_event(uuid.UUID(_EVENT_ID_1))

        mock_sqs.delete_message.assert_called_once_with(
            QueueUrl=_DLQ_URL, ReceiptHandle="my-receipt"
        )

    @patch("dlq.service._sqs")
    def test_sends_new_event_to_main_queue(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.return_value = {"Messages": [_make_sqs_message(_EVENT_ID_1)]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}

        replay_dlq_event(uuid.UUID(_EVENT_ID_1))

        mock_sqs.send_message.assert_called_once()
        assert mock_sqs.send_message.call_args[1]["QueueUrl"] == _QUEUE_URL

    @patch("dlq.service._sqs")
    def test_sent_message_has_new_event_id(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.return_value = {"Messages": [_make_sqs_message(_EVENT_ID_1)]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}

        result = replay_dlq_event(uuid.UUID(_EVENT_ID_1))
        sent = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])

        assert sent["eventId"] == str(result.new_event_id)
        assert sent["eventId"] != _EVENT_ID_1

    @patch("dlq.service._sqs")
    def test_sent_message_has_replayed_from_in_meta(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.return_value = {"Messages": [_make_sqs_message(_EVENT_ID_1)]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}

        replay_dlq_event(uuid.UUID(_EVENT_ID_1))
        sent = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])

        assert sent["meta"]["replayed_from"] == _EVENT_ID_1
        assert "replayed_at" in sent["meta"]

    @patch("dlq.service._sqs")
    def test_non_matching_messages_are_returned_to_dlq(self, mock_sqs):
        from dlq.service import replay_dlq_event
        target = _make_sqs_message(_EVENT_ID_1, receipt_handle="r-target")
        other  = _make_sqs_message(_EVENT_ID_2, receipt_handle="r-other")
        mock_sqs.receive_message.return_value = {"Messages": [other, target]}
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}
        mock_sqs.change_message_visibility_batch.return_value = {}

        replay_dlq_event(uuid.UUID(_EVENT_ID_1))

        mock_sqs.change_message_visibility_batch.assert_called_once()
        entries = mock_sqs.change_message_visibility_batch.call_args[1]["Entries"]
        assert len(entries) == 1
        assert entries[0]["ReceiptHandle"] == "r-other"
        assert entries[0]["VisibilityTimeout"] == 0

    @patch("dlq.service._sqs")
    def test_scans_multiple_batches_until_event_found(self, mock_sqs):
        from dlq.service import replay_dlq_event
        batch1 = [_make_sqs_message(_EVENT_ID_2, receipt_handle="r2")]
        batch2 = [_make_sqs_message(_EVENT_ID_1, receipt_handle="r1")]
        mock_sqs.receive_message.side_effect = [
            {"Messages": batch1},
            {"Messages": batch2},
        ]
        mock_sqs.send_message.return_value = {}
        mock_sqs.delete_message.return_value = {}
        mock_sqs.change_message_visibility_batch.return_value = {}

        result = replay_dlq_event(uuid.UUID(_EVENT_ID_1))

        assert result is not None
        assert str(result.replayed_event_id) == _EVENT_ID_1
        assert mock_sqs.receive_message.call_count == 2

    @patch("dlq.service._sqs")
    def test_returns_none_after_exhausting_all_batches(self, mock_sqs):
        from dlq.service import replay_dlq_event
        mock_sqs.receive_message.side_effect = [
            {"Messages": [_make_sqs_message(_EVENT_ID_2, receipt_handle="r2")]},
            {"Messages": []},  # queue exhausted
        ]
        mock_sqs.change_message_visibility_batch.return_value = {}

        result = replay_dlq_event(uuid.UUID(_EVENT_ID_1))
        assert result is None


# ---------------------------------------------------------------------------
# Service unit tests — _peek_messages
# ---------------------------------------------------------------------------

class TestPeekMessages:

    @patch("dlq.service._sqs")
    def test_returns_empty_list_when_no_messages(self, mock_sqs):
        from dlq.service import _peek_messages
        mock_sqs.receive_message.return_value = {"Messages": []}
        assert _peek_messages(5) == []
        mock_sqs.change_message_visibility_batch.assert_not_called()

    @patch("dlq.service._sqs")
    def test_resets_visibility_after_receiving(self, mock_sqs):
        from dlq.service import _peek_messages
        msgs = [_make_sqs_message(_EVENT_ID_1, receipt_handle="r1")]
        mock_sqs.receive_message.return_value = {"Messages": msgs}
        mock_sqs.change_message_visibility_batch.return_value = {}

        _peek_messages(5)

        mock_sqs.change_message_visibility_batch.assert_called_once()
        entries = mock_sqs.change_message_visibility_batch.call_args[1]["Entries"]
        assert entries[0]["VisibilityTimeout"] == 0
        assert entries[0]["ReceiptHandle"] == "r1"

    @patch("dlq.service._sqs")
    def test_caps_request_at_sqs_batch_limit(self, mock_sqs):
        from dlq.service import _peek_messages, _MAX_SQS_BATCH
        mock_sqs.receive_message.return_value = {"Messages": []}

        _peek_messages(100)

        called_with = mock_sqs.receive_message.call_args[1]["MaxNumberOfMessages"]
        assert called_with == _MAX_SQS_BATCH
