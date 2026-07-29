# Observability Runbooks

Status: local code-readiness procedures. AWS/CloudWatch execution is not
tested. Any production command must follow environment approval and change
control; examples here are decision steps, not authorization.

## readiness-failure

Signals: `/health/ready` returns 503 or the readiness alarm fires.

1. Read the five non-sensitive checks: PostgreSQL, schema version, active
   dataset, critical configuration and queue contract.
2. Correlate request/trace IDs in structured logs; do not log connection
   strings or credentials.
3. If schema mismatches, stop rollout and follow `migration-failure`.
4. If no active dataset exists, stop writers and follow `stale-dataset` or
   `rollback` using an approved version.
5. If PostgreSQL is unavailable, follow `rds-outage`. If queue state is
   unavailable, stop manual/scheduled triggers and follow `queue-backlog`.

## migration-failure

1. Stop application promotion and pipeline writers; retain the failed target.
2. Record Alembic revision, migration run ID, dataset version and error class.
3. Verify source checksum and last approved dataset remain unchanged.
4. Use the revision's tested downgrade only when the migration owner approves;
   do not improvise DDL or modify Alembic state directly.
5. Re-run empty-database migration, reconciliation and restore tests before a
   new attempt.

## ingestion-failure

1. Pause the affected source schedule and preserve its last-good dataset.
2. Inspect job/ingestion events, immutable manifest, checksum, watermark and
   accepted/rejected/quarantined counts.
3. Classify retryability; allow only bounded configured retries.
4. Dead-letter exhausted work, correct through a new source/object version and
   reconcile before activation.
5. Do not call paid/news/model sources during incident diagnosis without
   separate approval.

## stale-dataset

1. Confirm dataset age and per-source freshness, accounting for paused or
   governance-blocked schedules.
2. Compare conversion gaps and programme/geography coverage with the last
   approved snapshot; never lower thresholds merely to silence an alarm.
3. Trigger only an authorised, idempotent queued refresh.
4. Keep the last-good dataset active on failure and communicate known coverage
   gaps.

## rds-outage

1. Fail readiness and stop write/pipeline traffic while liveness remains up.
2. Check RDS health, CPU, connections, free storage, maintenance and network
   path using approved read-only operational access.
3. Do not expose connection strings or rotate credentials as a speculative
   fix.
4. If restore/failover is approved, verify checksum/revision/active dataset and
   run reconciliation before reopening traffic.

## queue-backlog

1. Check oldest-message age, pending outbox rows, worker heartbeat age and
   worker failure/restart rates.
2. Pause schedules/manual triggers if age continues to rise.
3. Verify worker capacity, lease expiry, poison-message repetition and database
   health before scaling.
4. Preserve idempotency keys; never duplicate-publish by manually rewriting
   outbox status.

## dlq-replay

1. Classify every DLQ message and record its original job ID/attempt history.
2. Fix the root cause and verify the handler against a local/recorded fixture.
3. Replay only through an approved tool that preserves job identity and
   deduplication; cap the batch and monitor failures.
4. Stop immediately on repeated failure and retain the message/evidence.

## bad-deployment

1. Stop promotion when 5xx, latency, readiness, E2E or reconciliation budgets
   fail.
2. Compare immutable image digest, configuration revision, schema and frontend
   artifact with the last approved release.
3. Follow `rollback`; do not combine a rollback with an unreviewed migration.
4. Re-run smoke, security, E2E, load and rollback-readiness gates before retry.

## rollback

1. Route traffic to the last approved immutable application artifacts.
2. Stop incompatible writers and retain the failed target for diagnosis.
3. Reactivate only an approved PostgreSQL dataset/materialization version.
4. Reconcile totals, identities, filters and critical journeys before traffic
   resumes. Do not perform bidirectional replay without a conflict policy.

## restore

1. Identify the approved backup/PITR point and source checksum.
2. Restore into isolation; never overwrite the only working environment.
3. Verify encryption, database integrity, Alembic revision, FK/check constraints,
   active dataset and reconciliation.
4. Persist append-only restore evidence. Restore success does not itself approve
   deletion or cutover.

## security-incident

1. Preserve evidence, restrict access and notify the assigned security/privacy
   owners; currently unassigned ownership is an escalation blocker.
2. Revoke affected sessions/credentials through approved systems and block
   suspect routes/sources at the edge.
3. Search only redacted logs by request/trace/job IDs. Do not copy raw personal
   payloads into tickets or chat.
4. Apply incident holds before any retention action and document scope/timing.

## credential-rotation

1. Identify consumers and secret reference, not secret value.
2. Create a new version in the approved secret manager, update consumers and
   verify readiness without printing credentials.
3. Revoke the prior version after all healthy tasks use the new version.
4. Review audit logs and repository scans; never commit `.env` or static AWS
   keys.

## cost-spike

1. Break down RDS, NAT, Fargate, CloudWatch, S3, Athena, CloudFront, WAF and
   transfer costs by environment/tag.
2. Check runaway schedules, log cardinality/retention, query scans, NAT routing
   and task scaling.
3. Pause non-critical schedules safely; preserve last-good data and evidence.
4. Any capacity reduction must retain readiness, backup/PITR and performance
   budgets.
