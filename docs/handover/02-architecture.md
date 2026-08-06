# Architecture

## Two systems, not one

The repository contains two loosely coupled systems that meet at the database:

1. **An offline data pipeline** — scrapers, consolidation, deterministic enrichment, and
   a migration that loads versioned datasets into PostgreSQL. Run deliberately, not
   continuously.
2. **An online serving application** — a FastAPI backend-for-frontend over async
   PostgreSQL, and a React single-page UI.

Understanding that split explains most of the directory layout. `src/scrapers/`,
`src/preprocessing/`, `src/pipelines/`, `src/data/` and `src/migration/` belong to the
pipeline. `src/bff/` and `frontend/` belong to the serving application.
`src/transition/`, `src/governance/` and `src/observability/` straddle both.

```text
cached source JSON / optional live source APIs
        │
        ▼
  src/scrapers/ ──► src/preprocessing/consolidate.py ──► src/preprocessing/enrichment.py
                              │                                      │
              src/preprocessing/philea_adapter.py ──────────────────┘
                                                                     │
                                                                     ▼
                                        src/data/db_loader.py → SQLite migration source
                                                                     │
                              src/migration/sqlite_to_postgres.py (versioned, reconciled)
                                                                     │
                                                                     ▼
                                                              PostgreSQL
                                                                     │
  React/Vite UI ◄── cookie/bearer-authenticated JSON ── FastAPI BFF ─┤
                                                                     ├── async repositories
                                                                     ├── versioned analytics
                                                                     ├── durable job/outbox workers
                                                                     ├── scoring engine
                                                                     └── optional news service
```

## The dual-runtime split — read this before editing any route

Two complete API implementations exist in the tree, and the choice is made **at module
import time** in `src/bff/main.py:29`:

```python
TRANSITION_SETTINGS = load_transition_settings()
POSTGRESQL_ONLY_RUNTIME = TRANSITION_SETTINGS.postgresql_authoritative
if POSTGRESQL_ONLY_RUNTIME:
    from bff.postgres.routes import router as charity_router
    ...
else:
    from bff.charity import router as charity_router   # legacy SQLite
    from bff.admin import router as admin_router
```

| | Current path | Legacy path |
|---|---|---|
| Routes | `src/bff/postgres/routes.py` | `src/bff/charity.py` |
| Admin | `src/bff/postgres/admin_routes.py` | `src/bff/admin.py` |
| Data access | `src/bff/postgres/*_repository.py` (async, SQLAlchemy/asyncpg) | `src/bff/repositories.py` (sync, sqlite3) |
| Active when | `DATA_RUNTIME_MODE` is `postgresql` or `shadow_compare` (the default) | `DATA_RUNTIME_MODE=sqlite_migration_source` |
| Permitted in staging/production | Yes | **No** — rejected by `TransitionSettings.validate()` |

**Almost always, the file you want to edit is under `src/bff/postgres/`.** The legacy
layer is retained for local migration-compatibility work and for shadow comparison, and
is forbidden outside development and test. A subprocess architectural test blocks the
`sqlite3` import and loads the production application to prove the boundary holds.

The three runtime modes are defined in `src/transition/runtime.py`:

- `postgresql` — the default and only authoritative operational mode.
- `sqlite_migration_source` — local/test only, for migration-source compatibility work.
- `shadow_compare` — serves from PostgreSQL while replaying requests against a separate
  coherent SQLite snapshot and recording differences. Requires `SHADOW_SQLITE_PATH`
  pointing at a file that is not `DB_PATH`.

## Request lifecycle

A request to `GET /api/charities/grants/map` passes through, in order:

1. **CORS middleware** — explicit origins only; wildcards rejected at startup.
2. **Shadow comparison middleware** (`src/transition/shadow.py`) — active only in
   `shadow_compare` mode; bounded queue, byte cap and timeout so it can never block or
   alter the served response.
3. **Request instrumentation** (`src/bff/main.py`) — assigns request ID and trace ID,
   starts the duration timer, enforces body-size and timeout limits.
4. **Router-level role dependency** — `src/bff/postgres/routes.py:38` attaches
   `require_roles(Role.VIEWER, action="charity.read")` to every route on the router.
   Individual routes raise the requirement where needed.
5. **Authentication** (`src/bff/security.py:338`) — extracts an OIDC bearer token or the
   development session cookie, decodes it, derives roles from the configured claim, and
   applies the per-actor rate limit. There is no anonymous path.
6. **Idempotency reservation** — for mutating routes, an `Idempotency-Key` header is
   required and reserved durably in PostgreSQL before the handler runs.
7. **Handler** — resolves a repository from `request.app.state.database.sessions()` and
   delegates. Handlers stay thin; SQL lives in repositories.
8. **Audit sink** — writes an append-only `audit_events` row with pseudonymous actor ID,
   route template, action and outcome.
9. **Structured log** — one redacted JSON object per request.

Role inheritance is `administrator > operator > analyst > viewer`; a higher role
satisfies a lower-role read. Everything unlisted is denied by default.

## Data access layer

Repositories under `src/bff/postgres/` each own one domain and expose an async interface.
`src/bff/postgres/interfaces.py` declares the `Protocol` for each, which is what handlers
depend on:

| Repository | Owns |
|---|---|
| `organization_repository.py` | Organization list, detail, stats, grants, Sankey, score |
| `registry_repository.py` | Charity Commission registry pagination, cursor and search |
| `analytics_repository.py` | Map, overview, trends, drilldown, themes, summary — reads versioned aggregate tables |
| `funder_repository.py` | Source-funder ranking, detail, relink, reset, profile cache |
| `job_repository.py` | Durable job enqueue, status, history, events |
| `pipeline_repository.py` | Pipeline state and source configuration |
| `governance_repository.py` | Retention policies, holds, expiration reports, subject requests |
| `audit_repository.py` | Append-only audit event sink |
| `idempotency_repository.py` | Durable idempotency records |

`base.py` supplies the shared session helpers and `ANALYTICS_CACHE`.

Default map, trend, theme, summary and funder queries read pre-built versioned aggregate
tables rather than scanning facts. Arbitrarily filtered requests fall back to fact-table
execution. Heavy country-relationship queries are exposed through a separate endpoint
capped at 250 rows; funder-recipient materialization is capped at 50 per funder.

## Asynchronous work

Long-running work never runs inside a request. `POST /api/admin/pipeline/trigger` enqueues
a durable job and returns a job ID. `src/pipelines/durable_worker.py` claims jobs through
PostgreSQL leases, and `job_dispatch_outbox` provides the transactional outbox contract
that an SQS delivery path would consume. Workers record heartbeats, retry state and
dead-letter status in the database.

## Dataset versioning

Every serving row carries its dataset version in its primary and foreign keys. A candidate
dataset can therefore be loaded and reconciled alongside the approved one. A partial unique
index permits exactly one active dataset at a time; activation happens in a single explicit
transaction after reconciliation succeeds. Prior approved datasets stay addressable, which
is what makes rollback a status change rather than a restore. See
[06-data-model.md](06-data-model.md).

## Frontend

A Vite/React SPA. `frontend/src/App.tsx` holds application state, view routing and most
data fetching; six lazy-loaded components render the heavy views. All requests use
`credentials: "include"` and rely on a session cookie the application does not itself
create. Mutations attach an `Idempotency-Key` via `frontend/src/lib/http.ts`. See
[04-frontend-reference.md](04-frontend-reference.md).
