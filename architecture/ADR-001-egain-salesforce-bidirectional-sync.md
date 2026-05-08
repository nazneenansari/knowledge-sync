# ADR-001: eGain ↔ Salesforce Knowledge Integration — Bidirectional Sync

> **Project / System:** eGain ↔ Salesforce Service Cloud Integration  
> **Author:** Nazneen Ansari  
> **Date:** 2026-05-07

---

## Problem Statement

Two integration flows need to operate reliably at ~10,000 events/hour without either system becoming a bottleneck or point of failure for the other.

1. **Push — eGain to Salesforce:** eGain publishes knowledge articles that must be reflected in Salesforce Knowledge so service agents have access to current content without manual duplication. A synchronous push would couple eGain's publishing pipeline directly to Salesforce's API rate limits — a Salesforce outage would cascade into eGain.

2. **Pull — Salesforce to eGain:** When a Salesforce case is **closed**, the case data carries signals about content gaps — topics agents struggled to find answers for. Salesforce publishes a closed-case event and eGain must receive that data to identify missing or underperforming knowledge articles. A synchronous call on every case closure would expose eGain's ingestion pipeline to Salesforce's event volume with no buffer.

Both flows share the same failure mode: tight coupling between systems at scale. The architecture must absorb bursts, handle downstream failures gracefully, and guarantee at-least-once delivery in both directions.

---

## Assumptions

> *These assumptions bound the design. If any prove false, the architecture decisions below should be revisited.*

| # | Assumption | Impact if Wrong |
|---|---|---|
| 1 | Sync is **batch-oriented**, not real-time — minutes of lag in both directions is acceptable | If real-time is required, SQS batch polling must be replaced with an event-driven push (SNS fan-out or EventBridge) and the cost/latency trade-off re-evaluated |
| 2 | Peak traffic is **~10,000 events/hour** (~2.8 events/second sustained) across both flows | If volume grows 10×, Lambda concurrency limits and Salesforce API rate limit budgets must be re-sized; architecture pattern holds but parameters change |
| 3 | **eGain is the system of record** for knowledge articles — Salesforce Knowledge is a read replica; agents do not author or materially edit articles in Salesforce | If Salesforce becomes a co-authoring system, a true bidirectional sync with merge conflict resolution is required, significantly increasing complexity |
| 4 | Article payloads and case payloads may **exceed SQS's 256KB message size limit** due to rich HTML bodies, attachments, and metadata | If all payloads are consistently small (<50KB), S3 offloading is optional overhead; the pointer-in-SQS pattern still works but adds latency for no benefit |
| 5 | The integration runs on **AWS** — SQS, S3, DynamoDB, and Lambda are available and approved | If the target platform changes, equivalent managed services (e.g., Azure Service Bus + Blob Storage) would replace AWS-specific components |
| 6 | **Idempotency window** for DynamoDB deduplication is set to **24 hours** — duplicate messages older than that are treated as new events | If the same record can legitimately go unprocessed for >24 hours (e.g., extended outage + DLQ backlog), the TTL on the idempotency record must be extended |

---

## Key Architectural Decision

**Both flows use the same webhook-triggered, SQS-backed async pipeline with S3 payload offloading — the direction of the webhook and the identity of the consumer flip, but the pattern is identical.**

**Why:**
- API Gateway acts as the public HTTP entry point for webhooks in both directions — it terminates the inbound connection and hands off to Lambda, keeping the queue infrastructure private and never directly exposed
- Webhooks decouple the producing system from the consuming system — the producer fires and forgets; the pipeline absorbs the burst
- SQS acts as a durable buffer in both directions, smoothing 10k/hour spikes so neither Salesforce nor eGain is overwhelmed
- S3 payload offloading keeps SQS message size small (pointer only), preventing queue memory exhaustion for large article bodies or rich case records
- DynamoDB idempotency keys ensure that if a message is redelivered (SQS at-least-once), neither system receives duplicate writes or triggers duplicate analysis
- Exponential backoff + jitter on all third-party API calls, combined with a DLQ, ensures transient failures are retried safely without thundering herd

---

## Architecture Flow

```
 ─────────────────────────────────────────────────────────────────────────────
  FLOW 1 — PUSH                              FLOW 2 — PULL
  eGain → Salesforce Knowledge              Salesforce Closed Case → eGain
 ─────────────────────────────────────────────────────────────────────────────

  eGain CMS                                  Salesforce Service Cloud
      │ Publish / Update Event                    │ Case Closed Event
      ▼                                           ▼
  [API Gateway]                              [API Gateway]
  POST /webhook                              POST /webhook
      │                                           │
      ▼                                           ▼
  [Lambda — HMAC Validator]                  [Lambda — HMAC Validator]
  HMAC-SHA256 validation                     HMAC-SHA256 validation
  (secret from Secrets Manager)             (secret from Secrets Manager)
      │                                           │
      │ writes full payload                       │ writes full payload
      ▼                                           ▼
  [Amazon S3]                                [Amazon S3]
  article body, media refs, metadata         case fields, resolution, closure reason
      │ S3 object key (pointer only)             │ S3 object key (pointer only)
      ▼                                           ▼
  [SQS — Article Sync Queue]                 [SQS — Case Ingestion Queue]
      │                                           │
      │ CloudWatch alarm on queue depth           │ CloudWatch alarm on queue depth
      ▼                                           ▼
  [Article Sync Lambda]                      [Case Ingestion Lambda]
  ├── pulls full payload from S3             ├── pulls full payload from S3
  │   (SQS carries S3 key only)             │   (SQS carries S3 key only)
  ├── check DynamoDB idempotency             ├── check DynamoDB idempotency
  │   (article_id + egain_version)           │   (case_id + sf_version)
  ├── upsert → Salesforce Knowledge API      ├── call → eGain Gap Analysis API
  │   OAuth 2.0 · backoff + jitter           │   OAuth 2.0 · backoff + jitter
  │   max 5 retries                          │   max 5 retries
  └── max retries exceeded                   └── max retries exceeded
          │                                              │
          ▼                                              ▼
  [Article Sync DLQ]                         [Case Ingestion DLQ]
          │                                              │
          └──── ops replay (≤500 events) ───────────────┘
                back to respective main queue
```

---

## Data Synchronization Strategy

### Real-time vs. Batch
Both flows are **batch-oriented by design**. eGain article publishing and Salesforce case creation operate on human-driven cadences; sub-second propagation has no business value in either direction. Lambda consumers process messages in configurable batch sizes, translating event volume into steady, metered API calls.

### Conflict Resolution — Flow 1 (Push)
Each article payload carries an `egain_version` timestamp. The Lambda checks the DynamoDB idempotency record `{article_id, egain_version}` before writing — if already processed, the message is skipped.

**eGain is the system of record.** The conflict resolution strategy is **last write wins** — every eGain publish unconditionally overwrites the Salesforce record.

> **Note:** Checking `LastModifiedDate` on the Salesforce record before upsert to detect conflicting edits only applies if this integration evolves into a true bidirectional sync where agents can author articles in Salesforce. In the current model, eGain always wins and the pre-fetch check is unnecessary.

### Conflict Resolution — Flow 2 (Pull)
Case ingestion is **read and analyze only** — no data is written back to Salesforce. Only closed cases are processed; the closure event from Salesforce is the trigger, ensuring the full case lifecycle (description, resolution, closure reason) is available for gap analysis. Idempotency is enforced via `{case_id, sf_version}` in DynamoDB to prevent duplicate gap analysis runs on the same closed case.

### Data Consistency
- SQS provides at-least-once delivery in both directions; DynamoDB idempotency prevents duplicate processing
- Article status transitions (draft → published → archived) are included in every Flow 1 payload so Salesforce visibility always reflects eGain state
- Case payloads in Flow 2 include full field snapshots so gap analysis runs on a consistent point-in-time record, not a partial update

---

## Security & Compliance

### Authentication / Authorization
- **Inbound webhooks (both flows):** API Gateway receives all inbound webhook POST requests and forwards to Lambda; the Lambda validates the HMAC-SHA256 signature on every request using the shared secret stored in AWS Secrets Manager — requests with invalid or missing signatures are rejected before reaching SQS
- **Outbound to Salesforce (Flow 1):** OAuth 2.0 Client Credentials flow (server-to-server); access token cached in memory with TTL refresh, never stored in code or config files
- **Outbound to eGain (Flow 2):** OAuth 2.0 Client Credentials flow; same token caching pattern
- **Internal AWS:** IAM roles with least privilege per Lambda — the Article Sync Lambda role covers only its own queue, bucket prefix, and DynamoDB table; the Case Ingestion Lambda role covers only its own equivalent resources; no shared execution role between flows

### Data Encryption
- **In transit:** TLS 1.2+ enforced on all endpoints — webhook receivers, S3 presigned URLs, Salesforce API calls, eGain API calls
- **At rest:** S3 bucket uses SSE-KMS with a customer-managed key; SQS queues use SSE-SQS; DynamoDB encryption at rest enabled by default

### Audit Logging
- Every webhook receipt, SQS enqueue, API call (success or failure), and DLQ event is written to CloudWatch Logs with structured JSON: `{timestamp, record_id, version, flow, action, status, latency_ms}`
- CloudTrail captures all S3 and DynamoDB access for compliance audit trail
- Failed events in either DLQ are never deleted without a logged replay or explicit discard decision

---

## Scalability & Reliability

### Auto-scaling (Both Flows)
A CloudWatch alarm monitors the `ApproximateNumberOfMessagesVisible` metric on each queue independently. When depth crosses a threshold (e.g., >500 messages), the alarm triggers the corresponding Lambda immediately — no waiting for a scheduled polling interval. Lambda concurrency scales out in response, and scales back in once the queue drains to zero.

### Handling API Rate Limits
- **Flow 1 (Salesforce):** The Lambda inspects the `Sforce-Limit-Info` response header and backs off proactively when remaining API calls drop below a safety threshold (e.g., <10% remaining)
- **Flow 2 (eGain):** eGain's internal API rate limits are governed by the same backoff + jitter strategy; thresholds set based on eGain's documented ingestion capacity

### Retry & Circuit Breaker Strategy
- **Per-message retry:** exponential backoff with jitter — `base_delay * 2^attempt + random_jitter_ms` — capped at 5 attempts before the message moves to the DLQ
- **Circuit breaker:** if the downstream API error rate exceeds 50% over a 60-second window, the Lambda stops polling SQS and enters a half-open wait state (30 seconds), then retries a single probe call before resuming full polling
- **DLQ replay:** ops team can trigger replay of up to 500 DLQ messages at a time back to the main queue, at a reduced concurrency cap to avoid re-triggering rate limits

### Integration Health Monitoring
- CloudWatch dashboard per flow: queue depth, DLQ depth, API call success rate, average processing latency, Lambda concurrency scaling events
- Alarms: DLQ depth > 0 (PagerDuty alert), queue depth growing faster than drain rate for >10 minutes, OAuth token refresh failures

---

## Trade-offs Considered

| What I Gained | What I Gave Up |
|---|---|
| Resilience — failures in one system don't cascade into the other | Eventual consistency — both flows introduce processing lag of seconds to minutes |
| Rate limit safety — SQS buffers burst traffic in both directions | Operational complexity — two independent pipelines (queues, Lambdas, DLQs, DynamoDB tables) to own and monitor |
| Idempotency via DynamoDB — safe replay without duplicate writes or analysis | Cost — S3 storage, DynamoDB reads/writes, SQS requests across two flows at 10k/hour scale |
| Independent scaling per flow — case ingestion and article sync scale separately | Payload retrieval latency — every Lambda must fetch the S3 object before processing (~20–50ms per message) |
| Shared architecture pattern — one pattern to understand, operate, and debug across both directions | Two DLQ replay processes to manage — a failure in one flow does not automatically surface the other |

---

## What I'd Do Differently With More Time

- **Evaluate SQS FIFO for per-record ordering** — SQS Standard was chosen for throughput, accepting that two rapid updates to the same article or case could arrive out of order. A FIFO queue using message group IDs per record eliminates that edge case, but at reduced throughput and higher cost — worth a deliberate decision rather than an assumption.
- **Automate DLQ replay with health guardrails** — DLQ replay is currently a manual ops trigger. Automating it with a circuit check — replay only if the downstream API error rate is below 5% for the past 15 minutes — avoids replaying into a still-degraded system.
