# ADR-002: Stream Real-Time Article Usage Analytics to eGain

> **Project / System:** eGain ↔ Salesforce Service Cloud Integration — Analytics Streaming  
> **Author:** Nazneen Ansari  
> **Date:** 2026-05-07

---

## Problem Statement

Salesforce tracks how service agents interact with knowledge articles — views, clicks, search hits, ratings, and time-on-article. eGain needs this usage data streamed back in near real-time to power article performance dashboards, surface underperforming content, and inform editorial decisions. At ~50,000 events/hour (~13.9 events/second sustained), a batch approach introduces lag that makes the analytics stale by the time authors act on them. A purely synchronous push per event would create a tightly coupled, brittle pipeline with no tolerance for eGain API slowdowns.

---

## Assumptions

> *These assumptions bound the design. If any prove false, the architecture decisions below should be revisited.*

| # | Assumption | Impact if Wrong |
|---|---|---|
| 1 | **Near real-time** delivery is sufficient — 5–15 second lag between a usage event in Salesforce and visibility in eGain is acceptable | If sub-second latency is required, the architecture must shift to Kinesis Data Streams → Lambda → DynamoDB + OpenSearch (always-on infrastructure, ~8–10× higher cost) |
| 2 | Peak traffic is **~50,000 events/hour** (~13.9 events/second sustained) | If volume grows beyond ~700,000 events/hour (~200 events/sec), shard count must be re-evaluated; 1 shard handles up to ~2,000 events/sec at 500 bytes/event |
| 3 | Average **event payload size is ≤ 500 bytes** — article ID, user ID, session ID, event type, timestamp, metadata | If rich payloads (e.g., full article snapshots attached to events) push size beyond 1MB, Kinesis's hard per-record limit is breached and the architecture must pre-strip payloads |
| 4 | Analytics events are **append-only** — there is no update or delete operation on a usage event once recorded | If corrections or deletions are required (e.g., GDPR erasure of user activity), a separate deletion propagation mechanism must be added |
| 5 | The integration runs on **AWS** — Kinesis Data Streams, Kinesis Firehose, Lambda, S3, and DynamoDB are available and approved | If the target platform changes, equivalent managed services (e.g., Azure Event Hubs + Azure Functions) would replace AWS-specific components |
| 6 | **Idempotency window** for DynamoDB deduplication is set to **24 hours** — duplicate events older than that are treated as new | If Kinesis retry or DLQ replay pushes the same event beyond the 24-hour window, duplicate analytics entries may appear in eGain |

---

## Key Architectural Decision

**The design uses Kinesis Data Streams as the streaming backbone — not SQS — with Lambda reading via Enhanced Fan-Out to push analytics to eGain in near real-time, and Kinesis Firehose delivering an S3 archive in parallel.**

SQS is a task queue — built for processing and deleting discrete jobs. Kinesis is a stream — built for ordered, replayable, multi-consumer event delivery. For analytics at 50k/hour where events must be delivered to eGain in seconds and archived to S3 simultaneously, Kinesis is the correct primitive. SQS would require a separate fan-out mechanism (SNS + multiple queues) to achieve the same multi-consumer delivery that Kinesis provides natively.

**Why:**
- Kinesis Data Streams decouples Salesforce event production from eGain consumption — Salesforce writes to the stream; the stream absorbs bursts
- Enhanced Fan-Out gives Lambda and Firehose each a dedicated 2 MB/sec read pipe per shard — no read bandwidth competition between consumers
- Lambda reads in batches with a 5–10 second batch window, accumulating records before pushing to eGain's API — reducing API call count and delivering near real-time without per-event overhead
- Kinesis Firehose delivers a parallel S3 archive automatically — no separate consumer needed for storage
- DynamoDB idempotency keys on `event_id` prevent duplicate analytics entries if Kinesis retries or DLQ replay re-delivers an event
- Exponential backoff + jitter on eGain API calls, combined with an SQS DLQ on Lambda failure, ensures transient failures are retried safely

---

## Architecture Flow

```
 Salesforce Service Cloud
 (Article view / click / search / rating event)
         │
         │  POST /events
         ▼
 [API Gateway]
         │
         ▼
 [Kinesis Data Streams — 1 Shard]
 (50k/hr = ~14 events/sec · well within 1,000 records/sec shard limit)
         │
         │  Enhanced Fan-Out (dedicated 2MB/sec per consumer)
         ├─────────────────────────────────────────────────────────┐
         ▼                                                         ▼
 [Lambda — Analytics Push]                            [Kinesis Firehose]
 Batch window: 5–10s                                  S3 archive
 Batch size: 100–500 records                          (partitioned by year/month/day/hr)
         │
         ├── check DynamoDB idempotency (event_id)
         ├── bulk POST → eGain Analytics API
         │   OAuth 2.0 · exponential backoff + jitter
         │   max 5 retries
         └── on max retries exceeded
                     │
                     ▼
             [SQS Dead Letter Queue]
                     │
                     └── ops replay (≤ 500 events)
                         back to Kinesis Data Streams
```

**Shard sizing:**
- 50,000 events/hour ÷ 3,600 = ~13.9 events/sec
- At 500 bytes/event = ~6.9 KB/sec write throughput
- 1 shard limit: 1 MB/sec write, 1,000 records/sec
- **1 shard with 30% headroom is sufficient** — re-evaluate if volume approaches 700k/hour

---

## Data Synchronization Strategy

### Real-time vs. Near Real-Time
This flow is **near real-time by design**. Lambda's batch window (5–10 seconds) accumulates events before pushing to eGain, reducing API call volume while keeping delivery latency well within editorial feedback loops. Sub-second delivery is not required — authors reviewing article performance act on data over hours, not milliseconds.

### Conflict Resolution
Analytics events are **append-only**. There are no updates or deletes — each event (view, click, rating) is a discrete immutable fact. Conflict resolution does not apply. Idempotency via DynamoDB on `event_id` ensures the same event is never counted twice in eGain, regardless of Kinesis retry or DLQ replay.

### Data Consistency
- Kinesis provides **ordered, at-least-once delivery** within a shard — events for the same article arrive in sequence
- DynamoDB idempotency prevents duplicate counts in eGain
- Kinesis Firehose delivers the raw event archive to S3 independently — if the Lambda → eGain push fails, the raw data is preserved in S3 for replay or audit
- Kinesis default retention is 24 hours (extendable to 365 days) — events can be replayed from the stream itself if eGain is unavailable for an extended period

---

## Security & Compliance

### Authentication / Authorization
- **Inbound from Salesforce:** Salesforce delivers usage events via webhook over HTTP API; each incoming request is validated using HMAC-SHA256 signature verification — the shared secret is stored in AWS Secrets Manager; requests with invalid or missing signatures are rejected before reaching Kinesis
- **Outbound to eGain:** OAuth 2.0 Client Credentials flow (server-to-server); access token cached in Lambda memory with TTL refresh, never stored in code or config files
- **Internal AWS:** IAM roles with least privilege — the Lambda execution role covers only `kinesis:GetRecords`, `dynamodb:GetItem / PutItem` on the idempotency table, and `sqs:SendMessage` to the DLQ; Firehose role covers only `s3:PutObject` on the archive bucket

### Data Encryption
- **In transit:** TLS 1.2+ enforced on all endpoints — API Gateway, eGain API calls, S3 presigned URLs
- **At rest:** Kinesis Data Streams encrypted with SSE-KMS; S3 archive uses SSE-KMS with a customer-managed key; DynamoDB encryption at rest enabled by default

### Audit Logging
- Every API Gateway request, Kinesis record ingestion, Lambda invocation (success or failure), and DLQ event is written to CloudWatch Logs with structured JSON: `{timestamp, event_id, article_id, event_type, action, status, latency_ms}`
- CloudTrail captures all Kinesis, S3, and DynamoDB access for compliance audit trail
- Failed events in the DLQ are never deleted without a logged replay or explicit discard decision

---

## Scalability & Reliability

### Kinesis Shard-Based Scaling
Each shard handles up to 1,000 records/sec write and 2 MB/sec read. At 50k/hour, 1 shard is sufficient with headroom. A CloudWatch alarm on `IncomingRecords` monitors write throughput — if sustained throughput approaches 70% of shard capacity, an alarm triggers a shard split (scaling out). Kinesis Enhanced Fan-Out ensures Lambda and Firehose each retain their dedicated 2 MB/sec read bandwidth regardless of shard count changes.

### Retry & Circuit Breaker Strategy
- **Per-batch retry:** exponential backoff with jitter — `base_delay * 2^attempt + random_jitter_ms` — capped at 5 attempts
- **Circuit breaker:** if eGain API error rate exceeds 50% over a 60-second window, Lambda stops processing and enters a half-open wait state (30 seconds), then retries a single probe call before resuming full throughput
- **DLQ replay:** ops team can trigger replay of up to 500 DLQ messages at a time, re-ingested into Kinesis Data Streams at a throttled rate to avoid overwhelming eGain

### Integration Health Monitoring
- CloudWatch dashboard: Kinesis `IncomingRecords`, `GetRecords.IteratorAgeMilliseconds` (stream lag), Lambda invocation success rate, eGain API call latency, DLQ depth
- Alarms: DLQ depth > 0 (PagerDuty alert), iterator age > 30 seconds (Lambda falling behind the stream), eGain OAuth token refresh failures
- `IteratorAgeMilliseconds` is the key health signal — if it grows, Lambda is not keeping up with the stream and concurrency or batch size must be tuned

---

## Trade-offs Considered

| What I Gained | What I Gave Up |
|---|---|
| Near real-time delivery (5–15s) — authors see usage data within seconds of agent interaction | Cost — Kinesis shards, Enhanced Fan-Out, and Lambda invocations are more expensive than a batch SQS pipeline (~$200–400/mo vs ~$80–250/mo) |
| Multi-consumer fan-out — Lambda and Firehose read the same stream simultaneously with no coordination | Kinesis operational complexity — shard management, iterator age monitoring, and Enhanced Fan-Out registration add ops surface area vs. SQS simplicity |
| Ordered delivery within a shard — events for the same article arrive in sequence | Single shard = single ordered partition — horizontal scale requires shard splits and partition key design upfront |
| Replayable stream — Kinesis retains events for 24 hours (up to 365); replay is native without a separate DLQ | 24-hour default retention window — if eGain is down for longer, events must be replayed from S3 archive, not Kinesis directly |
| S3 archive via Firehose — raw events preserved independently of eGain delivery success | Firehose buffer delay — S3 archive lags by ~60 seconds; not suitable if the archive itself needs near-real-time query access |

---

## What I'd Do Differently With More Time

- **Automate DLQ replay with health guardrails** — DLQ replay is currently a manual ops trigger. Automating it with a circuit check — replay only if eGain API error rate is below 5% for the past 15 minutes — avoids replaying into a still-degraded system.
- **Define partition key strategy explicitly** — Using `article_id` as the Kinesis partition key ensures all events for the same article land on the same shard and arrive in order. This was assumed but should be formalised before go-live to avoid ordering surprises at scale.
