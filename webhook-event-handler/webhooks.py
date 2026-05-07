"""
routes/webhooks.py
------------------
Three webhook receiver endpoints — one per integration flow.
Each endpoint:
  1. Verifies HMAC-SHA256 signature (injected via Depends)
  2. Validates the request body via Pydantic models
  3. Enqueues the event for async processing
  4. Returns 202 Accepted immediately
"""

import logging
import os
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from middleware.signature import make_signature_verifier
from models.schemas import (
    AcceptedResponse,
    ArticlePublishedWebhook,
    ArticleViewedWebhook,
    CaseClosedWebhook,
    ForbiddenError,
    InternalServerError,
    UnauthorizedError,
)
from middleware.queuing import enqueue_event
from middleware.storage import DuplicateEventError, store_webhook

logger = logging.getLogger(__name__)

BUCKET_NAME = os.environ["BUCKET_NAME"]
QUEUE_URL   = os.environ["QUEUE_URL"]

router = APIRouter(prefix="/dev/webhooks")

_verify = make_signature_verifier()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _require_nonempty(fields: dict[str, str]) -> None:
    for field, value in fields.items():
        if not value.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{field} must not be blank",
            )


def _process_event(
    event_id: UUID,
    payload: dict,
    message: str,
) -> AcceptedResponse:
    str_event_id = str(event_id)
    enriched = {**payload, "eventId": str_event_id}
    try:
        s3_key = store_webhook(BUCKET_NAME, enriched)
    except DuplicateEventError:
        logger.info("Duplicate event ignored: %s", str_event_id)
        return AcceptedResponse(eventId=str_event_id, message=message)

    enqueue_event(QUEUE_URL, BUCKET_NAME, s3_key, enriched)
    logger.info("Event enqueued: %s", str_event_id)
    return AcceptedResponse(eventId=event_id, message=message)


# ---------------------------------------------------------------------------
# OpenAPI metadata — shared error responses + per-endpoint docs blocks
# ---------------------------------------------------------------------------

_ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    401: {"description": "Missing or invalid HMAC signature", "model": UnauthorizedError},
    403: {"description": "Forbidden – tenant not authorised for this operation", "model": ForbiddenError},
    500: {"description": "Internal server error", "model": InternalServerError},
}

_article_published_docs: dict[str, Any] = {
    "tags": ["Webhooks - Knowledge Articles"],
    "response_model": AcceptedResponse,
    "summary": "Receive article-published event",
    "responses": {
        202: {"description": "Event accepted and queued", "model": AcceptedResponse},
        **_ERROR_RESPONSES,
    }
}

_case_closed_docs: dict[str, Any] = {
    "tags": ["Webhooks - Cases"],
    "response_model": AcceptedResponse,
    "summary": "Receive case-closed event",
    "responses": {
        202: {"description": "Event accepted and queued", "model": AcceptedResponse},
        **_ERROR_RESPONSES,
    }
}

_article_viewed_docs: dict[str, Any] = {
    "tags": ["Webhooks - Analytics"],
    "response_model": AcceptedResponse,
    "summary": "Receive article-viewed analytics event",
    "responses": {
        202: {"description": "Analytics event accepted and queued", "model": AcceptedResponse},
        **_ERROR_RESPONSES,
    }
}

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/article-published", status_code=status.HTTP_202_ACCEPTED, **_article_published_docs)
async def receive_article_published(
    body: ArticlePublishedWebhook,
    _: None = Depends(_verify),
) -> AcceptedResponse:
    """
    Called by the **eGain CMS** whenever a knowledge article is published or updated.
    The service transforms the payload and enqueues a push to **Salesforce Knowledge**
    (`Knowledge__kav` sObject).

    Request body must include `"event": "article.published"`, `tenantId` (eGain tenant),
    and `appId` (the application triggering the webhook).

    **Processing flow:**
    1. eGain publishes article → fires this webhook
    2. HMAC-SHA256 signature is verified
    3. System validates the request body
    4. Event is enqueued → `202 Accepted` returned immediately
    5. Worker transforms the article and calls Salesforce Knowledge API
    """
    _require_nonempty({
        "event":          body.event,
        "tenantId":       body.tenant_id,
        "appId":          body.app_id,
        "data.articleId": body.data.article_id,
        "data.title":     body.data.title,
        "data.content":   body.data.content,
    })
    return _process_event(
        uuid4(),
        body.model_dump(mode="json", by_alias=True),
        "Article publish event queued for processing.",
    )


@router.post("/case-closed", status_code=status.HTTP_202_ACCEPTED, **_case_closed_docs)
async def receive_case_closed(
    body: CaseClosedWebhook,
    _: None = Depends(_verify),
) -> AcceptedResponse:
    """
    Called by **Salesforce** (Outbound Message or Platform Event) when a support case
    is closed. The service analyses whether the case was resolved with existing
    knowledge articles, computes a **content-gap**, and records a gap entry in eGain.

    Request body must include `"event": "case.closed"`, `tenantId` (eGain tenant),
    and `appId` (the application triggering the webhook).

    **Processing flow:**
    1. Salesforce closes case → fires this webhook
    2. HMAC-SHA256 signature verified
    3. Event enqueued → `202 Accepted` returned
    4. Worker stores data for scheduled batch content-gap analysis
    """
    _require_nonempty({
        "event":            body.event,
        "tenantId":         body.tenant_id,
        "appId":            body.app_id,
        "data.caseId":      body.data.case_id,
        "data.subject":     body.data.subject,
        "data.description": body.data.description,
    })
    return _process_event(
        uuid4(),
        body.model_dump(mode="json", by_alias=True),
        "Case closed event queued for processing.",
    )


@router.post("/article-viewed", status_code=status.HTTP_202_ACCEPTED, **_article_viewed_docs)
async def receive_article_viewed(
    body: ArticleViewedWebhook,
    _: None = Depends(_verify),
) -> AcceptedResponse:
    """
    Called by the **eGain portal** on every article page view. Events are streamed
    to the **eGain Analytics API** (or Kafka/Kinesis in high-volume deployments).

    Request body must include `"event": "article.viewed"`, `tenantId` (eGain tenant),
    and `appId` (the application triggering the webhook).

    This is a **high-throughput** endpoint — expect bursts of hundreds of events
    per second during peak support hours.

    **Processing flow:**
    1. eGain fires view event in real time
    2. Signature verified, body validated
    3. Event enqueued → `202 Accepted` returned
    4. Worker batches and forwards to `POST /api/v1/analytics/events`
    5. Analytics pipeline updates article usage metrics in eGain
    """
    _require_nonempty({
        "event":          body.event,
        "tenantId":       body.tenant_id,
        "appId":          body.app_id,
        "data.articleId": body.data.article_id,
        "data.sessionId": body.data.session_id,
    })
    return _process_event(
        uuid4(),
        body.model_dump(mode="json", by_alias=True),
        "Article view analytics event queued for streaming.",
    )
