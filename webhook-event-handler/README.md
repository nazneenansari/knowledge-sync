# webhook-event-handler

FastAPI application that receives webhooks from eGain and Salesforce, verifies their authenticity, stores the raw payload in S3, and enqueues a processing message in SQS.

---

## Architecture

```
eGain CMS / Salesforce / eGain Portal
              │
              ▼
        API Gateway  ──►  Lambda (webhook-event-handler / Mangum)
                               │
                               ├── Secrets Manager  (fetch per-app HMAC secret)
                               ├── S3               (store raw payload)
                               └── SQS Main Queue   (enqueue processing message)
                                         │
                                         │  (on max retries exhausted)
                                         ▼
                                   SQS Dead Letter Queue
                                         │
                               Admin API (list / replay)
```

### Module layout

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app setup and Mangum Lambda handler |
| `webhooks.py` | Three webhook endpoint definitions and shared processing logic |
| `models/schemas.py` | Pydantic v2 request/response models and enums |
| `middleware/signature.py` | HMAC-SHA256 signature verification FastAPI dependency |
| `middleware/secret_manager.py` | Per-app HMAC secret lookup from AWS Secrets Manager |
| `middleware/storage.py` | Writes raw payload to S3 (conditional write, no overwrites) |
| `middleware/queuing.py` | Publishes a lightweight reference message to SQS |
| `dlq/routes.py` | Admin endpoints to list and replay Dead Letter Queue events |
| `dlq/service.py` | SQS DLQ operations: peek messages and replay by event ID |

---

## Project setup

### Prerequisites

- Python 3.11+
- AWS CLI configured (`aws configure`) with access to SQS, S3, and Secrets Manager
- An SQS queue, an S3 bucket, and a Secrets Manager secret already created

### Step 1 — Clone the repository

```bash
git clone <repo-url>
cd knowledge-management/webhook-event-handler
```

### Step 2 — Create and activate a virtual environment

```bash
# Create
python -m venv .venv

# Activate (macOS / Linux)
source .venv/bin/activate

# Activate (Windows)
.venv\Scripts\activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Set environment variables

| Variable | Description |
|---|---|
| `BUCKET_NAME` | S3 bucket name for raw webhook storage |
| `QUEUE_URL` | Full SQS main queue URL (e.g. `https://sqs.us-east-1.amazonaws.com/123456789/my-queue`) |
| `DLQ_URL` | Full SQS Dead Letter Queue URL (e.g. `https://sqs.us-east-1.amazonaws.com/123456789/my-queue-dlq`) |

The Secrets Manager secret name is hardcoded as `event-producer-secret`.

```bash
# macOS / Linux
export BUCKET_NAME="my-webhooks-bucket"
export QUEUE_URL="https://sqs.us-east-1.amazonaws.com/123456789/my-queue"
export DLQ_URL="https://sqs.us-east-1.amazonaws.com/123456789/my-queue-dlq"

# Windows (PowerShell)
$env:BUCKET_NAME = "my-webhooks-bucket"
$env:QUEUE_URL   = "https://sqs.us-east-1.amazonaws.com/123456789/my-queue"
$env:DLQ_URL     = "https://sqs.us-east-1.amazonaws.com/123456789/my-queue-dlq"
```

### Step 5 — Create the Secrets Manager secret

The secret must be a JSON object with one key per registered app:

```bash
aws secretsmanager create-secret \
  --name event-producer-secret \
  --secret-string '{"EGAIN_WEBHOOK_HMAC_SECRET":"your-egain-secret","TEST_WEBHOOK_HMAC_SECRET":"your-test-secret"}'
```

The mapping from `appId` to secret key is:

| `appId` | Secret key |
|---|---|
| `egain` | `EGAIN_WEBHOOK_HMAC_SECRET` |
| `test` | `TEST_WEBHOOK_HMAC_SECRET` |

### Step 6 — Verify the setup

Start the development server and confirm it responds:

```bash
uvicorn main:app --reload
# Expected: Uvicorn running on http://127.0.0.1:8000
```

```bash
curl -s http://127.0.0.1:8000/docs
# Expected: FastAPI OpenAPI UI HTML
```

---

## Running the application

This function runs as an AWS Lambda via the [Mangum](https://mangum.fastapiexpert.com/) adapter. To deploy:

1. Zip the contents of this directory:

```bash
zip -r function.zip . --exclude "*.pyc" --exclude "__pycache__/*"
```

2. Deploy to Lambda:

```bash
aws lambda update-function-code \
  --function-name webhook-event-handler \
  --zip-file fileb://function.zip
```

3. Set environment variables on the Lambda function:

```bash
aws lambda update-function-configuration \
  --function-name webhook-event-handler \
  --environment "Variables={BUCKET_NAME=...,QUEUE_URL=...,DLQ_URL=...}"
```

4. Attach the Lambda to an API Gateway.

### Local development

```bash
uvicorn main:app --reload
```

---

## Running tests

### Install test dependencies

```bash
pip install pytest-cov
```

`pytest` is already included in `requirements.txt` (installed in Step 3).

### Run all tests

```bash
pytest tests/ -v
```

### Run a specific test file

```bash
pytest tests/test_endpoints.py -v
pytest tests/test_dlq.py -v
```

### Run tests matching a keyword

```bash
pytest tests/ -k "duplicate or signature" -v
pytest tests/ -k "dlq or replay" -v
```

No AWS credentials or live infrastructure are needed — all AWS calls are mocked.

---

## Coverage report

### Terminal report (shows uncovered line numbers)

```bash
pytest --cov=. --cov-report=term-missing tests/
```

### HTML report (browsable, line-by-line highlights)

```bash
pytest --cov=. --cov-report=html tests/
```

Open `htmlcov/index.html` in a browser after the run.

```

---

## API documentation

### Endpoints

| Method | Path | Source | Purpose |
|---|---|---|---|
| `POST` | `/dev/webhooks/article-published` | eGain CMS | Publish/update knowledge article to Salesforce Knowledge |
| `POST` | `/dev/webhooks/case-closed` | Salesforce | Analyse case for content gaps against existing articles |
| `POST` | `/dev/webhooks/article-viewed` | eGain Portal | Stream article view analytics events |
| `GET` | `/dev/admin/dlq` | Admin | List events currently in the Dead Letter Queue |
| `POST` | `/dev/admin/dlq/{event_id}/replay` | Admin | Re-enqueue a DLQ event for reprocessing |

---

### Common request headers

| Header | Required | Description |
|---|---|---|
| `x-signature` | Yes | HMAC-SHA256 signature (see below) |
| `x-timestamp` | Yes | Unix timestamp (seconds) of when the request was signed |
| `Content-Type` | Yes | `application/json` |

### Signature scheme

The signature is computed as:

```
HMAC-SHA256(secret, timestamp_bytes + b"\n" + raw_body)
```

- `secret` — the per-app HMAC secret fetched from Secrets Manager (resolved via `appId`)
- `timestamp` — the value of the `x-timestamp` header (Unix seconds, integer string)
- `raw_body` — the raw request body bytes, not re-serialised

Requests with a timestamp older or newer than **300 seconds** are rejected with 401 to prevent replay attacks.

---

### POST `/dev/webhooks/article-published`

**Source**: eGain CMS on article publish or update  
**Purpose**: Push article content to Salesforce Knowledge (`Knowledge__kav` sObject)

#### Request body

```json
{
  "event":    "article.published",
  "tenantId": "string (required)",
  "appId":    "string (required)",
  "timestamp": "ISO-8601 string (required)",
  "data": {
    "articleId":  "string (required)",
    "title":      "string (required)",
    "urlName":    "string (required, URL-safe slug)",
    "content":    "string (required, HTML)",
    "language":   "string (optional, default: en-US)",
    "version":    "string (optional)",
    "categories": ["string"] 
  }
}
```

`event` must be exactly `"article.published"`.

#### Response (202)

```json
{
  "status":   "accepted",
  "event_id": "uuid",
  "message":  "Article published event accepted for processing"
}
```

---

### POST `/dev/webhooks/case-closed`

**Source**: Salesforce (Outbound Message or Platform Event) on case closure  
**Purpose**: Determine whether the case was resolved with existing articles; compute content gaps

#### Request body

```json
{
  "event":    "case.closed",
  "tenantId": "string (required)",
  "appId":    "string (required)",
  "timestamp": "ISO-8601 string (required)",
  "data": {
    "caseId":           "string (required)",
    "subject":          "string (required)",
    "description":      "string (required)",
    "caseNumber":       "string (optional)",
    "resolution":       "string (optional)",
    "category":         "string (optional, default: Uncategorised)",
    "priority":         "Low|Medium|High|Critical (optional, default: Medium)",
    "articleIds":       ["string"],
    "viewedArticleIds": ["string"]
  }
}
```

`event` must be exactly `"case.closed"`.

#### Response (202)

```json
{
  "status":   "accepted",
  "event_id": "uuid",
  "message":  "Case closed event accepted for processing"
}
```

---

### POST `/dev/webhooks/article-viewed`

**Source**: eGain Portal on every article page view  
**Purpose**: Stream view analytics to eGain Analytics API (or Kafka/Kinesis)  
**Note**: High-throughput endpoint — expects 100s of events per second in bursts

#### Request body

```json
{
  "event":    "article.viewed",
  "tenantId": "string (required)",
  "appId":    "string (required)",
  "timestamp": "ISO-8601 string (required)",
  "data": {
    "articleId":       "string (required)",
    "sessionId":       "string (required)",
    "articleVersion":  "string (optional)",
    "userId":          "string (optional)",
    "channel":         "web|portal|mobile|chat|email (optional)",
    "durationSeconds": "integer (optional)",
    "helpful":         "true|false|null (optional)",
    "searchQuery":     "string (optional)",
    "caseId":          "string (optional)",
    "userAgent":       "string (optional)",
    "locale":          "string (optional)",
    "deviceType":      "desktop|mobile|tablet|unknown (optional)",
    "timestamp":       "ISO-8601 string (optional)"
  }
}
```

`event` must be exactly `"article.viewed"`.

#### Response (202)

```json
{
  "status":   "accepted",
  "event_id": "uuid",
  "message":  "Article viewed event accepted for processing"
}
```

---

### GET `/dev/admin/dlq`

**Purpose**: Peek at events currently sitting in the Dead Letter Queue.  
Messages are not consumed — visibility is reset immediately so they remain in the DLQ.

#### Query parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `event` | `EventType` enum | _(none)_ | Optional filter. One of `egain.article.published`, `salesforce.case.closed`, `egain.article.viewed`. Omit to return all event types. |

#### Response (200)

```json
{
  "total": 3,
  "items": [
    {
      "event_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "event": "egain.article.published",
      "status_code": 500,
      "attempts": 3,
      "createdAt": "2024-06-01T12:00:00Z",
      "updatedAt": "2024-06-01T12:05:00Z",
      "error": "Salesforce API returned 503",
      "dlqReason": "Max retry attempts exceeded",
      "dlqAt": "2024-06-01T12:05:00Z",
      "meta": { "tenantId": "acme-corp", "appId": "egain" }
    }
  ]
}
```

> **Note**: SQS surfaces at most 10 messages per call (its batch ceiling). The `total` field reflects the approximate queue depth from `ApproximateNumberOfMessages`.

---

### POST `/dev/admin/dlq/{event_id}/replay`

**Purpose**: Re-enqueue a specific DLQ event for reprocessing.  
The original DLQ entry is deleted and a new event with a fresh UUID is sent to the main SQS queue. The new message's `meta` includes `replayed_from` (original event ID) and `replayed_at` (ISO-8601 timestamp) for traceability.

#### Path parameter

| Parameter | Type | Description |
|---|---|---|
| `event_id` | UUID | The `event_id` from the DLQ list response |

#### Response (202)

```json
{
  "status": "accepted",
  "replayedEventId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "newEventId": "7cb96a18-1234-4abc-9def-aabbccddeeff",
  "message": "Event re-enqueued for reprocessing."
}
```

#### Error responses

| Status | Condition |
|---|---|
| `404 Not Found` | No event with that ID exists in the DLQ |
| `422 Unprocessable Entity` | `event_id` path parameter is not a valid UUID |
| `500 Internal Server Error` | SQS call failed |

---

### Common responses

| Status | Condition |
|---|---|
| `202 Accepted` | Event accepted, stored in S3, and enqueued in SQS |
| `400 Bad Request` | Invalid JSON body |
| `401 Unauthorized` | Missing/invalid signature, stale timestamp, or unknown `appId` |
| `422 Unprocessable Entity` | Required field is blank |
| `500 Internal Server Error` | AWS service failure (S3, SQS, or Secrets Manager) |

#### 401 response

```json
{
  "error":   "UNAUTHORIZED",
  "message": "Invalid signature"
}
```

#### 500 response

```json
{
  "error":   "INTERNAL_SERVER_ERROR",
  "message": "An unexpected error occurred"
}
```

---

### S3 storage path

Raw payloads are stored at:

```
webhooks/raw/{tenantId}/{appId}/{event_name}/{eventId}.json
```

Where `event_name` is the dot-separated event type with dots replaced by slashes, e.g. `article.published` → stored under the `article.published` prefix.

### SQS message shape

```json
{
  "tenantId":   "string",
  "appId":      "string",
  "event":      "string",
  "eventId":    "string",
  "receivedAt": "2025-05-05T10:00:00+00:00",
  "s3Bucket":   "string",
  "s3Key":      "webhooks/raw/{tenantId}/{appId}/{event_name}/{eventId}.json"
}
```

---

## Technology choices

| Technology | Choice | Rationale |
|---|---|---|
| **Framework** | FastAPI | Async-first, automatic OpenAPI docs, Pydantic v2 integration, dependency injection for middleware |
| **Runtime** | Python 3.11 | Native Lambda support; `hmac`, `hashlib`, `json` are stdlib |
| **Lambda adapter** | Mangum | Translates API Gateway events to ASGI requests with no code changes |
| **Compute** | AWS Lambda | Scales to zero; pay-per-request pricing suits bursty webhook traffic |
| **Queue** | AWS SQS | Decouples ingestion from processing; built-in retry and dead-letter queue support |
| **Storage** | AWS S3 | Durable, cheap storage for raw payloads; workers read the full payload from S3 (avoids 256 KB SQS message limit) |
| **Secrets** | AWS Secrets Manager | Per-app HMAC secrets without redeployment; secrets are not exposed in Lambda environment variables |
| **Signature scheme** | HMAC-SHA256 + timestamp | `sha256=` prefixed digest; timestamp binding prevents replay attacks within a 5-minute window |
| **Duplicate prevention** | S3 `IfNoneMatch: *` | Atomic conditional write — no TOCTOU race; duplicate `eventId` is silently accepted (202) so the sender stops retrying |
| **Validation** | Pydantic v2 | Per-event typed schemas with field-level validators; generates accurate OpenAPI documentation |
