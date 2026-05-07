"""SQS helpers for draining the work queue and routing unprocessable messages to the DLQ."""
import json
import time
import logging
import boto3
from datetime import datetime, timezone

from config import QUEUE_URL, DLQ_URL
from middleware.idempotency import update_status

logger = logging.getLogger(__name__)
sqs = boto3.client("sqs")


def delete_message(receipt_handle: str):
    logger.debug(f"Deleting SQS message: {receipt_handle}")
    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)


def drain_sqs(max_batches: int = 50) -> list[dict]:
    """Polls QUEUE_URL in batches of 10 until the queue is empty or max_batches is reached; malformed messages are DLQ'd and deleted."""
    collected = []
    batches = 0

    while batches < max_batches:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=2,
            VisibilityTimeout=900
        )

        messages = response.get("Messages", [])
        if not messages:
            break

        for msg in messages:
            try:
                body = json.loads(msg["Body"])
                collected.append({
                    "tenantId": body["tenantId"],
                    "eventId": body["eventId"],
                    "appId":  body["appId"],
                    "event":  body["event"],
                    "s3Key": body["s3Key"],
                    "s3Bucket": body["s3Bucket"],
                    "receipt_handle": msg["ReceiptHandle"]
                })
            except Exception as e:
                logger.exception(f"Bad SQS message format, routing to DLQ: {e}")
                try:
                    sqs.send_message(
                        QueueUrl=DLQ_URL,
                        MessageBody=json.dumps({
                            "originalMessage": msg.get("Body"),
                            "failureReason": f"{type(e).__name__}: {str(e)}",
                            "timestamp": int(time.time())
                        })
                    )
                except Exception as dlq_err:
                    logger.error(f"Failed to DLQ malformed message: {dlq_err}")
                finally:
                    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])

        batches += 1

    if batches == max_batches:
        logger.warning(f"Reached max_batches={max_batches}, queue may have more unprocessed messages")
    logger.info(f"Drained {len(collected)} messages from SQS")
    return collected


def send_to_dlq(message: dict, status_code: str, reason=None, attempts=1):
    """Marks event as FAILED in idempotency table and forwards enriched payload to DLQ_URL."""
    now = datetime.now(timezone.utc).isoformat()
    event_id = message.get("eventId")
    app_id = message.get("appId")
    update_status(event_id, app_id, "FAILED")

    dlq_payload = {
        "eventId":   event_id,
        "event":     message.get("event"),
        "status":    "FAILED",
        "attempts":  attempts,
        "createdAt": message.get("receivedAt", now),
        "updatedAt": now,
        "statusCode": status_code,
        "error":     str(reason) if reason else None,
        "dlqReason": f"Max retry attempts ({attempts}) exceeded" if reason else reason,
        "dlqAt":     now,
    }

    logger.error(
        "Sending to DLQ | eventId=%s | event=%s | attempts=%s | reason=%s",
        event_id, message.get("event"), attempts, reason,
    )

    sqs.send_message(QueueUrl=DLQ_URL, MessageBody=json.dumps(dlq_payload))