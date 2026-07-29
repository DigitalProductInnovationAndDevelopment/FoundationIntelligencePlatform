# Phase 8 Durable Pipeline Evidence

## Result

Gate 8 **passes for local code readiness**. PostgreSQL owns job coordination,
request idempotency, worker leases, outbox delivery state, source-ingestion
state and immutable manifest/object descriptors. The API only enqueues work.

Real AWS S3, SQS, DLQ, EventBridge and Step Functions execution is **not
tested** and is not claimed. Those actions require a separately approved
deployment.

## Verified locally

- Alembic `0005_durable_pipeline` upgrades, downgrades to `0004` and upgrades
  again without changing the active dataset.
- A queued job has a versioned, payload-minimal dispatch envelope; repeated
  enqueue returns the same job ID.
- Workers claim through PostgreSQL row locking, refresh leases/heartbeats and
  transition through retry, success and dead-letter states.
- Staging/production request idempotency uses PostgreSQL rather than a local
  process dictionary.
- Raw storage descriptors and ingestion manifests reject mutation; corrections
  require a new version.
- Failure/retry testing leaves `sqlite-v7-8fc0cce61c81-r2` active.
- All eight source configurations are disabled and blocked while their legal
  and licence states remain honestly unresolved.
- Manual refresh remains operator-only, idempotent, queued and job-ID based;
  logs are redacted before both text and structured event output.

## Tests

The normal backend run passes 318 tests with ten explicit live-environment
skips and eight route subtests. The dedicated real PostgreSQL Phase-8 suite
passes 8/8. The combined Phase-8/application/schema run passes 18/18. Python
compile, blocking Flake8 and whitespace checks pass.

No dependency download, AWS call, live scraper/model/news call, paid API,
upload or push occurred.
