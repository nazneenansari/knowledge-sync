# knowledge-sync

Event-driven pipeline that synchronises knowledge articles, case data, and usage analytics between **eGain** and **Salesforce Service Cloud**.

---
## API Documentation (OpenAPI / Swagger)

The `openapi.json` at the repository root is the machine-readable spec for the **webhook-event-handler** API.

### View in Swagger UI (browser)

1. Go to [swagger.io/tools/swagger-ui](https://swagger.io/tools/swagger-ui/) → **"Try it out"**, or open a local instance.
2. In the **"Explore"** box, paste the raw URL of `openapi.json`.
3. Click **Explore** — the UI renders all endpoints, request schemas, and example responses interactively.

### View locally while the server is running

```bash
cd webhook-event-handler
uvicorn main:app --reload
```

Then open:

| URL | What you get |
|---|---|
| `http://localhost:8000/docs` | Swagger UI (interactive — try requests live) |
| `http://localhost:8000/redoc` | ReDoc (read-only, cleaner layout) |
| `http://localhost:8000/openapi.json` | Raw JSON spec |

## System Overview

```
eGain CMS / Salesforce / eGain Portal
            │
            ▼
      API Gateway
            │
            ▼
  webhook-event-handler        ← FastAPI + Mangum Lambda
  (verifies HMAC, stores to S3, enqueues SQS message)
            │
        SQS Queue
            │
            ▼
     event-consumer            ← stdlib Python Lambda (EventBridge-triggered)
  (idempotency via DynamoDB, fetches payload from S3, POSTs to external API)
            │
       ┌────┴────┐
       ▼         ▼
  External API  SQS DLQ  (4xx, max retries exceeded, or malformed messages)
```

---

## Services

### [webhook-event-handler/](webhook-event-handler/)

Receives webhooks, verifies HMAC-SHA256 signatures, writes raw payloads to S3, and enqueues a lightweight reference message to SQS.

**Webhook endpoints**

| Endpoint | Source | Purpose |
|---|---|---|
| `POST /webhooks/article-published` | eGain CMS | Sync article to Salesforce Knowledge |
| `POST /webhooks/case-closed` | Salesforce | Trigger content-gap analysis |
| `POST /webhooks/article-viewed` | eGain Portal | Stream view analytics |

**Admin API**

| Endpoint | Purpose |
|---|---|
| `GET /admin/dlq` | Peek at events in the Dead Letter Queue |
| `POST /admin/dlq/{event_id}/replay` | Re-enqueue a failed event for reprocessing |

Stack: **FastAPI · Mangum · Pydantic v2 · AWS Lambda · S3 · SQS · Secrets Manager**

→ [Full details](webhook-event-handler/README.md)

### [event-consumer/](event-consumer/)

EventBridge-scheduled Lambda that batch-drains SQS (up to 500 messages), enforces distributed idempotency, retrieves payloads from S3, and forwards them to an external API with OAuth2 auth and exponential-backoff retry.

Stack: **Python 3.11 stdlib · AWS Lambda · SQS · DynamoDB · S3**

→ [Full details](event-consumer/README.md)

---

## Shared AWS Infrastructure

| Resource | Used by |
|---|---|
| SQS main queue | Both (webhook-event-handler enqueues; event-consumer drains) |
| SQS Dead Letter Queue | Both (unprocessable messages land here; admin API can replay) |
| S3 bucket | Both (webhook-event-handler writes; event-consumer reads) |
| Secrets Manager | webhook-event-handler (per-app HMAC secrets) |
| DynamoDB table | event-consumer (idempotency records) |
| API Gateway | webhook-event-handler (public HTTPS entry point) |

---

## Getting Started

Each service has its own virtual environment and dependencies.

```bash
# webhook-event-handler
cd webhook-event-handler
python -m venv .venv && .venv\Scripts\Activate.ps1   # Windows
pip install -r requirements.txt
uvicorn main:app --reload                             # local dev

# event-consumer
cd event-consumer
python -m venv .venv && .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

See each service's README for the full environment variable list and AWS infrastructure requirements.

---

## Running Tests

```bash
# from either service directory
pytest                                    # all tests
pytest --cov=. --cov-report=term-missing  # with coverage
pytest --cov=. --cov-report=html          # browsable HTML report (htmlcov/index.html)
```

No real AWS credentials are needed — all AWS calls are mocked.

---