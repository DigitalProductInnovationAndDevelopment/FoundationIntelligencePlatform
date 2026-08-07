# Runtime transition guide

Date: 2026-07-29

PostgreSQL is the default and only authoritative operational datastore. The
transition configuration has three explicit modes:

| `DATA_RUNTIME_MODE` | Purpose | May serve user responses | Environment boundary |
|---|---|---:|---|
| `postgresql` | Normal runtime | Yes, from PostgreSQL | Default everywhere |
| `sqlite_migration_source` | Local/test migration compatibility | Local/test only | Rejected in staging/production |
| `shadow_compare` | Temporary PostgreSQL-primary comparison | Yes, PostgreSQL only | Requires a separate coherent SQLite snapshot |

There is no SQLite failover. A failed, timed-out, oversized or queue-rejected
shadow read cannot change the primary response. `shadow_compare` schedules the
SQLite read only after the final PostgreSQL response body has been passed to the
client. Work is bounded to eight pending comparisons, 2 MiB per response, a
20-second shadow timeout and 100 recorded differences.

Differences contain a JSON path, difference kind and SHA-256-derived value
fingerprints; values themselves are not logged. Ordering is ignored only for
the three allowlisted set-like paths in `config/runtime-transition.json`.
Rankings, pages, grant lists and Sankey links retain semantic order.

The SQLite adapter opens only an explicitly supplied snapshot copy. The
configuration rejects `SHADOW_SQLITE_PATH` when it resolves to the active
`DB_PATH`, preventing the compatibility repository from changing the preserved
source. Production images contain no SQLite database or source dataset.

Local activation example (temporary, not a production fallback):

```bash
cp src/data/charities.db /private/tmp/fip-shadow-snapshot.db
DATA_RUNTIME_MODE=shadow_compare \
SHADOW_SQLITE_PATH=/private/tmp/fip-shadow-snapshot.db \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 \
DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app \
DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder \
PYTHONPATH=src venv/bin/uvicorn bff.main:app --host 127.0.0.1 --port 8000
```

Delete only the temporary snapshot after evidence has been reviewed. Never
mount the preserved active SQLite source into a production task.

The versioned journey register contains dashboard, maps and relationships,
date/country/programme/donor/recipient filters, monthly/yearly trends,
donor/recipient rankings, registry search, profile, grant list, drill-down,
Sankey, score, news, pipeline status and manual-refresh authorization. Live
news is deliberately not invoked during local acceptance.
