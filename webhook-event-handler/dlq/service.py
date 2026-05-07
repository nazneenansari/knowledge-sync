"""
dlq/service.py
--------------
SQS Dead Letter Queue operations: list events (peek) and replay a specific event.
"""

import json
import logging
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import boto3

from models.schemas import (
    DLQListResponse,
    EventStatus,
    EventStatusResponse,
    EventType,
    ReplayResponse,
)

logger = logging.getLogger(__name__)

DLQ_URL   = os.environ["DLQ_URL"]
QUEUE_URL = os.environ["QUEUE_URL"]

_sqs = boto3.client("sqs")

_MAX_SQS_BATCH = 10

_EVENT_TYPE_MAP: dict[str, EventType] = {
    "article.published":        EventType.ARTICLE_PUBLISHED,
    "case.closed":              EventType.CASE_CLOSED,
    "article.viewed":           EventType.ARTICLE_VIEWED,
}


def _sqs_timestamp(attrs: dict, key: str) -> datetime:
    """Convert an SQS epoch-milliseconds attribute to a UTC datetime."""
    raw = attrs.get(key)
    if raw:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _parse_message(msg: dict) -> EventStatusResponse:
    body  = json.loads(msg["Body"])
    print(body)
    attrs = msg.get("Attributes", {})

    sent_at = _sqs_timestamp(attrs, "SentTimestamp")

    received_raw = body.get("receivedAt")
    created_at   = datetime.fromisoformat(received_raw) if received_raw else sent_at

    event_type = _EVENT_TYPE_MAP.get(body.get("event", ""), EventType.ARTICLE_PUBLISHED)

    attempts = int(attrs.get("ApproximateReceiveCount", body.get("attempts", 1)))

    return EventStatusResponse(
        event_id=UUID(body["eventId"]),
        event=event_type,
        status_code=body["statusCode"],
        attempts=attempts,
        created_at=created_at,
        updated_at=sent_at,
        error=body.get("lastError"),
        dlq_reason=body.get("dlqReason", "Max retry attempts exceeded"),
        dlq_at=sent_at,
        meta={
            "tenantId": body.get("tenantId"),
            "appId":    body.get("appId")
        },
    )


def _peek_messages(max_count: int) -> list[dict]:
    """Receive up to max_count messages and immediately reset their visibility."""
    response = _sqs.receive_message(
        QueueUrl=DLQ_URL,
        MaxNumberOfMessages=min(max_count, _MAX_SQS_BATCH),
        AttributeNames=["SentTimestamp", "ApproximateReceiveCount"],
        VisibilityTimeout=30,
    )
    messages = response.get("Messages", [])
    if messages:
        _sqs.change_message_visibility_batch(
            QueueUrl=DLQ_URL,
            Entries=[
                {"Id": str(i), "ReceiptHandle": m["ReceiptHandle"], "VisibilityTimeout": 0}
                for i, m in enumerate(messages)
            ],
        )
    return messages


def _approximate_total() -> int:
    attrs = _sqs.get_queue_attributes(
        QueueUrl=DLQ_URL,
        AttributeNames=["ApproximateNumberOfMessages"],
    )
    return int(attrs["Attributes"].get("ApproximateNumberOfMessages", 0))


def list_dlq_events(event_type: EventType | None = None) -> DLQListResponse:
    """
    Return up to 10 DLQ events (SQS batch ceiling) without permanently removing them.
    When `event_type` is provided, only events of that type are returned.
    """
    messages = _peek_messages(_MAX_SQS_BATCH)
    total    = _approximate_total()
    items    = [_parse_message(m) for m in messages]
    if event_type is not None:
        items = [item for item in items if item.event == event_type]
    return DLQListResponse(total=total, items=items)


def replay_dlq_event(event_id: UUID) -> ReplayResponse | None:
    """
    Scan the DLQ for a message whose `eventId` matches, delete it, and
    re-enqueue a new copy (new UUID) to the main processing queue.
    Returns None if the event is not found.
    """
    target_id = str(event_id)

    while True:
        response = _sqs.receive_message(
            QueueUrl=DLQ_URL,
            MaxNumberOfMessages=_MAX_SQS_BATCH,
            AttributeNames=["SentTimestamp"],
            VisibilityTimeout=30,
        )
        messages = response.get("Messages", [])
        if not messages:
            return None

        not_matched: list[dict] = []
        matched_msg: dict | None = None
        matched_body: dict | None = None

        for msg in messages:
            body = json.loads(msg["Body"])
            if body.get("eventId") == target_id:
                matched_msg  = msg
                matched_body = body
            else:
                not_matched.append(msg)

        # Put back any messages we are not replaying
        if not_matched:
            _sqs.change_message_visibility_batch(
                QueueUrl=DLQ_URL,
                Entries=[
                    {"Id": str(i), "ReceiptHandle": m["ReceiptHandle"], "VisibilityTimeout": 0}
                    for i, m in enumerate(not_matched)
                ],
            )

        if matched_msg and matched_body:
            new_event_id = uuid4()
            enriched = {
                **matched_body,
                "eventId": str(new_event_id),
                "meta": {
                    **matched_body.get("meta", {}),
                    "replayed_from": target_id,
                    "replayed_at":   datetime.now(timezone.utc).isoformat(),
                },
            }
            _sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(enriched))
            _sqs.delete_message(QueueUrl=DLQ_URL, ReceiptHandle=matched_msg["ReceiptHandle"])
            logger.info("Replayed DLQ event %s → new event %s", target_id, new_event_id)
            return ReplayResponse(
                replayed_event_id=event_id,
                new_event_id=new_event_id,
                message="Event re-enqueued for reprocessing.",
            )
