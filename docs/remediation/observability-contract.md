# Observability Contract

## Health

`GET /health/live` checks process viability only. `GET /health/ready` uses an
independent, no-pool PostgreSQL connection so exhausted analytical connections
cannot block the probe. Readiness requires:

- PostgreSQL query success;
- exact expected Alembic revision;
- one approved active dataset;
- synchronized source and retention configuration;
- durable outbox/queue contract availability.

The public response contains check states only, not database identifiers or
connection details. The legacy `/health` route remains a liveness alias.

## Structured logs

Every application log line is one redacted JSON object containing timestamp,
level, service, environment and message. Request completion adds request ID,
trace ID, pseudonymous actor ID, role, route-template operation, duration,
status and error class. Worker events add job/dataset IDs, operation, counts
and retry state where available.

The formatter excludes raw exception payloads and applies the Phase-9
recursive redaction policy to structured fields. Request paths use route
templates to avoid high-cardinality IDs.

## Metrics and alarms

`config/observability.json` is the executable definition. It covers API
latency/errors, DB pool, query latency, cache hit rate, dataset/source age,
pipeline duration/failures/counts, conversion gaps, programme/geography
coverage, queue age, DLQ, worker failures, task restarts and estimated cost.

Alarm definitions cover readiness, 5xx/latency budgets, stale data, pipeline
failure, reconciliation, DLQ/backlog, gap/coverage regression, cost and RDS
CPU/connections/storage. Baseline coverage/gap thresholds derive from the
locally reconciled dataset; the USD 500 cost threshold is a proposed fail-safe,
not an approved budget. Owners must approve production thresholds.

`/api/admin/observability/metrics` is administrator-only and returns definitions
plus bounded local-process evidence. Terraform maps the same definitions to
CloudWatch later. No CloudWatch metric, dashboard, alarm or notification has
been created or tested in AWS.
