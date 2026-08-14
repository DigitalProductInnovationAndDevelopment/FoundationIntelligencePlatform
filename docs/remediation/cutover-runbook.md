# PostgreSQL cutover runbook

This runbook is prepared but has not been executed in AWS or production.
Every production or AWS step requires a new explicit approval.

## Preconditions and stop conditions

- Approved change window, incident commander, data owner and rollback owner.
- Reviewed immutable application image digests and exact Terraform plan.
- Current full backup with checksum and a successful isolated restore proof.
- Current SQLite source checksum, source counts and frozen source snapshot.
- Alembic, reconciliation, golden, security, load, E2E and release gates green.
- PostgreSQL previous dataset and previous ECS task definitions retained.
- Stop if writers cannot be frozen, checksums drift, the final delta is not
  coherent, reconciliation differs, readiness fails or approvals are absent.

## Procedure

1. Announce the window and freeze every SQLite writer, pipeline schedule,
   worker, manual refresh and enrichment action. Confirm no queued/running job.
2. Create the final coherent SQLite backup with the application stopped. Record
   byte size, SHA-256, schema version, source watermark and row counts. Preserve
   the source snapshot read-only; never overwrite it.
3. Create a final PostgreSQL backup/restore point and verify the checksum and
   restore instructions before changing the active dataset.
4. Load only the deterministic final SQLite delta into a new PostgreSQL dataset
   version. Never update the currently active version in place.
5. Run FK/orphan checks, counts, totals, duplicate controls, conversion gaps,
   Golden Fixtures and `scripts/verify_transition.py`. Any unexplained
   difference is a stop condition.
6. Activate the approved PostgreSQL candidate transactionally. Retain the prior
   dataset as `approved`/`rolled_back`; do not delete it.
7. Run `python -m migration.release_gate`. Only after it passes, switch the
   application to `DATA_RUNTIME_MODE=postgresql`. Do not configure SQLite as a
   fallback.
8. Run authenticated smoke tests for liveness/readiness, dashboard, map,
   relationships, filters, registry, profiles, grants, Sankey, score, pipeline
   status and operator-only manual refresh.
9. Observe API latency/errors, readiness, pool use, reconciliation, conversion
   gaps, coverage, queue/DLQ, worker failures and cost alarms for the approved
   monitoring window.
10. Preserve the SQLite source snapshot and all cutover evidence. Resume writers
    only after reconciliation and monitoring acceptance.

## Rollback triggers

Rollback on readiness failure, auth/RBAC regression, unexplained semantic
difference, reconciliation failure, materialization absence, persistent error
or latency budget breach, writer inconsistency, or explicit incident-command
decision. Use `rollback-runbook.md`; do not improvise bidirectional replay.

## Local evidence

The local proof on 2026-07-29 compared 18 semantic projections with zero
differences, switched from `sqlite-v7-8fc0cce61c81-r2` to the approved prior
dataset and back, and fully restored a 247,509,368-byte logical archive into an
isolated database. No traffic, AWS resource or production mode was changed.
