"""
dlq/routes.py
-------------
Admin endpoints for inspecting and replaying Dead Letter Queue events.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from models.schemas import (
    DLQListResponse,
    ErrorResponse,
    EventType,
    ForbiddenError,
    InternalServerError,
    ReplayResponse,
    UnauthorizedError,
)
from dlq.service import list_dlq_events, replay_dlq_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dev/admin", tags=["Admin - DLQ"])

_ADMIN_ERRORS: dict[int, dict[str, Any]] = {
    401: {"description": "Missing or invalid authentication credentials", "model": UnauthorizedError},
    403: {"description": "Forbidden – insufficient permissions",          "model": ForbiddenError},
    500: {"description": "Internal server error",                         "model": InternalServerError},
}

_list_dlq_docs: dict[str, Any] = {
    "response_model": DLQListResponse,
    "summary": "List all events in the Dead Letter Queue",
    "description": (
        "Returns a paginated list of events that exhausted all retry attempts. "
        "Messages are peeked — visibility is reset immediately so they remain in the DLQ. "
        "Use `POST /admin/dlq/{event_id}/replay` to re-enqueue any entry."
    ),
    "responses": {
        200: {"description": "Paginated DLQ event list", "model": DLQListResponse},
        **_ADMIN_ERRORS,
    },
}

_replay_dlq_docs: dict[str, Any] = {
    "response_model": ReplayResponse,
    "summary": "Replay a dead-letter event",
    "description": (
        "Re-enqueues a DLQ event for reprocessing. The original DLQ entry is removed "
        "and a **new event** (new UUID) is created. The new event's `meta` includes "
        "`replayed_from` and `replayed_at` for traceability."
    ),
    "responses": {
        202: {"description": "Event re-enqueued for reprocessing", "model": ReplayResponse},
        404: {"description": "Event not found in DLQ",             "model": ErrorResponse},
        **_ADMIN_ERRORS,
    },
}

@router.get("/dlq", **_list_dlq_docs)
async def list_dlq(event: EventType | None = None) -> DLQListResponse:
    try:
        return list_dlq_events(event_type=event)
    except Exception:
        logger.exception("Failed to list DLQ events")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve DLQ events.",
        )

@router.post(
    "/dlq/{event_id}/replay",
    status_code=status.HTTP_202_ACCEPTED,
    **_replay_dlq_docs,
)
async def replay_event(event_id: UUID) -> ReplayResponse:
    try:
        result = replay_dlq_event(event_id=event_id)
    except Exception:
        logger.exception("Failed to replay DLQ event %s", event_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to replay DLQ event.",
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found in DLQ.",
        )
    return result
