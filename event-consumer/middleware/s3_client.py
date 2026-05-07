"""S3 helper for fetching event payloads stored by upstream producers."""
import json
import logging
import boto3

logger = logging.getLogger(__name__)
s3 = boto3.client("s3")


def fetch_payload(s3_key: str, s3_bucket: str) -> dict:
    logger.info(f"Fetching payload from s3://{s3_bucket}/{s3_key}")
    obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    return json.loads(obj["Body"].read())
