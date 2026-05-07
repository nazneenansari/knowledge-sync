"""
models/schemas.py
-----------------
All Pydantic v2 models for the eGain ↔ Salesforce Webhook API.
FastAPI generates the OpenAPI 3.1 spec entirely from these definitions.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class EventStatus(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    COMPLETED  = "completed"
    RETRYING   = "retrying"
    DEAD       = "dead"


class EventType(str, Enum):
    ARTICLE_PUBLISHED = "egain.article.published"
    CASE_CLOSED       = "salesforce.case.closed"
    ARTICLE_VIEWED    = "egain.article.viewed"


class Priority(str, Enum):
    LOW      = "Low"
    MEDIUM   = "Medium"
    HIGH     = "High"
    CRITICAL = "Critical"


class Channel(str, Enum):
    WEB    = "web"
    PORTAL = "portal"
    MOBILE = "mobile"
    CHAT   = "chat"
    EMAIL  = "email"


class DeviceType(str, Enum):
    DESKTOP = "desktop"
    MOBILE  = "mobile"
    TABLET  = "tablet"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ── FLOW 1: eGain Article Published → Salesforce Knowledge
# ---------------------------------------------------------------------------

class ArticleData(BaseModel):
    """Knowledge article payload from eGain CMS."""

    article_id: str = Field(
        ...,
        alias="articleId",
        description="Unique article identifier in eGain.",
        examples=["ARTICLE-1001"],
    )
    title: str = Field(
        ...,
        max_length=255,
        description="Article title — maps to Salesforce Knowledge `Title` field.",
        examples=["How to reset your password"],
    )
    url_name: str = Field(
        ...,
        alias="urlName",
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe slug. Maps to Salesforce `UrlName`. Must be unique per org.",
        examples=["how-to-reset-password"],
    )
    content: str = Field(
        ...,
        description="Full HTML article body. Maps to Salesforce `ArticleBody`.",
        examples=["<p>Step 1: Click <b>Forgot Password</b>...</p>"],
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language tag. Maps to Salesforce `Language`.",
        examples=["en-US", "fr-FR", "de-DE"],
    )
    version: str | None = Field(
        default=None,
        description="Article version string stored in custom field `eGain__Source_Article_Version__c`.",
        examples=["2.0"],
    )
    categories: list[str] = Field(
        default_factory=list,
        description="eGain category names. Joined with ';' into Salesforce `eGain__Categories__c`.",
        examples=[["Account Management", "Security"]],
    )

    model_config = {"populate_by_name": True}


class ArticlePublishedWebhook(BaseModel):
    """
    Webhook envelope sent by eGain when a knowledge article is published or updated.
    Triggers a push to **Salesforce Knowledge** (`Knowledge__kav` sObject).
    """

    webhook_id: str | None = Field(
        default=None,
        alias="webhookId",
        description="Unique delivery ID assigned by eGain. Use for idempotency checks.",
        examples=["wh_01HZK8M3N2PQRS"],
    )
    event: str = Field(
        ...,
        description="Must be exactly `article.published`.",
        examples=["article.published"],
    )
    tenant_id: str = Field(
        ...,
        alias="tenantId",
        description="eGain tenant identifier. Required for all webhook calls.",
        examples=["acme-corp"],
    )
    app_id: str = Field(
        ...,
        alias="appId",
        description="Identifier of the application triggering this webhook.",
        examples=["egain-cms-v3"],
    )
    timestamp: datetime = Field(
        ...,
        description="ISO-8601 event time in UTC.",
        examples=["2024-06-01T12:00:00Z"],
    )
    data: ArticleData

    @field_validator("event")
    @classmethod
    def event_must_be_article_published(cls, v: str) -> str:
        if v != "article.published":
            raise ValueError(f'Expected "article.published", got "{v}"')
        return v

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# ── FLOW 2: Salesforce Case Closed → eGain Content Gap Analysis
# ---------------------------------------------------------------------------

class CaseData(BaseModel):
    """Salesforce case payload delivered on case closure."""

    case_id: str = Field(
        ...,
        alias="caseId",
        description="Salesforce Case record ID (15 or 18-char Salesforce ID).",
        examples=["5003000000ABC123AAA"],
    )
    case_number: str | None = Field(
        default=None,
        alias="caseNumber",
        description="Human-readable case number shown in Salesforce UI.",
        examples=["00001234"],
    )
    subject: str = Field(
        ...,
        max_length=255,
        description="Case subject line.",
        examples=["Cannot log into portal"],
    )
    description: str = Field(
        ...,
        description="Full case description / customer problem statement.",
        examples=["User says password reset email not arriving."],
    )
    resolution: str | None = Field(
        default=None,
        description="Agent resolution notes. Null = no resolution recorded.",
        examples=["Sent user the password reset knowledge article."],
    )
    category: str | None = Field(
        default="Uncategorised",
        description="Salesforce case category / record type.",
        examples=["Account Management"],
    )
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Salesforce case priority.",
        examples=["Medium"],
    )
    article_ids: list[str] = Field(
        default_factory=list,
        alias="articleIds",
        description="eGain article IDs **suggested** to the agent during the case.",
        examples=[["ARTICLE-1001", "ARTICLE-1005"]],
    )
    viewed_article_ids: list[str] = Field(
        default_factory=list,
        alias="viewedArticleIds",
        description="eGain article IDs the agent actually **opened/read**.",
        examples=[["ARTICLE-1001"]],
    )

    model_config = {"populate_by_name": True}


class CaseClosedWebhook(BaseModel):
    """
    Webhook envelope from Salesforce (Outbound Message or Platform Event)
    when a support case is closed. Triggers content-gap analysis in eGain.
    """

    event: str = Field(
        ...,
        description="Must be exactly `case.closed`.",
        examples=["case.closed"],
    )
    tenant_id: str = Field(
        ...,
        alias="tenantId",
        description="eGain tenant identifier. Required for all webhook calls.",
        examples=["acme-corp"],
    )
    app_id: str = Field(
        ...,
        alias="appId",
        description="Identifier of the application triggering this webhook.",
        examples=["salesforce-service-cloud"],
    )
    timestamp: datetime = Field(
        ...,
        description="ISO-8601 event time in UTC.",
        examples=["2024-06-01T15:00:00Z"],
    )
    data: CaseData

    @field_validator("event")
    @classmethod
    def event_must_be_case_closed(cls, v: str) -> str:
        if v != "case.closed":
            raise ValueError(f'Expected "case.closed", got "{v}"')
        return v

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# ── FLOW 3: eGain Article Viewed → Stream Analytics back to eGain
# ---------------------------------------------------------------------------

class ArticleViewData(BaseModel):
    """Article view analytics event from the eGain portal."""

    article_id: str = Field(
        ...,
        alias="articleId",
        description="eGain article identifier.",
        examples=["ARTICLE-1001"],
    )
    article_version: str | None = Field(
        default=None,
        alias="articleVersion",
        description="Version of the article viewed. Null if version tracking is off.",
        examples=["2.0"],
    )
    session_id: str = Field(
        ...,
        alias="sessionId",
        description="Browser / portal session identifier.",
        examples=["sess_abc123def456"],
    )
    user_id: str | None = Field(
        default=None,
        alias="userId",
        description="Authenticated user ID. Null for anonymous (self-service) views.",
        examples=["usr_xyz789"],
    )
    channel: Channel = Field(
        default=Channel.WEB,
        description="Surface where the article was viewed.",
        examples=["web"],
    )
    duration_seconds: int | None = Field(
        default=None,
        alias="durationSeconds",
        ge=0,
        description="Time in seconds the user spent on the article page.",
        examples=[45],
    )
    helpful: bool | None = Field(
        default=None,
        description=(
            "User's helpfulness rating. "
            "`true` = helpful, `false` = not helpful, `null` = not yet rated."
        ),
        examples=[True],
    )
    search_query: str | None = Field(
        default=None,
        alias="searchQuery",
        description="Search term that led the user to this article, if any.",
        examples=["reset password"],
    )
    case_id: str | None = Field(
        default=None,
        alias="caseId",
        description="Salesforce Case ID if the article was viewed in a case context.",
        examples=["5003000000ABC123AAA"],
    )
    user_agent: str | None = Field(
        default=None,
        alias="userAgent",
        description="Browser user-agent string.",
        examples=["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"],
    )
    locale: str = Field(
        default="en-US",
        description="User locale (BCP-47).",
        examples=["en-US"],
    )
    device_type: DeviceType = Field(
        default=DeviceType.UNKNOWN,
        alias="deviceType",
        description="Device category derived from user-agent.",
        examples=["desktop"],
    )
    timestamp: datetime | None = Field(
        default=None,
        description="Client-side event time. Falls back to server receipt time if absent.",
        examples=["2024-06-01T17:00:00Z"],
    )

    model_config = {"populate_by_name": True}


class ArticleViewedWebhook(BaseModel):
    """
    Webhook envelope sent by eGain on every article page view.
    High-throughput endpoint — expect bursts of hundreds of events per second.
    Events are streamed to the eGain Analytics API (or Kafka/Kinesis in production).
    """

    webhook_id: str | None = Field(
        default=None,
        alias="webhookId",
        examples=["wh_03HZK0N5O4RSTU"],
    )
    event: str = Field(
        ...,
        description="Must be exactly `article.viewed`.",
        examples=["article.viewed"],
    )
    tenant_id: str = Field(
        ...,
        alias="tenantId",
        description="eGain tenant identifier. Required for all webhook calls.",
        examples=["acme-corp"],
    )
    app_id: str = Field(
        ...,
        alias="appId",
        description="Identifier of the application triggering this webhook.",
        examples=["egain-portal-v2"],
    )
    timestamp: datetime = Field(
        ...,
        examples=["2024-06-01T17:00:00Z"],
    )
    data: ArticleViewData

    @field_validator("event")
    @classmethod
    def event_must_be_article_viewed(cls, v: str) -> str:
        if v != "article.viewed":
            raise ValueError(f'Expected "article.viewed", got "{v}"')
        return v

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# ── Shared response models
# ---------------------------------------------------------------------------

class AcceptedResponse(BaseModel):
    """Returned immediately (HTTP 202) for every inbound webhook."""

    status: str = Field(default="accepted", examples=["accepted"])
    event_id: UUID = Field(
        ...,
        alias="eventId",
        description="Assigned UUID for status polling.",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )
    message: str = Field(
        ...,
        examples=["Article publish event queued for processing."],
    )

    model_config = {"populate_by_name": True}


class EventStatusResponse(BaseModel):
    """Full status record for a queued or completed event."""

    id: UUID = Field(..., examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"])
    type: EventType
    status: EventStatus
    attempts: int = Field(..., ge=0, examples=[1])
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    error: str | None = Field(default=None, examples=["Salesforce API returned 503"])
    dlq_reason: str | None = Field(
        default=None,
        alias="dlqReason",
        examples=["Max retry attempts (3) exceeded"],
    )
    dlq_at: datetime | None = Field(default=None, alias="dlqAt")
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Source metadata attached at enqueue time.",
    )

    model_config = {"populate_by_name": True}


class DLQListResponse(BaseModel):
    """Paginated list of dead-letter queue events."""

    total: int = Field(..., examples=[3])
    page: int = Field(..., examples=[1])
    limit: int = Field(..., examples=[20])
    items: list[EventStatusResponse]


class ReplayResponse(BaseModel):
    """Returned when a DLQ event is successfully re-enqueued."""

    status: str = Field(default="accepted", examples=["accepted"])
    replayed_event_id: UUID = Field(
        ...,
        alias="replayedEventId",
        description="The original DLQ event ID that was replayed.",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )
    new_event_id: UUID = Field(
        ...,
        alias="newEventId",
        description="Newly assigned event ID for the re-enqueued event.",
        examples=["7cb96a18-1234-4abc-9def-aabbccddeeff"],
    )
    message: str = Field(
        ...,
        examples=["Event re-enqueued for reprocessing."],
    )

    model_config = {"populate_by_name": True}


class QueueMetrics(BaseModel):
    received: int
    processed: int
    failed: int
    retried: int
    dlq: int
    active_queue_size: int = Field(..., alias="activeQueueSize")
    dlq_size: int = Field(..., alias="dlqSize")
    registered_handlers: list[str] = Field(..., alias="registeredHandlers")

    model_config = {"populate_by_name": True}


class HealthResponse(BaseModel):
    status: str = Field(default="ok", examples=["ok"])
    timestamp: datetime
    uptime_seconds: float = Field(..., alias="uptimeSeconds")
    queue: QueueMetrics

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# ── Error models
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    """Standard error envelope returned on 4xx responses where the body does not have a dedicated schema."""

    error: str = Field(
        ...,
        description="Machine-readable error code.",
        examples=["INVALID_PAYLOAD"],
    )
    message: str = Field(
        ...,
        description="Human-readable error description.",
        examples=["Payload must include data.articleId."],
    )
    details: list[str] | None = Field(
        default=None,
        description="Optional list of field-level validation errors.",
        examples=[["data.articleId: field required", "data.urlName: field required"]],
    )


class UnauthorizedError(BaseModel):
    """Returned on HTTP 401 when authentication credentials are missing or the HMAC signature cannot be verified."""

    error: str = Field(
        ...,
        description="Machine-readable error code. Always `UNAUTHORIZED` for 401 responses.",
        examples=["UNAUTHORIZED"],
    )
    message: str = Field(
        ...,
        description="Human-readable description of the authentication failure.",
        examples=["Missing or invalid HMAC signature."],
    )


class ForbiddenError(BaseModel):
    """Returned on HTTP 403 when the caller is authenticated but not permitted to perform the requested operation."""

    error: str = Field(
        ...,
        description="Machine-readable error code. Always `FORBIDDEN` for 403 responses.",
        examples=["FORBIDDEN"],
    )
    message: str = Field(
        ...,
        description="Human-readable description of why the request was denied.",
        examples=["Tenant acme-corp is not authorised to publish articles."],
    )


class InternalServerError(BaseModel):
    """Returned on HTTP 500 when an unexpected server-side error prevents the request from completing."""

    error: str = Field(
        ...,
        description="Machine-readable error code. Always `INTERNAL_SERVER_ERROR` for 500 responses.",
        examples=["INTERNAL_SERVER_ERROR"],
    )
    message: str = Field(
        ...,
        description="Human-readable description of the server-side failure.",
        examples=["An unexpected error occurred. Please try again later."],
    )
    details: list[str] | None = Field(
        default=None,
        description="Optional diagnostic detail. Omitted in production to avoid leaking internal state.",
        examples=[None],
    )
