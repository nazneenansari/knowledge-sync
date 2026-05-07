# Event Consumer — AWS Lambda

A Python AWS Lambda that drains an SQS queue, enforces distributed idempotency via DynamoDB, fetches event payloads from S3, and forwards them to an external API with OAuth2 authentication and exponential-backoff retry. Unprocessable messages are routed to a Dead Letter Queue.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Setup Instructions](#setup-instructions)
- [Running the Application](#running-the-application)
- [Running Tests](#running-tests)
- [Environment Variables](#environment-variables)
- [API Documentation](#api-documentation)
- [Technology Choices and Rationale](#technology-choices-and-rationale)

---

## Architecture Overview

```
Amazon EventBridge Scheduler
      │
      ▼
 lambda_handler   (main.py)
      │
      ├─ drain_sqs()              ← batch-polls up to 500 messages (50 × 10)
      │       │
      │       └─ malformed msg → DLQ + delete
      │
      ├─ acquire_idempotency()    ← DynamoDB conditional write
      │       │
      │       ├─ COMPLETED  → skip + delete
      │       ├─ in-flight  → skip (leave visible)
      │       └─ stale lock → reacquire
      │
      ├─ fetch_payload()          ← S3 GetObject
      │
      ├─ call_external_api_with_retry()
      │       │
      │       ├─ 2xx → update_status(COMPLETED) + delete_message()
      │       ├─ 4xx → send_to_dlq() + delete (no retry)
      │       └─ 5xx / network → exponential backoff + retry → send_to_dlq() + delete
      │
      └─ send_to_dlq()            ← marks FAILED in DynamoDB, enriches with reason/timestamp
```

---

## Project Structure

```
event-consumer/
├── config.py                       # Env var loading and tuneable constants
├── main.py                         # Lambda entry point (lambda_handler)
├── middleware/
│   ├── idempotency.py              # DynamoDB conditional-write idempotency guard
│   ├── s3_client.py                # S3 payload fetch
│   └── sqs_client.py              # SQS drain, delete, DLQ routing
├── service/
│   ├── api_client.py               # External API POST + retry/backoff
│   └── auth.py                     # OAuth2 client-credentials token cache
├── tests/
│   ├── conftest.py                 # AWS + app env vars bootstrapped before imports
│   └── unit/
│       ├── test_config.py          # 22 tests — env var loading, type coercion, defaults
│       ├── test_auth.py            # 8 tests  — token fetch, cache hit/miss, expiry buffer
│       ├── test_api_client.py      # 28 tests — dummy modes, headers, retry, backoff, jitter
│       ├── test_idempotency.py     # 20 tests — all acquire branches, stale lock, update_status
│       ├── test_sqs_client.py      # 25 tests — drain, malformed routing, DLQ payload, delete
│       ├── test_s3_client.py       # 5 tests  — payload fetch, error propagation
│       └── test_main.py            # 31 tests — all handler paths and multi-message scenarios
├── requirements.txt                # Runtime dependencies
├── requirements-test.txt           # Test dependencies
└── pytest.ini                      # Test configuration
```

---

## Setup Instructions

### Prerequisites

| Tool | Minimum version |
|------|----------------|
| Python | 3.11 |
| pip | 23+ |
| AWS CLI | 2.x (for manual deployment) |
| AWS account | with Lambda, SQS, DynamoDB, S3 access |

### 1. Clone the repository

```bash
git clone <repo-url>
cd knowledge-management/event-consumer
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

### 3. Install runtime dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the template below into a `.env` file (or set them in your Lambda console / IaC tool):

```bash
# Required
IDEMPOTENCY_TABLE=event-idempotency
OAUTH_TOKEN_URL=https://auth.example.com/oauth/token
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
API_URL=https://api.example.com/events
QUEUE_URL=https://sqs.<region>.amazonaws.com/<account-id>/<queue-name>
DLQ_URL=https://sqs.<region>.amazonaws.com/<account-id>/<dlq-name>

# Optional — defaults shown
LOG_LEVEL=INFO
USE_DUMMY_API=false
DUMMY_API_MODE=success
MAX_RETRIES=5
BASE_DELAY=1
MAX_BACKOFF=30
HTTP_TIMEOUT=10
STALE_LOCK_TIMEOUT_SECONDS=900
IDEMPOTENCY_TTL_SECONDS=604800
```

### 5. AWS infrastructure requirements

| Resource | Configuration |
|----------|--------------|
| DynamoDB table | Partition key: `eventId` (String), Sort key: `appId` (String), TTL attribute: `expiresAt` |
| SQS work queue | VisibilityTimeout ≥ 900 s (matches `STALE_LOCK_TIMEOUT_SECONDS`) |
| SQS DLQ | Standard queue, attached to the work queue |
| S3 bucket | Stores event payloads as JSON objects |

---

## Running the Application

### Deploy to AWS Lambda

The entry point is `main.lambda_handler`. Package and deploy using your preferred method:

**AWS CLI (zip deployment)**

```bash
pip install -r requirements.txt -t package/
cp -r *.py middleware/ service/ package/
cd package && zip -r ../function.zip . && cd ..
aws lambda update-function-code \
    --function-name event-consumer \
    --zip-file fileb://function.zip
```

**Invoke manually**

```bash
aws lambda invoke \
    --function-name event-consumer \
    --payload '{}' \
    response.json
cat response.json
# {"processed": 3, "skipped": 0, "failed": 0}
```

### Run locally (dummy API mode)

Set `USE_DUMMY_API=true` to skip real HTTP calls during local development. All AWS calls still require real or mocked credentials.

```bash
USE_DUMMY_API=true DUMMY_API_MODE=success python -c "
from main import lambda_handler
print(lambda_handler({}, {}))
"
```

`DUMMY_API_MODE` options:

| Value | Behaviour |
|-------|-----------|
| `success` | Returns `200 {"message": "ok"}` |
| `client_error` | Returns `400 {"error": "bad request"}` |
| `server_error` | Returns `500 {"error": "internal error"}` |
| `timeout` | Raises `Exception("Simulated timeout")` |
| anything else | Raises `URLError` (simulated network failure) |

---

## Running Tests

### Install test dependencies

```bash
pip install -r requirements-test.txt
```

### Run all unit tests

```bash
pytest
```

### Run a specific test file

```bash
pytest tests/unit/test_main.py
```

### Run with verbose output and coverage report

```bash
pytest --cov=. --cov-report=term-missing
```

Example coverage output:

```
Name                          Stmts   Miss  Cover
-------------------------------------------------
config.py                        16      0   100%
main.py                          27      0   100%
middleware/idempotency.py        31      0   100%
middleware/s3_client.py           6      0   100%
middleware/sqs_client.py         40      1    98%
service/api_client.py            35      0   100%
service/auth.py                  19      0   100%
-------------------------------------------------
TOTAL                           174      1    99%
```

### Test structure

```
tests/
├── conftest.py                     # AWS region + app env vars set before any import
└── unit/
    ├── test_config.py              # 22 tests — required vars, missing var errors, type coercion, defaults
    ├── test_auth.py                # 8 tests  — token fetch, cache hit/miss, expiry buffer, missing expires_in
    ├── test_api_client.py          # 28 tests — dummy modes, bearer header, retry, 2xx/4xx/5xx, jitter/backoff cap
    ├── test_idempotency.py         # 20 tests — new event, completed, fresh/stale lock, missing item, update_status args
    ├── test_sqs_client.py          # 25 tests — batch drain, message parsing, malformed DLQ routing, send_to_dlq payload
    ├── test_s3_client.py           # 5 tests  — parsed JSON return, correct args, nested payload, error propagation
    └── test_main.py                # 31 tests — empty queue, success, both skip types, 4xx, exceptions, multi-message
```

**Total: 139 tests** across 7 test files.

Each test file covers exactly one source module — no shared logic between test files. All external dependencies (boto3 clients, urllib, time, random) are mocked using `unittest.mock.patch`. No real AWS credentials or network access required to run the suite.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IDEMPOTENCY_TABLE` | Yes | — | DynamoDB table name for idempotency records |
| `OAUTH_TOKEN_URL` | Yes | — | OAuth2 token endpoint URL |
| `CLIENT_ID` | Yes | — | OAuth2 client ID |
| `CLIENT_SECRET` | Yes | — | OAuth2 client secret |
| `API_URL` | Yes | — | External API endpoint (POST) |
| `QUEUE_URL` | Yes | — | SQS work queue URL |
| `DLQ_URL` | Yes | — | SQS dead letter queue URL |
| `LOG_LEVEL` | No | `INFO` | Python log level (uppercased automatically) |
| `USE_DUMMY_API` | No | `false` | Skip real API calls; use `DUMMY_API_MODE` response |
| `DUMMY_API_MODE` | No | `success` | Dummy response mode (see table above) |
| `MAX_RETRIES` | No | `5` | Max API retry attempts for 5xx / network errors |
| `BASE_DELAY` | No | `1` | Base backoff delay in seconds |
| `MAX_BACKOFF` | No | `30` | Backoff ceiling in seconds |
| `HTTP_TIMEOUT` | No | `10` | `urlopen` timeout in seconds (applies to both API and OAuth calls) |
| `STALE_LOCK_TIMEOUT_SECONDS` | No | `900` | Age in seconds at which a PROCESSING lock is considered stale |
| `IDEMPOTENCY_TTL_SECONDS` | No | `604800` | DynamoDB TTL for idempotency records (default 7 days) |

---

## API Documentation

The Lambda does not expose an HTTP endpoint. It is triggered by an EventBridge Scheduler or invoked directly.

### SQS message schema (work queue)

Each message body must be valid JSON with the following fields:

```json
{
  "tenantId":  "string",
  "eventId":   "string",
  "appId":     "string",
  "event":     "string",
  "s3Key":     "string",
  "s3Bucket":  "string"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `tenantId` | string | Tenant identifier |
| `eventId` | string | Unique event identifier — DynamoDB idempotency partition key |
| `appId` | string | Application identifier — DynamoDB idempotency sort key |
| `event` | string | Event type name (e.g. `UserCreated`) |
| `s3Key` | string | S3 object key for the event payload JSON |
| `s3Bucket` | string | S3 bucket name containing the payload |

Messages that fail JSON parsing or are missing any required field are routed to the DLQ and deleted from the work queue.

### S3 payload schema

The object at `s3Bucket/s3Key` must be a valid JSON object. Its contents are forwarded as-is to the external API via POST.

### External API contract

The external API receives a POST with:
- `Content-Type: application/json`
- `Authorization: Bearer <token>`
- Body: the S3 payload dict

Expected responses:

| Status | Behaviour |
|--------|-----------|
| `2xx` | Event marked `COMPLETED`, message deleted |
| `4xx` | Sent to DLQ immediately, no retry, message deleted |
| `5xx` / network error | Retried up to `MAX_RETRIES` with exponential backoff + jitter, then DLQ |

### Lambda return value

```json
{
  "processed": 3,
  "skipped":   1,
  "failed":    0
}
```

| Field | Meaning |
|-------|---------|
| `processed` | Successfully delivered to external API (2xx) |
| `skipped` | Already COMPLETED or currently in-flight on another processor |
| `failed` | Sent to DLQ (4xx client error, max retries exceeded, or unexpected exception) |

### DLQ message schemas

**Failed processing** (from `send_to_dlq`):

```json
{
  "eventId":    "string",
  "event":      "string",
  "status":     "FAILED",
  "attempts":   1,
  "createdAt":  "2024-01-01T00:00:00+00:00",
  "updatedAt":  "2024-01-01T00:00:01+00:00",
  "statusCode": 503,
  "error":      "ExternalAPIException: ...",
  "dlqReason":  "Max retry attempts (1) exceeded",
  "dlqAt":      "2024-01-01T00:00:01+00:00"
}
```

**Malformed SQS message** (from `drain_sqs`):

```json
{
  "originalMessage": "<raw message body string>",
  "failureReason":   "JSONDecodeError: ...",
  "timestamp":       1714900000
}
```

---

## Technology Choices and Rationale

### Python 3.11 + stdlib only (no frameworks)

Lambda cold starts are sensitive to package size. The entire production runtime uses only `boto3`/`botocore` (AWS SDK) plus Python's built-in `urllib`, `json`, `logging`, `time`, and `random`. No `requests`, `httpx`, or `aws-lambda-powertools` — this keeps the deployment package small and eliminates third-party CVE exposure.

### urllib over requests

`urllib` ships with Python. For a Lambda making one POST per invocation, the ergonomic gap vs `requests` doesn't justify an extra dependency. Timeout, headers, and error handling are explicit and auditable.

### Layered module structure (middleware / service)

Source code is split into two layers:
- `middleware/` — AWS infrastructure concerns (SQS, DynamoDB, S3)
- `service/` — external service concerns (OAuth2, API client)

This separation keeps each module focused, makes mocking straightforward in tests (patch at the boundary of the layer under test), and prevents `main.py` from importing AWS SDK calls directly.

### DynamoDB conditional write for idempotency

`put_item` with `attribute_not_exists(eventId) AND attribute_not_exists(appId)` is an atomic, server-side compare-and-set. This avoids the check-then-act race condition that a read-before-write approach would have. Four outcomes are handled explicitly:

| Result | Meaning |
|--------|---------|
| `True` | New event — claim acquired, proceed |
| `False` | Already `COMPLETED` — skip and delete |
| `None` | `PROCESSING` with fresh lock — skip, leave visible |
| `True` (after stale check) | `PROCESSING` lock expired — reacquire and retry |

### DynamoDB TTL on idempotency records

`expiresAt` (default 7 days) lets DynamoDB expire records automatically. This bounds table growth without a scheduled cleanup job.

### Exponential backoff with full jitter

`random.uniform(0, min(BASE_DELAY × 2^attempt, MAX_BACKOFF))` avoids thundering-herd re-collision when multiple Lambda instances hit the same 5xx response simultaneously. The ceiling is configurable via `MAX_BACKOFF`.

### Dead Letter Queue pattern

Rather than raising and relying on Lambda's built-in retry mechanism, the handler explicitly routes failures to a DLQ with an enriched envelope (status code, reason, timestamps). This makes failures observable and replayable without depending on Lambda's redrive configuration. The idempotency record is also updated to `FAILED` so any redrive attempt can be detected.

### OAuth2 in-memory token cache

The `_token_cache` dict lives at module level, surviving Lambda warm starts. A fresh token is only fetched when the cached one is within 60 seconds of expiry — typically once per cold start per hour — avoiding a round-trip to the token endpoint on every invocation.

### unittest.mock for unit tests

Each unit test file patches only at the boundary of the module under test (e.g. `patch.object(sqs_module, "sqs")` rather than patching `boto3.client`). This ensures tests are isolated from each other, fast (no network or AWS calls), and resilient to implementation changes in sibling modules.
