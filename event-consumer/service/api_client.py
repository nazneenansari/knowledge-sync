"""External API client with OAuth bearer auth, exponential backoff, and per-attempt jitter."""
import json
import time
import random
import logging
import urllib.request
import urllib.error

from config import API_URL, USE_DUMMY_API, DUMMY_API_MODE, MAX_RETRIES, MAX_BACKOFF, BASE_DELAY, HTTP_TIMEOUT
from service.auth import get_oauth_token

logger = logging.getLogger(__name__)


class ExternalAPIException(Exception):
    def __init__(self, status_code: int, error_message: str):
        self.status_code = status_code
        self.error_message = error_message
        super().__init__(f"External API exception {status_code}: {error_message}")


def call_external_api(payload: dict) -> tuple[int, str]:
    """Single POST to API_URL; returns (status_code, body). HTTPError responses are returned as (code, body), not raised."""
    if USE_DUMMY_API:
        if DUMMY_API_MODE == "success":
            return 200, '{"message": "ok"}'
        elif DUMMY_API_MODE == "client_error":
            return 400, '{"error": "bad request"}'
        elif DUMMY_API_MODE == "server_error":
            return 500, '{"error": "internal error"}'
        elif DUMMY_API_MODE == "timeout":
            raise Exception("Simulated timeout")
        else:
            raise urllib.error.URLError(f"Simulated network error (DUMMY_API_MODE='{DUMMY_API_MODE}')")

    token = get_oauth_token()

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def call_external_api_with_retry(payload: dict) -> tuple[int, str]:
    """Retries 5xx and network errors up to MAX_RETRIES with exponential backoff and jitter; 4xx errors are returned immediately without retry."""
    for attempt in range(MAX_RETRIES):
        try:
            status, resp = call_external_api(payload)

            if 200 <= status < 300:
                return status, resp

            if 400 <= status < 500:
                logger.error(f"Client error (no retry): {status}")
                return status, resp

            raise ExternalAPIException(status, "Server Error")

        except (ExternalAPIException, urllib.error.URLError) as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")

            if attempt == MAX_RETRIES - 1:
                if isinstance(e, ExternalAPIException):
                    raise ExternalAPIException(e.status_code, f"{str(e)} | Max retries exceeded")
                raise

            max_backoff = min(BASE_DELAY * (2 ** attempt), MAX_BACKOFF)
            jitter_delay = random.uniform(0, max_backoff)

            logger.info(f"Retrying with jitter in {jitter_delay:.2f}s (max backoff {max_backoff:.2f}s)")
            time.sleep(jitter_delay)
