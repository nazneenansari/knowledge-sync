"""OAuth2 client-credentials token fetch with in-memory cache across Lambda warm starts."""
import json
import time
import logging
import urllib.request
import urllib.parse

from config import TOKEN_URL, CLIENT_ID, CLIENT_SECRET, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

_token_cache = {"access_token": None, "expires_at": 0}


def get_oauth_token() -> str:
    """Returns a valid bearer token, fetching a fresh one from TOKEN_URL only when the cached token has expired."""
    global _token_cache

    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data)

    logger.info("Refreshing OAuth token")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as res:
        body = json.loads(res.read().decode())

    access_token = body["access_token"]
    expires_in = body.get("expires_in", 3600)

    _token_cache = {
        "access_token": access_token,
        "expires_at": time.time() + expires_in - 60
    }

    return access_token
