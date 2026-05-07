"""Tests for service/auth.py — OAuth2 token caching and refresh."""
import json
import time
from unittest.mock import patch, MagicMock
import pytest

import service.auth as auth_module


def _make_urlopen_mock(access_token: str = "test-token", expires_in: int = 3600):
    """Returns a context-manager mock that simulates a successful urlopen response."""
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"access_token": access_token, "expires_in": expires_in}
    ).encode()
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)
    return response


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Reset the module-level token cache before and after each test."""
    auth_module._token_cache = {"access_token": None, "expires_at": 0}
    yield
    auth_module._token_cache = {"access_token": None, "expires_at": 0}


class TestGetOAuthTokenFetch:
    def test_returns_token_from_response(self):
        with patch("service.auth.urllib.request.urlopen", return_value=_make_urlopen_mock("fresh-token")):
            token = auth_module.get_oauth_token()
        assert token == "fresh-token"

    def test_stores_token_in_cache_after_fetch(self):
        with patch("service.auth.urllib.request.urlopen", return_value=_make_urlopen_mock("cached")):
            auth_module.get_oauth_token()
        assert auth_module._token_cache["access_token"] == "cached"
        assert auth_module._token_cache["expires_at"] > time.time()

    def test_expires_at_subtracts_60s_buffer(self):
        fixed_now = 1_000_000.0
        with patch("service.auth.urllib.request.urlopen", return_value=_make_urlopen_mock("tok", 3600)), \
             patch("service.auth.time.time", return_value=fixed_now):
            auth_module.get_oauth_token()
        assert auth_module._token_cache["expires_at"] == fixed_now + 3600 - 60

    def test_defaults_expires_in_to_3600_when_missing(self):
        response = MagicMock()
        response.read.return_value = json.dumps({"access_token": "no-expiry"}).encode()
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        fixed_now = 1_000_000.0
        with patch("service.auth.urllib.request.urlopen", return_value=response), \
             patch("service.auth.time.time", return_value=fixed_now):
            auth_module.get_oauth_token()
        assert auth_module._token_cache["expires_at"] == fixed_now + 3600 - 60


class TestGetOAuthTokenCache:
    def test_returns_cached_token_when_not_expired(self):
        auth_module._token_cache = {
            "access_token": "still-valid",
            "expires_at": time.time() + 1000,
        }
        with patch("service.auth.urllib.request.urlopen") as mock_urlopen:
            token = auth_module.get_oauth_token()
            mock_urlopen.assert_not_called()
        assert token == "still-valid"

    def test_does_not_call_urlopen_when_cache_valid(self):
        auth_module._token_cache = {
            "access_token": "valid-token",
            "expires_at": time.time() + 500,
        }
        with patch("service.auth.urllib.request.urlopen") as mock_urlopen:
            auth_module.get_oauth_token()
        mock_urlopen.assert_not_called()

    def test_fetches_new_token_when_cache_expired(self):
        auth_module._token_cache = {
            "access_token": "expired-token",
            "expires_at": time.time() - 1,
        }
        with patch("service.auth.urllib.request.urlopen", return_value=_make_urlopen_mock("refreshed")):
            token = auth_module.get_oauth_token()
        assert token == "refreshed"

    def test_fetches_token_when_access_token_is_none(self):
        auth_module._token_cache = {"access_token": None, "expires_at": time.time() + 1000}
        with patch("service.auth.urllib.request.urlopen", return_value=_make_urlopen_mock("brand-new")):
            token = auth_module.get_oauth_token()
        assert token == "brand-new"
