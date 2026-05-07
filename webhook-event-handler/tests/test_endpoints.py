"""
Integration tests for all three webhook endpoints.

Signature verification is bypassed via the `client` fixture (see conftest.py).
Storage and queuing are mocked per-test so no AWS calls are made.
"""

from unittest.mock import patch

import pytest

from middleware.storage import DuplicateEventError


# ---------------------------------------------------------------------------
# Shared valid payloads
# ---------------------------------------------------------------------------

ARTICLE_PUBLISHED = {
    "event": "article.published",
    "tenantId": "acme-corp",
    "appId": "egain",
    "timestamp": "2024-06-01T12:00:00Z",
    "data": {
        "articleId": "ARTICLE-1001",
        "title": "How to reset your password",
        "urlName": "how-to-reset-password",
        "content": "<p>Reset steps here</p>",
    },
}

CASE_CLOSED = {
    "event": "case.closed",
    "tenantId": "acme-corp",
    "appId": "salesforce",
    "timestamp": "2024-06-01T15:00:00Z",
    "data": {
        "caseId": "5003000000ABC123AAA",
        "subject": "Cannot log into portal",
        "description": "User says password reset email is not arriving.",
    },
}

ARTICLE_VIEWED = {
    "event": "article.viewed",
    "tenantId": "acme-corp",
    "appId": "egain",
    "timestamp": "2024-06-01T17:00:00Z",
    "data": {
        "articleId": "ARTICLE-1001",
        "sessionId": "sess_abc123def456",
    },
}

_S3_KEY = "webhooks/raw/acme-corp/egain/article_published/evt-001.json"


# ---------------------------------------------------------------------------
# POST /dev/webhooks/article-published
# ---------------------------------------------------------------------------

class TestArticlePublished:

    # ------------------------------------------------------------------
    # 202 happy paths
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_valid_request(self, _store, _enqueue, client):
        resp = client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_response_body_shape(self, _store, _enqueue, client):
        resp = client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        body = resp.json()
        assert body["status"] == "accepted"
        assert "eventId" in body
        assert body["message"] == "Article publish event queued for processing."

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_event_id_in_response_is_a_uuid(self, _store, _enqueue, client):
        import uuid
        resp = client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        uuid.UUID(resp.json()["eventId"])  # raises if not valid UUID

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_store_and_enqueue_both_called(self, mock_store, mock_enqueue, client):
        client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        mock_store.assert_called_once()
        mock_enqueue.assert_called_once()

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_with_optional_fields(self, _store, _enqueue, client):
        payload = {
            **ARTICLE_PUBLISHED,
            "webhookId": "wh_abc123",
            "data": {
                **ARTICLE_PUBLISHED["data"],
                "language": "fr-FR",
                "version": "3.1",
                "categories": ["Security", "Account Management"],
            },
        }
        resp = client.post("/dev/webhooks/article-published", json=payload)
        assert resp.status_code == 202

    # ------------------------------------------------------------------
    # 202 duplicate / idempotency
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=DuplicateEventError("already exists"))
    def test_202_on_duplicate_event(self, _store, mock_enqueue, client):
        resp = client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=DuplicateEventError("already exists"))
    def test_enqueue_not_called_on_duplicate(self, _store, mock_enqueue, client):
        client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        mock_enqueue.assert_not_called()

    # ------------------------------------------------------------------
    # 422 — missing required fields (Pydantic validation)
    # ------------------------------------------------------------------

    def test_422_on_missing_article_id(self, client):
        data = {**ARTICLE_PUBLISHED["data"]}
        del data["articleId"]
        resp = client.post("/dev/webhooks/article-published", json={**ARTICLE_PUBLISHED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_title(self, client):
        data = {**ARTICLE_PUBLISHED["data"]}
        del data["title"]
        resp = client.post("/dev/webhooks/article-published", json={**ARTICLE_PUBLISHED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_content(self, client):
        data = {**ARTICLE_PUBLISHED["data"]}
        del data["content"]
        resp = client.post("/dev/webhooks/article-published", json={**ARTICLE_PUBLISHED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_url_name(self, client):
        data = {**ARTICLE_PUBLISHED["data"]}
        del data["urlName"]
        resp = client.post("/dev/webhooks/article-published", json={**ARTICLE_PUBLISHED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_tenant_id(self, client):
        payload = {k: v for k, v in ARTICLE_PUBLISHED.items() if k != "tenantId"}
        resp = client.post("/dev/webhooks/article-published", json=payload)
        assert resp.status_code == 422

    def test_422_on_missing_app_id(self, client):
        payload = {k: v for k, v in ARTICLE_PUBLISHED.items() if k != "appId"}
        resp = client.post("/dev/webhooks/article-published", json=payload)
        assert resp.status_code == 422

    def test_422_on_missing_timestamp(self, client):
        payload = {k: v for k, v in ARTICLE_PUBLISHED.items() if k != "timestamp"}
        resp = client.post("/dev/webhooks/article-published", json=payload)
        assert resp.status_code == 422

    def test_422_on_missing_data_block(self, client):
        payload = {k: v for k, v in ARTICLE_PUBLISHED.items() if k != "data"}
        resp = client.post("/dev/webhooks/article-published", json=payload)
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — wrong event type
    # ------------------------------------------------------------------

    def test_422_on_wrong_event_type_case_closed(self, client):
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "event": "case.closed"},
        )
        assert resp.status_code == 422

    def test_422_on_wrong_event_type_article_viewed(self, client):
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "event": "article.viewed"},
        )
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — blank required fields (_require_nonempty guard)
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_tenant_id(self, _store, _enqueue, client):
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "tenantId": "   "},
        )
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_app_id(self, _store, _enqueue, client):
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "appId": ""},
        )
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_article_id(self, _store, _enqueue, client):
        data = {**ARTICLE_PUBLISHED["data"], "articleId": "   "}
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "data": data},
        )
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_title(self, _store, _enqueue, client):
        data = {**ARTICLE_PUBLISHED["data"], "title": "\t"}
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "data": data},
        )
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_content(self, _store, _enqueue, client):
        data = {**ARTICLE_PUBLISHED["data"], "content": "  \n  "}
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "data": data},
        )
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — field format constraints
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_url_name_with_uppercase_letters(self, _store, _enqueue, client):
        data = {**ARTICLE_PUBLISHED["data"], "urlName": "Has-Uppercase"}
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "data": data},
        )
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_url_name_with_spaces(self, _store, _enqueue, client):
        data = {**ARTICLE_PUBLISHED["data"], "urlName": "has spaces"}
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "data": data},
        )
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_title_exceeds_255_characters(self, _store, _enqueue, client):
        data = {**ARTICLE_PUBLISHED["data"], "title": "x" * 256}
        resp = client.post(
            "/dev/webhooks/article-published",
            json={**ARTICLE_PUBLISHED, "data": data},
        )
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 5xx — AWS failures
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=Exception("S3 is down"))
    def test_500_on_s3_failure(self, _store, _enqueue, error_client):
        resp = error_client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        assert resp.status_code == 500

    @patch("webhooks.enqueue_event", side_effect=Exception("SQS is down"))
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_500_on_sqs_failure(self, _store, _enqueue, error_client):
        resp = error_client.post("/dev/webhooks/article-published", json=ARTICLE_PUBLISHED)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /dev/webhooks/case-closed
# ---------------------------------------------------------------------------

class TestCaseClosed:

    # ------------------------------------------------------------------
    # 202 happy paths
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_valid_request(self, _store, _enqueue, client):
        resp = client.post("/dev/webhooks/case-closed", json=CASE_CLOSED)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_response_body_shape(self, _store, _enqueue, client):
        resp = client.post("/dev/webhooks/case-closed", json=CASE_CLOSED)
        body = resp.json()
        assert body["status"] == "accepted"
        assert "eventId" in body
        assert body["message"] == "Case closed event queued for processing."

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_store_and_enqueue_both_called(self, mock_store, mock_enqueue, client):
        client.post("/dev/webhooks/case-closed", json=CASE_CLOSED)
        mock_store.assert_called_once()
        mock_enqueue.assert_called_once()

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_with_all_optional_fields(self, _store, _enqueue, client):
        payload = {
            **CASE_CLOSED,
            "data": {
                **CASE_CLOSED["data"],
                "caseNumber": "00001234",
                "resolution": "Sent password reset article.",
                "category": "Account Management",
                "priority": "High",
                "articleIds": ["ARTICLE-1001", "ARTICLE-1005"],
                "viewedArticleIds": ["ARTICLE-1001"],
            },
        }
        resp = client.post("/dev/webhooks/case-closed", json=payload)
        assert resp.status_code == 202

    # ------------------------------------------------------------------
    # 202 duplicate / idempotency
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=DuplicateEventError("already exists"))
    def test_202_on_duplicate_event(self, _store, mock_enqueue, client):
        resp = client.post("/dev/webhooks/case-closed", json=CASE_CLOSED)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=DuplicateEventError("already exists"))
    def test_enqueue_not_called_on_duplicate(self, _store, mock_enqueue, client):
        client.post("/dev/webhooks/case-closed", json=CASE_CLOSED)
        mock_enqueue.assert_not_called()

    # ------------------------------------------------------------------
    # 422 — missing required fields
    # ------------------------------------------------------------------

    def test_422_on_missing_case_id(self, client):
        data = {**CASE_CLOSED["data"]}
        del data["caseId"]
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_subject(self, client):
        data = {**CASE_CLOSED["data"]}
        del data["subject"]
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_description(self, client):
        data = {**CASE_CLOSED["data"]}
        del data["description"]
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_tenant_id(self, client):
        payload = {k: v for k, v in CASE_CLOSED.items() if k != "tenantId"}
        resp = client.post("/dev/webhooks/case-closed", json=payload)
        assert resp.status_code == 422

    def test_422_on_missing_app_id(self, client):
        payload = {k: v for k, v in CASE_CLOSED.items() if k != "appId"}
        resp = client.post("/dev/webhooks/case-closed", json=payload)
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — wrong event type
    # ------------------------------------------------------------------

    def test_422_on_wrong_event_type_article_published(self, client):
        resp = client.post(
            "/dev/webhooks/case-closed",
            json={**CASE_CLOSED, "event": "article.published"},
        )
        assert resp.status_code == 422

    def test_422_on_wrong_event_type_article_viewed(self, client):
        resp = client.post(
            "/dev/webhooks/case-closed",
            json={**CASE_CLOSED, "event": "article.viewed"},
        )
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — blank required fields (_require_nonempty guard)
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_case_id(self, _store, _enqueue, client):
        data = {**CASE_CLOSED["data"], "caseId": "   "}
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_subject(self, _store, _enqueue, client):
        data = {**CASE_CLOSED["data"], "subject": ""}
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_description(self, _store, _enqueue, client):
        data = {**CASE_CLOSED["data"], "description": "  \t  "}
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — invalid enum values
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_invalid_priority_value(self, _store, _enqueue, client):
        data = {**CASE_CLOSED["data"], "priority": "Urgent"}
        resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_each_valid_priority_value(self, _store, _enqueue, client):
        for priority in ("Low", "Medium", "High", "Critical"):
            data = {**CASE_CLOSED["data"], "priority": priority}
            resp = client.post("/dev/webhooks/case-closed", json={**CASE_CLOSED, "data": data})
            assert resp.status_code == 202, f"Failed for priority={priority}"

    # ------------------------------------------------------------------
    # 5xx — AWS failures
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=Exception("S3 is down"))
    def test_500_on_s3_failure(self, _store, _enqueue, error_client):
        resp = error_client.post("/dev/webhooks/case-closed", json=CASE_CLOSED)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /dev/webhooks/article-viewed
# ---------------------------------------------------------------------------

class TestArticleViewed:

    # ------------------------------------------------------------------
    # 202 happy paths
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_valid_request(self, _store, _enqueue, client):
        resp = client.post("/dev/webhooks/article-viewed", json=ARTICLE_VIEWED)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_response_body_shape(self, _store, _enqueue, client):
        resp = client.post("/dev/webhooks/article-viewed", json=ARTICLE_VIEWED)
        body = resp.json()
        assert body["status"] == "accepted"
        assert "eventId" in body
        assert body["message"] == "Article view analytics event queued for streaming."

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_store_and_enqueue_both_called(self, mock_store, mock_enqueue, client):
        client.post("/dev/webhooks/article-viewed", json=ARTICLE_VIEWED)
        mock_store.assert_called_once()
        mock_enqueue.assert_called_once()

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_with_all_optional_analytics_fields(self, _store, _enqueue, client):
        payload = {
            **ARTICLE_VIEWED,
            "webhookId": "wh_analytics_001",
            "data": {
                **ARTICLE_VIEWED["data"],
                "articleVersion": "2.0",
                "userId": "usr_xyz789",
                "channel": "portal",
                "durationSeconds": 45,
                "helpful": True,
                "searchQuery": "reset password",
                "caseId": "5003000000ABC123AAA",
                "userAgent": "Mozilla/5.0",
                "locale": "en-GB",
                "deviceType": "desktop",
                "timestamp": "2024-06-01T17:00:00Z",
            },
        }
        resp = client.post("/dev/webhooks/article-viewed", json=payload)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_helpful_false(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "helpful": False}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_helpful_null(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "helpful": None}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 202

    # ------------------------------------------------------------------
    # 202 duplicate / idempotency
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=DuplicateEventError("already exists"))
    def test_202_on_duplicate_event(self, _store, mock_enqueue, client):
        resp = client.post("/dev/webhooks/article-viewed", json=ARTICLE_VIEWED)
        assert resp.status_code == 202

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=DuplicateEventError("already exists"))
    def test_enqueue_not_called_on_duplicate(self, _store, mock_enqueue, client):
        client.post("/dev/webhooks/article-viewed", json=ARTICLE_VIEWED)
        mock_enqueue.assert_not_called()

    # ------------------------------------------------------------------
    # 422 — missing required fields
    # ------------------------------------------------------------------

    def test_422_on_missing_article_id(self, client):
        data = {**ARTICLE_VIEWED["data"]}
        del data["articleId"]
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_session_id(self, client):
        data = {**ARTICLE_VIEWED["data"]}
        del data["sessionId"]
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    def test_422_on_missing_tenant_id(self, client):
        payload = {k: v for k, v in ARTICLE_VIEWED.items() if k != "tenantId"}
        resp = client.post("/dev/webhooks/article-viewed", json=payload)
        assert resp.status_code == 422

    def test_422_on_missing_app_id(self, client):
        payload = {k: v for k, v in ARTICLE_VIEWED.items() if k != "appId"}
        resp = client.post("/dev/webhooks/article-viewed", json=payload)
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — wrong event type
    # ------------------------------------------------------------------

    def test_422_on_wrong_event_type_article_published(self, client):
        resp = client.post(
            "/dev/webhooks/article-viewed",
            json={**ARTICLE_VIEWED, "event": "article.published"},
        )
        assert resp.status_code == 422

    def test_422_on_wrong_event_type_case_closed(self, client):
        resp = client.post(
            "/dev/webhooks/article-viewed",
            json={**ARTICLE_VIEWED, "event": "case.closed"},
        )
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — blank required fields (_require_nonempty guard)
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_article_id(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "articleId": "   "}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_blank_session_id(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "sessionId": ""}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # 422 — invalid enum values
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_invalid_channel_value(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "channel": "fax"}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_invalid_device_type(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "deviceType": "smartwatch"}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_each_valid_channel(self, _store, _enqueue, client):
        for channel in ("web", "portal", "mobile", "chat", "email"):
            data = {**ARTICLE_VIEWED["data"], "channel": channel}
            resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
            assert resp.status_code == 202, f"Failed for channel={channel}"

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_each_valid_device_type(self, _store, _enqueue, client):
        for device in ("desktop", "mobile", "tablet", "unknown"):
            data = {**ARTICLE_VIEWED["data"], "deviceType": device}
            resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
            assert resp.status_code == 202, f"Failed for deviceType={device}"

    # ------------------------------------------------------------------
    # 422 — numeric constraint violations
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_422_on_negative_duration_seconds(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "durationSeconds": -1}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 422

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", return_value=_S3_KEY)
    def test_202_on_zero_duration_seconds(self, _store, _enqueue, client):
        data = {**ARTICLE_VIEWED["data"], "durationSeconds": 0}
        resp = client.post("/dev/webhooks/article-viewed", json={**ARTICLE_VIEWED, "data": data})
        assert resp.status_code == 202

    # ------------------------------------------------------------------
    # 5xx — AWS failures
    # ------------------------------------------------------------------

    @patch("webhooks.enqueue_event")
    @patch("webhooks.store_webhook", side_effect=Exception("S3 is down"))
    def test_500_on_s3_failure(self, _store, _enqueue, error_client):
        resp = error_client.post("/dev/webhooks/article-viewed", json=ARTICLE_VIEWED)
        assert resp.status_code == 500
