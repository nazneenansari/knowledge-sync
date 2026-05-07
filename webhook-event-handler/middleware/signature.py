import hashlib
import hmac
import json
import time
from collections.abc import Callable

from botocore.exceptions import ClientError
from fastapi import Header, HTTPException, Request, status


def verify_signature(
    secret: str, timestamp: str, raw_body: bytes, signature: str, tolerance_seconds: int = 3000
) -> bool:
    """
    HMAC-SHA256 verification

    signing_payload = f"{timestamp}.{raw_body}"
    expected        = "sha256=" + HMAC-SHA256(secret, signing_payload).hex()
    """
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        raise ValueError("X-Timestamp must be a Unix epoch integer.")

    age = abs(time.time() - ts)
    if age > tolerance_seconds:
        raise ValueError(f"X-Timestamp is stale ({int(age)}s old).")

    signing_payload = timestamp.encode("utf-8") + b"\n" + raw_body
    expected = hmac.new(
        secret.encode("utf-8"),
        signing_payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)



def make_signature_verifier() -> Callable:
    """FastAPI dependency factory — fetches the HMAC secret from AWS Secrets Manager
    using the appId from the request body, then verifies the signature."""
    from middleware.secret_manager import get_secret

    async def verify(
        request: Request,
        x_signature: str = Header(...),
        x_timestamp: str = Header(...),
    ) -> None:
        raw_body = await request.body()

        try:
            app_id = json.loads(raw_body).get("appId")
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body.")

        if not app_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing appId in request body.")

        try:
            secret = get_secret("event-producer-secret", app_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown appId.")
        except ClientError:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve signing secret.")
        print(f"Retrieved secret for app {app_id}: {secret}")
        try:
            valid = verify_signature(secret, x_timestamp, raw_body, x_signature)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

        if not valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid HMAC signature.")

    return verify
