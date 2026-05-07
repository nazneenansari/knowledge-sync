"""DynamoDB-backed idempotency guard — prevents duplicate event processing across Lambda retries."""
import time
import logging
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

from config import IDEMPOTENCY_TABLE, STALE_LOCK_TIMEOUT_SECONDS, IDEMPOTENCY_TTL_SECONDS

logger = logging.getLogger(__name__)
dynamodb = boto3.client("dynamodb")


def acquire_idempotency(msg: dict) -> bool | None:
    """
    Returns:
        True  -> claim acquired, process event
        False -> already completed, skip & delete
        None  -> currently processing, do NOT delete
    """
    tenant_id   = msg["tenantId"]
    app_id      = msg.get("appId", "")
    event_name  = msg.get("event", "")
    event_id    = msg["eventId"]
    received_at = msg.get("receivedAt", datetime.now(timezone.utc).isoformat())

    logger.info("event=%s eventId=%s", event_name, event_id)

    try:
        dynamodb.put_item(
            TableName=IDEMPOTENCY_TABLE,
            Item={
                "eventId":    {"S": event_id},
                "appId":      {"S": app_id},
                "tenantId":   {"S": tenant_id},
                "event":      {"S": event_name},
                "receivedAt": {"S": received_at},
                "status":     {"S": "PROCESSING"},
                "updatedAt":  {"N": str(int(time.time()))},
                "expiresAt":  {"N": str(int(time.time()) + IDEMPOTENCY_TTL_SECONDS)},
            },
            ConditionExpression="attribute_not_exists(#pk) AND attribute_not_exists(#sk)",
            ExpressionAttributeNames={"#pk": "eventId", "#sk": "appId"},
        )
        return True

    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        resp = dynamodb.get_item(
            TableName=IDEMPOTENCY_TABLE,
            Key={"eventId": {"S": event_id}, "appId": {"S": app_id}},
        )

        item = resp.get("Item")
        if not item:
            logger.warning("Idempotency item missing after conflict for eventId=%s, retrying", event_id)
            return True

        status = item["status"]["S"]

        if status == "COMPLETED":
            return False

        if status == "PROCESSING":
            updated_at = int(item.get("updatedAt", {}).get("N", "0") or 0)
            if time.time() - updated_at > STALE_LOCK_TIMEOUT_SECONDS:
                logger.warning("Stale lock for eventId=%s, retrying", event_id)
                return True
            return None

        return False


def update_status(event_id: str, app_id: str, status: str):
    """Updates the processing status of an event in the idempotency table."""
    logger.info("eventId=%s status=%s", event_id, status)
    dynamodb.update_item(
        TableName=IDEMPOTENCY_TABLE,
        Key={"eventId": {"S": event_id}, "appId": {"S": app_id}},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": {"S": status}},
    )
