# PostgreSQL rollback runbook

This is a controlled rollback, not a bidirectional synchronization design.

1. Declare the rollback and route traffic to the last known-good immutable
   application task definition. If the fault is data-only and the current
   binary is compatible, keep traffic drained until dataset activation finishes.
2. Stop pipeline workers, schedules and every mutation endpoint. Confirm no
   queued/running/retrying writers and retain the failed target unchanged for
   investigation.
3. Record current task definitions, schema revision, active dataset, database
   backup identifier, counts, checksums, reconciliation output and error traces.
4. Select only a retained `approved` or `rolled_back` PostgreSQL dataset with an
   active or reproducible dashboard materialization. Never select an unknown or
   rejected candidate.
5. Execute the transactional local form only after resolving the exact target:

   ```bash
   DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 \
   DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app \
   DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder \
   PYTHONPATH=src venv/bin/python -m migration.sqlite_to_postgres \
     rollback --dataset-version EXACT_APPROVED_DATASET_VERSION
   ```

6. Run readiness, release, FK/orphan, reconciliation, golden and authenticated
   journey smoke tests. Resume traffic, then writers, only after they pass.
7. If logical dataset activation cannot recover the service, restore the prior
   PostgreSQL backup into a separate database, validate it, and promote it by
   the approved database recovery procedure. Never overwrite the failed target
   before evidence is retained.
8. Do not replay changes in both directions unless an approved conflict policy,
   ownership rule, idempotency model and reconciliation design exists. No such
   policy is currently approved.

The local proof uses `scripts/verify_local_rollback.py`. It refuses active jobs,
verifies counts/materialization on the prior version and restores the original
version in a `finally` path if an intermediate assertion fails.

AWS traffic changes, ECS rollbacks, RDS restores and production execution are
`NOT TESTED` and require explicit approval.
