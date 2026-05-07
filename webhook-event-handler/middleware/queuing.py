import json
from datetime import datetime, timezone

import boto3

sqs = boto3.client("sqs")

def enqueue_event(queue_url: str, bucket_name: str, s3_key: str, body: dict) -> str:
    """Send an SQS message referencing the stored S3 object."""
    tenant_id = body["tenantId"]
    event_id  = body["eventId"]
    appId     = body["appId"]
    event_name = body["event"]

    message = {
        "tenantId":   tenant_id,
        "appId":      appId,
        "event":      event_name,
        "eventId":    event_id,
        "receivedAt": datetime.now(timezone.utc).isoformat(),
        "s3Bucket":   bucket_name,
        "s3Key":      s3_key,
    }
    sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))