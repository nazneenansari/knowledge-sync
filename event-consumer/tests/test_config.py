"""Tests for config.py — env var loading, type coercion, and defaults."""
import os
import importlib
from unittest.mock import patch
import pytest


_REQUIRED = {
    "IDEMPOTENCY_TABLE": "test-table",
    "OAUTH_TOKEN_URL": "https://auth.example.com/token",
    "CLIENT_ID": "test-client-id",
    "CLIENT_SECRET": "test-client-secret",
    "API_URL": "https://api.example.com/events",
    "QUEUE_URL": "https://sqs.region.amazonaws.com/123/queue",
    "DLQ_URL": "https://sqs.region.amazonaws.com/123/dlq",
}


def _reload_config(overrides: dict = None, remove: set = None):
    """Reload config with exactly _REQUIRED + overrides, minus any keys in remove."""
    env = {**_REQUIRED, **(overrides or {})}
    for k in remove or set():
        env.pop(k, None)
    with patch.dict(os.environ, env, clear=True):
        import config
        return importlib.reload(config)


class TestRequiredVars:
    def test_idempotency_table_loaded(self):
        cfg = _reload_config()
        assert cfg.IDEMPOTENCY_TABLE == "test-table"

    def test_token_url_loaded(self):
        cfg = _reload_config()
        assert cfg.TOKEN_URL == "https://auth.example.com/token"

    def test_client_id_loaded(self):
        cfg = _reload_config()
        assert cfg.CLIENT_ID == "test-client-id"

    def test_client_secret_loaded(self):
        cfg = _reload_config()
        assert cfg.CLIENT_SECRET == "test-client-secret"

    def test_api_url_loaded(self):
        cfg = _reload_config()
        assert cfg.API_URL == "https://api.example.com/events"

    def test_queue_url_loaded(self):
        cfg = _reload_config()
        assert cfg.QUEUE_URL == "https://sqs.region.amazonaws.com/123/queue"

    def test_dlq_url_loaded(self):
        cfg = _reload_config()
        assert cfg.DLQ_URL == "https://sqs.region.amazonaws.com/123/dlq"

    def test_missing_idempotency_table_raises_key_error(self):
        with pytest.raises(KeyError):
            _reload_config(remove={"IDEMPOTENCY_TABLE"})

    def test_missing_oauth_token_url_raises_key_error(self):
        with pytest.raises(KeyError):
            _reload_config(remove={"OAUTH_TOKEN_URL"})

    def test_missing_api_url_raises_key_error(self):
        with pytest.raises(KeyError):
            _reload_config(remove={"API_URL"})

    def test_missing_queue_url_raises_key_error(self):
        with pytest.raises(KeyError):
            _reload_config(remove={"QUEUE_URL"})

    def test_missing_dlq_url_raises_key_error(self):
        with pytest.raises(KeyError):
            _reload_config(remove={"DLQ_URL"})


class TestOptionalDefaults:
    def test_log_level_defaults_to_info(self):
        cfg = _reload_config()
        assert cfg.LOG_LEVEL == "INFO"

    def test_log_level_uppercased(self):
        cfg = _reload_config({"LOG_LEVEL": "debug"})
        assert cfg.LOG_LEVEL == "DEBUG"

    def test_use_dummy_api_defaults_to_false(self):
        cfg = _reload_config()
        assert cfg.USE_DUMMY_API is False

    def test_use_dummy_api_true_string(self):
        cfg = _reload_config({"USE_DUMMY_API": "true"})
        assert cfg.USE_DUMMY_API is True

    def test_use_dummy_api_true_uppercase(self):
        cfg = _reload_config({"USE_DUMMY_API": "TRUE"})
        assert cfg.USE_DUMMY_API is True

    def test_use_dummy_api_false_string(self):
        cfg = _reload_config({"USE_DUMMY_API": "false"})
        assert cfg.USE_DUMMY_API is False

    def test_dummy_api_mode_defaults_to_success(self):
        cfg = _reload_config()
        assert cfg.DUMMY_API_MODE == "success"

    def test_dummy_api_mode_custom(self):
        cfg = _reload_config({"DUMMY_API_MODE": "client_error"})
        assert cfg.DUMMY_API_MODE == "client_error"

    def test_max_backoff_default_is_float(self):
        cfg = _reload_config()
        assert cfg.MAX_BACKOFF == 30.0
        assert isinstance(cfg.MAX_BACKOFF, float)

    def test_max_backoff_custom(self):
        cfg = _reload_config({"MAX_BACKOFF": "60"})
        assert cfg.MAX_BACKOFF == 60.0

    def test_max_retries_default_is_int(self):
        cfg = _reload_config()
        assert cfg.MAX_RETRIES == 5
        assert isinstance(cfg.MAX_RETRIES, int)

    def test_max_retries_custom(self):
        cfg = _reload_config({"MAX_RETRIES": "3"})
        assert cfg.MAX_RETRIES == 3

    def test_base_delay_default_is_float(self):
        cfg = _reload_config()
        assert cfg.BASE_DELAY == 1.0
        assert isinstance(cfg.BASE_DELAY, float)

    def test_http_timeout_default_is_float(self):
        cfg = _reload_config()
        assert cfg.HTTP_TIMEOUT == 10.0
        assert isinstance(cfg.HTTP_TIMEOUT, float)

    def test_stale_lock_timeout_default_is_int(self):
        cfg = _reload_config()
        assert cfg.STALE_LOCK_TIMEOUT_SECONDS == 900
        assert isinstance(cfg.STALE_LOCK_TIMEOUT_SECONDS, int)

    def test_idempotency_ttl_default_is_seven_days(self):
        cfg = _reload_config()
        assert cfg.IDEMPOTENCY_TTL_SECONDS == 604800
        assert isinstance(cfg.IDEMPOTENCY_TTL_SECONDS, int)
