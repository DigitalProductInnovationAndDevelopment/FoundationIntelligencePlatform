# 2. Architecture

## System composition

The repository contains two loosely coupled systems that meet at the database:

1. An offline data pipeline consisting of scrapers, consolidation, deterministic
   enrichment, and a migration that loads versioned datasets into PostgreSQL. It is run
   deliberately rather than continuously.
2. An online serving application consisting of a FastAPI backend-for-frontend over async
   PostgreSQL and a React single-page application.

This separation accounts for most of the directory layout.

```text
cached source JSON  (optional: live source APIs)
        │
        ▼
  src/scrapers/ ──► src/preprocessing/consolidate.py ──► src/preprocessing/enrichment.py
                                                                     │
                                        src/data/db_loader.py → SQLite migration source
                                                                     │
                              src/migration/sqlite_to_postgres.py (versioned, reconciled)
                                                                     │
                                                                     ▼
                                                              PostgreSQL
                                                                     │
  React/Vite UI ◄── authenticated JSON ── FastAPI BFF ───────────────┤
                                                                     ├── async repositories
                                                                     ├── versioned analytics
                                                                     ├── durable job/outbox workers
                                                                     ├── scoring engine
                                                                     └── optional news service
```

## Code layout

| Path | Responsibility | Side |
|---|---|---|
| `src/bff/` | FastAPI application, security, schemas, configuration | Serving |
| `src/bff/postgres/` | Async repositories and the active route surface | Serving |
| `src/scoring/` | Experimental relevance score | Serving |
| `frontend/` | React/Vite single-page application | Serving |
| `src/scrapers/` | Source-specific collectors | Pipeline |
| `src/preprocessing/` | Consolidation, enrichment, quality checks | Pipeline |
| `src/pipelines/` | Orchestration, durable jobs, targeted data operations | Pipeline |
| `src/data/`, `src/migration/` | SQLite migration source, migration and release gating | Pipeline |
| `src/transition/` | Runtime mode selection and shadow comparison | Both |
| `src/governance/` | Retention planning and exposure controls | Both |
| `src/observability/` | Metric and alarm definitions | Both |

The backend comprises approximately 32,400 lines across 68 modules, with 44 test modules.
The frontend comprises approximately 8,500 lines. Each module carries a docstring stating
its responsibility.

## Runtime selection

Two API implementations exist in the tree. The selection is made at module import time in
`src/bff/main.py`, based on `DATA_RUNTIME_MODE`.

| | Active path | Legacy path |
|---|---|---|
| Routes | `src/bff/postgres/routes.py` | `src/bff/charity.py` |
| Admin | `src/bff/postgres/admin_routes.py` | `src/bff/admin.py` |
| Data access | `src/bff/postgres/*_repository.py` (async, SQLAlchemy/asyncpg) | `src/bff/repositories.py` (sync, sqlite3) |
| Selected when | `DATA_RUNTIME_MODE` is `postgresql` (default) or `shadow_compare` | `DATA_RUNTIME_MODE=sqlite_migration_source` |
| Permitted in staging and production | Yes | No; rejected by `TransitionSettings.validate()` |

New work belongs under `src/bff/postgres/`. The legacy layer is retained for local
migration-compatibility work and shadow comparison. A subprocess test blocks the `sqlite3`
import and loads the production application to verify that the boundary holds.

The three runtime modes are defined in `src/transition/runtime.py`. In `shadow_compare`
mode the application serves from PostgreSQL while replaying requests against a separate
SQLite snapshot and recording differences. The bounds for this comparison are defined in
`config/runtime-transition.json`.

## Request lifecycle

A request to an `/api/*` route passes through the following stages in order:

1. CORS handling. Explicit origins only; wildcards are rejected at startup.
2. Shadow comparison (`src/transition/shadow.py`), active only in `shadow_compare` mode.
   A bounded queue, byte cap and timeout prevent it from blocking or altering the
   response.
3. Instrumentation. Assigns request and trace identifiers, starts the duration timer, and
   enforces body-size and timeout limits.
4. Role dependency. The router applies `require_roles(Role.VIEWER, ...)`; individual
   routes raise the requirement where applicable.
5. Authentication (`src/bff/security.py`) using an OIDC bearer token or the development
   session cookie, followed by per-actor rate limiting. There is no anonymous path.
6. Idempotency reservation. Mutating routes require an `Idempotency-Key`, which is
   reserved durably in PostgreSQL before the handler runs.
7. Handler execution. The handler resolves a repository from the application-scoped
   session factory and delegates. Handlers contain HTTP concerns only; SQL resides in
   repositories.
8. Audit sink. Writes one append-only `audit_events` row with a pseudonymous actor ID.
9. Structured logging. One redacted JSON object per request.

Roles inherit in the order `administrator > operator > analyst > viewer`. Any action not
listed is denied by default.

## Data access

Each repository under `src/bff/postgres/` owns one domain and exposes an async interface.
`interfaces.py` declares the `Protocol` that handlers depend on, rather than the concrete
class.

Default map, trend, theme, summary and funder queries read pre-built versioned aggregate
tables rather than scanning fact tables. Requests with arbitrary filters fall back to
fact-table execution. Heavy queries are capped at 250 country connections, 50 recipients
per funder and 100 registry rows.

## Asynchronous work

Long-running work does not run inside a request. `POST /api/admin/pipeline/trigger`
enqueues a durable job and returns a job identifier. `src/pipelines/durable_worker.py`
claims jobs through PostgreSQL leases. The `job_dispatch_outbox` table provides the
transactional outbox contract that an SQS delivery path would consume. Workers record
heartbeats, retry state and dead-letter status in the database.

## Frontend

The frontend is a Vite/React single-page application. `frontend/src/App.tsx` holds
application state, view routing and most data fetching. Six heavy views are lazy-loaded to
remain within the bundle budget of 120 KiB initial JavaScript (gzip), which is enforced by
`npm run build`.

Two conventions apply throughout:

- Every request sends `credentials: "include"`. The application does not perform a login;
  it assumes a session cookie already exists.
- Loading state is tracked per section rather than globally, so that a profile can render
  while one section is still loading or has failed.

Grant filter semantics are defined in `frontend/src/lib/grantScope.ts` and should be
changed there rather than in individual components.
