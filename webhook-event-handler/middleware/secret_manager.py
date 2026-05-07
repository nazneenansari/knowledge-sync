import json
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

app_secret_keys: dict = {
    "egain": "EGAIN_WEBHOOK_HMAC_SECRET",
    "test": "TEST_WEBHOOK_HMAC_SECRET",
}

def get_secret(secret_name: str, app_id: str) -> str:
    """Fetch a single key from an AWS Secrets Manager JSON secret."""
    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        logger.error("Failed to fetch secret %s: %s", secret_name, e.response["Error"]["Code"])
        raise

    if "SecretString" in response:
        raw = response["SecretString"]
    else:
        raw = response["SecretBinary"].decode("utf-8")

    secret_dict = json.loads(raw)
    key = app_secret_keys[app_id]

    if key not in secret_dict:
        raise KeyError(f"Key '{key}' not found in secret '{secret_name}'")

    return secret_dict[key]
