"""Lambda entry point — drains SQS, enforces idempotency, calls external API, routes failures to DLQ."""
import logging
import os
from middleware.idempotency import acquire_idempotency, update_status
from middleware.sqs_client import drain_sqs, delete_message, send_to_dlq
from middleware.s3_client import fetch_payload
from service.api_client import call_external_api_with_retry

from config import LOG_LEVEL

logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

def lambda_handler(event, context):
    """Returns {"processed": int, "skipped": int, "failed": int} — skipped covers both already-completed and in-flight events."""
    processed = 0
    skipped = 0
    failed = 0

    messages = drain_sqs()

    if not messages:
        logger.info("No messages in SQS")
        return {"processed": 0, "skipped": 0, "failed": 0}

    for msg in messages:
        tenant_id = msg["tenantId"]
        event_id = msg["eventId"]
        app_id = msg["appId"]
        receipt_handle = msg["receipt_handle"]

        try:
            decision = acquire_idempotency(msg)

            if decision is False:
                logger.info(f"{tenant_id}:{event_id} already completed, skipping")
                delete_message(receipt_handle)
                skipped += 1
                continue

            if decision is None:
                logger.info(f"{tenant_id}:{event_id} in-flight on another processor, skipping")
                skipped += 1
                continue
            payload = fetch_payload(msg["s3Key"], msg["s3Bucket"])
            status_code, resp = call_external_api_with_retry(payload)

            if 200 <= status_code < 300:
                update_status(event_id, app_id, "COMPLETED")
                delete_message(receipt_handle)
                processed += 1
            else:
                logger.error(f"{tenant_id}:{event_id} FAILED — client error {status_code}")
                send_to_dlq(msg, status_code, "Client error")
                delete_message(receipt_handle)
                failed += 1

        except Exception as e:
            logger.error(f"{tenant_id}:{event_id} FAILED — {type(e).__name__}: {e}")
            send_to_dlq(
                msg,
                status_code=getattr(e, "status_code", None),
                reason=f"{type(e).__name__}: {str(e)}"
            )
            delete_message(receipt_handle)
            failed += 1

    logger.info("Batch complete", extra={"processed": processed, "skipped": skipped, "failed": failed})
    return {"processed": processed, "skipped": skipped, "failed": failed}
