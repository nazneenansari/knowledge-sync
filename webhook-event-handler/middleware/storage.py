import json

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")

class DuplicateEventError(Exception):
    """Raised when an event with the same eventId already exists in S3."""


def store_webhook(bucket_name: str, body: dict) -> str:
    """Write the raw webhook payload to S3; raise DuplicateEventError if the key already exists."""
    tenant_id  = body["tenantId"]
    app_id     = body["appId"]
    event_id   = body["eventId"]
    event_name = body["event"].replace(".", "_")

    key = f"webhooks/raw/{tenant_id}/{app_id}/{event_name}/{event_id}.json"

    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=json.dumps(body),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "PreconditionFailed":
            raise DuplicateEventError(f"Event {event_id!r} already exists")
        raise
    return key