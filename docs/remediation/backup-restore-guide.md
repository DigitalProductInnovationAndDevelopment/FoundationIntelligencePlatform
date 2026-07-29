# Backup and restore guide

The supported database recovery proof is a full PostgreSQL logical archive,
restored into an isolated database before any promotion. Backups must be
encrypted at rest in approved environments, access-controlled, checksummed,
retained under the unresolved governance policy and accompanied by RPO/RTO
evidence. Retention deletion remains disabled.

Local full proof:

```bash
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder \
POSTGRES_HOST_PORT=55432 \
DOCKER_CONFIG=/private/tmp/docker-config-no-creds \
DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock \
scripts/verify_local_restore.sh
```

The script uses `pg_dump --format=custom --compress=zstd:3`, creates the exact
temporary database `fip_phase13_restore_check_20260729`, performs
`pg_restore --exit-on-error`, and compares schema revision, active dataset,
charity/registry/grant counts, EUR total and materialization state. A trap drops
only that prefixed temporary database and removes only the exact temporary
archive path.

Local evidence on 2026-07-29:

- Archive bytes: `247509368`
- Archive SHA-256: `2c571954768ba4379f3e61160fb808cbc0bd35e6e13ec2f0b4d776c760ceae87`
- Schema: `0006_governance_retention`
- Active dataset: `sqlite-v7-8fc0cce61c81-r2`
- Charities/registry/grants: `373 / 397469 / 302546`
- Eligible EUR minor units: `2243598670770`
- Active materialization control rows: `1`
- Temporary database and archive removal: `PASS`

RDS automated backups, PITR and cross-account restore are defined in Terraform
but remain `NOT TESTED`; no AWS API was called.
