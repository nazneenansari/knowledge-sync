import os

# Must be set before any application module is imported.
# webhooks.py reads BUCKET_NAME / QUEUE_URL at module load time via os.environ[...].
# storage.py / queuing.py create boto3 clients at module level, which require a region.
os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789/test-queue")
os.environ.setdefault("DLQ_URL",   "https://sqs.us-east-1.amazonaws.com/123456789/test-dlq")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test-key-id")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test-secret-key")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def app():
    from main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    """TestClient with HMAC signature verification bypassed."""
    from webhooks import _verify

    async def _bypass():
        return None

    app.dependency_overrides[_verify] = _bypass
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(_verify, None)


@pytest.fixture
def unauthed_client(app):
    """TestClient with real HMAC signature verification (no bypass)."""
    from webhooks import _verify
    app.dependency_overrides.pop(_verify, None)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def error_client(app):
    """TestClient that returns 500 responses instead of raising exceptions."""
    from webhooks import _verify

    async def _bypass():
        return None

    app.dependency_overrides[_verify] = _bypass
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(_verify, None)
