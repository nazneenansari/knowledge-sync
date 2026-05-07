"""Tests for service/api_client.py — dummy mode, real HTTP calls, retry and backoff."""
import json
import urllib.error
from unittest.mock import patch, MagicMock
import pytest

import service.api_client as api_module
from service.api_client import ExternalAPIException, call_external_api, call_external_api_with_retry


def _urlopen_mock(status: int = 200, body: str = "{}"):
    """Returns a context-manager mock simulating a successful urllib response."""
    response = MagicMock()
    response.status = status
    response.read.return_value = body.encode()
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)
    return response


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(url="http://test", code=code, msg="Error", hdrs={}, fp=None)
    err.read = lambda: body.encode()
    return err


class TestExternalAPIException:
    def test_stores_status_code(self):
        exc = ExternalAPIException(503, "unavailable")
        assert exc.status_code == 503

    def test_stores_error_message(self):
        exc = ExternalAPIException(500, "internal error")
        assert exc.error_message == "internal error"

    def test_str_contains_status_and_message(self):
        exc = ExternalAPIException(502, "bad gateway")
        assert "502" in str(exc)
        assert "bad gateway" in str(exc)

    def test_is_exception_subclass(self):
        assert issubclass(ExternalAPIException, Exception)


class TestCallExternalApiDummyMode:
    @pytest.fixture(autouse=True)
    def enable_dummy(self):
        with patch.object(api_module, "USE_DUMMY_API", True):
            yield

    def test_success_mode_returns_200(self):
        with patch.object(api_module, "DUMMY_API_MODE", "success"):
            status, body = call_external_api({})
        assert status == 200
        assert "ok" in body

    def test_client_error_mode_returns_400(self):
        with patch.object(api_module, "DUMMY_API_MODE", "client_error"):
            status, body = call_external_api({})
        assert status == 400

    def test_server_error_mode_returns_500(self):
        with patch.object(api_module, "DUMMY_API_MODE", "server_error"):
            status, body = call_external_api({})
        assert status == 500

    def test_timeout_mode_raises_exception(self):
        with patch.object(api_module, "DUMMY_API_MODE", "timeout"), \
             pytest.raises(Exception, match="Simulated timeout"):
            call_external_api({})

    def test_unknown_mode_raises_url_error(self):
        with patch.object(api_module, "DUMMY_API_MODE", "unknown_mode"), \
             pytest.raises(urllib.error.URLError):
            call_external_api({})

    def test_dummy_mode_does_not_call_oauth(self):
        with patch.object(api_module, "DUMMY_API_MODE", "success"), \
             patch("service.api_client.get_oauth_token") as mock_token:
            call_external_api({})
        mock_token.assert_not_called()


class TestCallExternalApiRealMode:
    @pytest.fixture(autouse=True)
    def disable_dummy(self):
        with patch.object(api_module, "USE_DUMMY_API", False):
            yield

    def test_returns_status_and_body_on_success(self):
        with patch("service.api_client.get_oauth_token", return_value="token"), \
             patch("service.api_client.urllib.request.urlopen", return_value=_urlopen_mock(200, '{"ok":true}')):
            status, body = call_external_api({"key": "value"})
        assert status == 200
        assert body == '{"ok":true}'

    def test_http_error_returns_code_and_body_without_raising(self):
        with patch("service.api_client.get_oauth_token", return_value="token"), \
             patch("service.api_client.urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
            status, body = call_external_api({})
        assert status == 404
        assert body == "Not Found"

    def test_sends_bearer_authorization_header(self):
        with patch("service.api_client.get_oauth_token", return_value="my-bearer"), \
             patch("service.api_client.urllib.request.Request") as mock_req, \
             patch("service.api_client.urllib.request.urlopen", return_value=_urlopen_mock()):
            mock_req.return_value = MagicMock()
            call_external_api({"x": 1})
        _, kwargs = mock_req.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer my-bearer"

    def test_sends_content_type_json_header(self):
        with patch("service.api_client.get_oauth_token", return_value="token"), \
             patch("service.api_client.urllib.request.Request") as mock_req, \
             patch("service.api_client.urllib.request.urlopen", return_value=_urlopen_mock()):
            mock_req.return_value = MagicMock()
            call_external_api({})
        _, kwargs = mock_req.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"

    def test_posts_json_encoded_payload(self):
        payload = {"tenantId": "t1", "event": "E1"}
        with patch("service.api_client.get_oauth_token", return_value="token"), \
             patch("service.api_client.urllib.request.Request") as mock_req, \
             patch("service.api_client.urllib.request.urlopen", return_value=_urlopen_mock()):
            mock_req.return_value = MagicMock()
            call_external_api(payload)
        _, kwargs = mock_req.call_args
        assert kwargs["data"] == json.dumps(payload).encode()

    def test_uses_post_method(self):
        with patch("service.api_client.get_oauth_token", return_value="token"), \
             patch("service.api_client.urllib.request.Request") as mock_req, \
             patch("service.api_client.urllib.request.urlopen", return_value=_urlopen_mock()):
            mock_req.return_value = MagicMock()
            call_external_api({})
        _, kwargs = mock_req.call_args
        assert kwargs["method"] == "POST"


class TestCallExternalApiWithRetry:
    @pytest.fixture(autouse=True)
    def disable_dummy(self):
        with patch.object(api_module, "USE_DUMMY_API", False):
            yield

    def test_2xx_returns_immediately_without_retry(self):
        with patch("service.api_client.call_external_api", return_value=(200, "ok")) as mock_api:
            status, body = call_external_api_with_retry({})
        assert status == 200
        mock_api.assert_called_once()

    def test_201_also_returns_immediately(self):
        with patch("service.api_client.call_external_api", return_value=(201, "created")) as mock_api:
            status, _ = call_external_api_with_retry({})
        assert status == 201
        mock_api.assert_called_once()

    def test_4xx_returns_immediately_without_retry(self):
        with patch("service.api_client.call_external_api", return_value=(400, "bad request")) as mock_api:
            status, body = call_external_api_with_retry({})
        assert status == 400
        mock_api.assert_called_once()

    def test_422_returns_immediately_without_retry(self):
        with patch("service.api_client.call_external_api", return_value=(422, "unprocessable")) as mock_api:
            status, _ = call_external_api_with_retry({})
        assert status == 422
        mock_api.assert_called_once()

    def test_5xx_retries_up_to_max_retries(self):
        with patch.object(api_module, "MAX_RETRIES", 3), \
             patch("service.api_client.call_external_api", return_value=(503, "error")) as mock_api, \
             patch("service.api_client.time.sleep"), \
             patch("service.api_client.random.uniform", return_value=0.1), \
             pytest.raises(ExternalAPIException):
            call_external_api_with_retry({})
        assert mock_api.call_count == 3

    def test_5xx_final_exception_has_status_code(self):
        with patch.object(api_module, "MAX_RETRIES", 2), \
             patch("service.api_client.call_external_api", return_value=(502, "error")), \
             patch("service.api_client.time.sleep"), \
             patch("service.api_client.random.uniform", return_value=0.1), \
             pytest.raises(ExternalAPIException) as exc_info:
            call_external_api_with_retry({})
        assert exc_info.value.status_code == 502

    def test_5xx_final_exception_mentions_max_retries(self):
        with patch.object(api_module, "MAX_RETRIES", 2), \
             patch("service.api_client.call_external_api", return_value=(500, "err")), \
             patch("service.api_client.time.sleep"), \
             patch("service.api_client.random.uniform", return_value=0.1), \
             pytest.raises(ExternalAPIException) as exc_info:
            call_external_api_with_retry({})
        assert "Max retries exceeded" in str(exc_info.value)

    def test_url_error_retries_up_to_max_retries(self):
        with patch.object(api_module, "MAX_RETRIES", 3), \
             patch("service.api_client.call_external_api", side_effect=urllib.error.URLError("refused")) as mock_api, \
             patch("service.api_client.time.sleep"), \
             patch("service.api_client.random.uniform", return_value=0.1), \
             pytest.raises(urllib.error.URLError):
            call_external_api_with_retry({})
        assert mock_api.call_count == 3

    def test_succeeds_on_second_attempt_after_5xx(self):
        side_effects = [(503, "error"), (200, "ok")]
        with patch.object(api_module, "MAX_RETRIES", 5), \
             patch("service.api_client.call_external_api", side_effect=side_effects) as mock_api, \
             patch("service.api_client.time.sleep"), \
             patch("service.api_client.random.uniform", return_value=0.1):
            status, _ = call_external_api_with_retry({})
        assert status == 200
        assert mock_api.call_count == 2

    def test_sleeps_between_retries(self):
        side_effects = [(503, "error"), (200, "ok")]
        with patch.object(api_module, "MAX_RETRIES", 5), \
             patch.object(api_module, "BASE_DELAY", 1.0), \
             patch.object(api_module, "MAX_BACKOFF", 30.0), \
             patch("service.api_client.call_external_api", side_effect=side_effects), \
             patch("service.api_client.random.uniform", return_value=0.75) as mock_rand, \
             patch("service.api_client.time.sleep") as mock_sleep:
            call_external_api_with_retry({})
        mock_sleep.assert_called_once_with(0.75)
        mock_rand.assert_called_once_with(0, 1.0)

    def test_no_sleep_when_max_retries_is_one(self):
        with patch.object(api_module, "MAX_RETRIES", 1), \
             patch("service.api_client.call_external_api", return_value=(500, "err")), \
             patch("service.api_client.time.sleep") as mock_sleep, \
             pytest.raises(ExternalAPIException):
            call_external_api_with_retry({})
        mock_sleep.assert_not_called()

    def test_backoff_is_capped_by_max_backoff(self):
        side_effects = [(503, "err")] * 4 + [(200, "ok")]
        with patch.object(api_module, "MAX_RETRIES", 5), \
             patch.object(api_module, "BASE_DELAY", 100.0), \
             patch.object(api_module, "MAX_BACKOFF", 30.0), \
             patch("service.api_client.call_external_api", side_effect=side_effects), \
             patch("service.api_client.random.uniform", return_value=0.5) as mock_rand, \
             patch("service.api_client.time.sleep"):
            call_external_api_with_retry({})
        for rand_call in mock_rand.call_args_list:
            lower, upper = rand_call[0]
            assert upper <= 30.0
            assert lower == 0
