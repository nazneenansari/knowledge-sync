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
                               └── SQS              (enqueue processing message)
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
| `QUEUE_URL` | Full SQS queue URL (e.g. `https://sqs.us-east-1.amazonaws.com/123456789/my-queue`) |

The Secrets Manager secret name is hardcoded as `event-producer-secret`.

```bash
# macOS / Linux
export BUCKET_NAME="my-webhooks-bucket"
export QUEUE_URL="https://sqs.us-east-1.amazonaws.com/123456789/my-queue"

# Windows (PowerShell)
$env:BUCKET_NAME = "my-webhooks-bucket"
$env:QUEUE_URL   = "https://sqs.us-east-1.amazonaws.com/123456789/my-queue"
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
  --environment "Variables={BUCKET_NAME=...,QUEUE_URL=...}"
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
```

### Run tests matching a keyword

```bash
pytest tests/ -k "duplicate or signature" -v
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
