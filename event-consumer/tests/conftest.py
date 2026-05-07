"""Root test conftest — sets required env vars at module level so config.py can be imported."""
import os

# Boto3 requires region + dummy credentials at import time (module-level client creation)
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test-key-id")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test-secret-key")

os.environ.setdefault("IDEMPOTENCY_TABLE", "test-idempotency-table")
os.environ.setdefault("OAUTH_TOKEN_URL", "https://auth.test.example.com/token")
os.environ.setdefault("CLIENT_ID", "test-client-id")
os.environ.setdefault("CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("API_URL", "https://api.test.example.com/events")
os.environ.setdefault("QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/000000000000/test-queue")
os.environ.setdefault("DLQ_URL", "https://sqs.us-east-1.amazonaws.com/000000000000/test-dlq")
