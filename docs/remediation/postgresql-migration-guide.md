# PostgreSQL setup and migration guide

The schema is managed exclusively by Alembic. PostgreSQL holds versioned source
facts, materialized analytics, durable jobs/outbox, audit events, governance and
observability controls. One partial unique constraint permits exactly one active
dataset while prior approved datasets remain available for rollback.

## Empty schema

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker-compose up -d postgres
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 \
DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app \
DATABASE_PASSWORD_FILE="$POSTGRES_PASSWORD_FILE" \
venv/bin/alembic upgrade head
```

## Deterministic full source migration

Keep the source immutable and invoke the migration with a reviewed output
directory outside `docs/audits/`:

```bash
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 \
DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app \
DATABASE_PASSWORD_FILE="$POSTGRES_PASSWORD_FILE" \
PYTHONPATH=src venv/bin/python -m migration.sqlite_to_postgres migrate \
  --source src/data/charities.db \
  --output-dir /private/tmp/fip-migration-evidence \
  --enforce-baseline
```

The command preflights disk/capacity and the read-only source, stages a candidate
dataset, preserves duplicate cohorts and anomalies, validates every declared FK,
builds materializations, reconciles counts/controls, and activates only a fully
passing candidate. It is idempotent by source checksum and code revision.

Post-migration gates:

```bash
PYTHONPATH=src venv/bin/python scripts/verify_transition.py
PYTHONPATH=src venv/bin/python -m migration.release_gate
```

Use `rollback-runbook.md` for exact rollback boundaries. Never replay writes in
both directions without an approved conflict policy. Never run `terraform apply`
or an AWS migration from this local guide.
