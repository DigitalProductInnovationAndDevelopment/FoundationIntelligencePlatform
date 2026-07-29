# Durable Pipeline and Storage Contract

Status: implemented locally by Alembic revision `0005_durable_pipeline`.
No AWS queue, object-store or scheduler operation has been executed.

## Coordination boundary

Production API requests create a `job_runs` record, its first `job_events`
entry and one `job_dispatch_outbox` envelope in the same PostgreSQL
transaction. The API never starts a scraper or subprocess. Request-level
idempotency is also PostgreSQL-backed in staging/production, so horizontally
scaled API tasks do not depend on process-local locks or dictionaries.

Workers claim one queued job with `FOR UPDATE SKIP LOCKED`, publish heartbeats
and bounded leases, and record success, retry, timeout, failure or dead-letter
events. A failed worker cannot activate a candidate dataset: every job and
ingestion run records the dataset that was active at enqueue/start time as its
last-good version. Retry and lease-reaping transitions leave that version
active.

The transactional outbox is the SQS delivery contract. Its versioned envelope
contains only job identity, type, queue, request time and bounded attempt
metadata; the worker obtains job input from PostgreSQL. Publishing is
idempotently keyed by job ID. AWS delivery remains unexecuted until separately
approved.

## Object zones and immutability

`storage_objects` models the `raw`, `validated`, `curated` and `export` bucket
aliases, object keys, provider version IDs, SHA-256 checksums, byte length,
content type and source/run ownership. Raw objects must be immutable. Database
triggers reject update or deletion of immutable object descriptors.
Corrections therefore create new object versions and new ingestion runs.

`ingestion_run_manifests` is append-only and records:

- source, source version, dataset and manifest schema version;
- raw, validated and curated object identities;
- before/after watermarks;
- source, accepted, rejected and quarantined record counts;
- retry count and a canonical SHA-256 manifest checksum.

The Python object-store protocol is intentionally SDK-neutral. Local tests use
an in-memory immutable substitute. Terraform supplies encrypted/versioned S3
zones later; this phase does not claim an S3 upload.

## Source schedules and governance

`config/source-pipelines.json` defines bounded schedules and limits for
360Giving, Charity Commission, Philea, Hinchilla, ECB, Google News RSS,
bounded article content and the optional Anthropic summary integration. Each
entry includes owners, legal/licence status, terms URL, rate limit, user agent,
freshness SLA, schedule, enabled state, watermark, classification, retention
class, schema version, credential reference, retry policy, timeout and record/
page limits.

Unknown approval is represented as `unresolved`, never inferred. All eight
sources are disabled and governance-blocked. Both the configuration validator
and PostgreSQL check constraint reject an enabled schedule unless legal and
licence states are `approved` and the governance block has been removed.
Enabling a blocked schedule through the administrator endpoint returns a
conflict. Paid AI/news access remains separately approval-gated.

## Local gate

- `0005 -> 0004 -> 0005` migration cycle passes transactionally.
- Seven local contract tests and one real PostgreSQL integration test pass.
- The combined Phase-8/application/schema PostgreSQL run passes 18 tests.
- The normal backend suite passes 318 tests, skips ten explicit live tests and
  passes eight route subtests.
- The catalog reports 40 application tables, 49 foreign keys and 161 checks.
- Exactly eight source configurations exist; zero are enabled and eight are
  governance-blocked.
- The active dataset remains `sqlite-v7-8fc0cce61c81-r2`.
