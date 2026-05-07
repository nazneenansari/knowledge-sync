"""Environment variables and tuneable constants for the event-consumer Lambda."""
import os

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

IDEMPOTENCY_TABLE = os.environ["IDEMPOTENCY_TABLE"]
TOKEN_URL = os.environ["OAUTH_TOKEN_URL"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
API_URL = os.environ["API_URL"]
QUEUE_URL = os.environ["QUEUE_URL"]
DLQ_URL = os.environ["DLQ_URL"]

USE_DUMMY_API = os.getenv("USE_DUMMY_API", "false").lower() == "true"
DUMMY_API_MODE = os.getenv("DUMMY_API_MODE", "success")
MAX_BACKOFF = float(os.getenv("MAX_BACKOFF", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
BASE_DELAY = float(os.getenv("BASE_DELAY", "1"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
STALE_LOCK_TIMEOUT_SECONDS = int(os.getenv("STALE_LOCK_TIMEOUT_SECONDS", "900"))
IDEMPOTENCY_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "604800"))  # 7 days
