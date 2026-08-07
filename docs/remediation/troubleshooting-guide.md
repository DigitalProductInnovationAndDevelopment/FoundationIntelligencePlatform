# Troubleshooting guide

## Readiness is unavailable

Run liveness first, then inspect the independent readiness checks. Verify the
database secret file, host/port, Alembic revision, exactly one active dataset,
source/governance configuration and durable outbox tables. Do not relax
readiness to route traffic around a missing schema or dataset.

## Migration refuses the schema

Run `alembic current` and `alembic heads`. The migration gate reads the expected
revision from the versioned observability configuration. Upgrade with Alembic;
never edit `alembic_version` manually.

## Shadow differences appear

Preserve the evidence, identify the JSON paths, reproduce with
`scripts/verify_transition.py`, and classify the cause. Only versioned
allowlisted set ordering may be ignored. Do not add broad tolerances, remove
fields or switch responses to SQLite.

## Rollback cannot select a target

The target must exist with `approved` or `rolled_back` status and a valid
materialization. Retain the failed target, freeze writers and follow the
rollback runbook. Never force status flags in SQL.

## Docker build/start fails

Use `docker build --pull=false` only when pinned base images are present. The
runtime image intentionally contains no SQLite database or raw data. Compose
requires a real local password file and explicitly selects PostgreSQL.

## Terraform validation is unavailable

Do not claim success. Terraform/provider locks and scanners are not locally
available from approved sources. Use the offline validator, record `NOT TESTED`,
and later run the exact non-destructive commands in `terraform-validation.md`.
Never run apply/destroy to diagnose validation.

## GitHub/AWS workflow cannot run

Branch protection, environments, reviewers, OIDC trust and action SHA resolution
are external blockers. Workflow files alone do not prove configuration. Stop
before push/AWS and request explicit approval.
