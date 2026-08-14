# 5. Operating

## Prerequisites

Python 3.12, Node.js 22 with npm, Docker Desktop with Compose, and a local file containing
the PostgreSQL password.

## Setup

```bash
python3.12 -m venv venv
./venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
./venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env

cd frontend && npm ci --ignore-scripts --no-audit --no-fund && cp .env.example .env && cd ..
```

Installs are hash-pinned. Packages should not be installed outside the lock files;
`src/tests/test_supply_chain.py` verifies this.

## Authentication configuration

There is no anonymous access path. `.env.example` ships with `AUTH_MODE=disabled`. In that
mode `POST /api/auth/login` returns 404 and all `/api/*` routes return 401. To use the API
locally, add the following to the root `.env`:

```bash
AUTH_MODE=development
DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=localdev
DEV_AUTH_PASSWORD=<choose a local password>
DEV_AUTH_SECRET=<at least 32 characters>
```

The following constraints are enforced at startup: the secret must be at least 32
characters, `APP_ENV` must be `development` or `test`, and the request host must be listed
in `DEV_AUTH_ALLOWED_HOSTS`. Staging and production reject this mode and require
`AUTH_MODE=oidc`.

## Starting PostgreSQL

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker-compose up -d postgres
docker-compose --profile operations run --rm migration upgrade head
```

Configure `.env` accordingly: `DATABASE_HOST=127.0.0.1`, `DATABASE_PORT=55432`,
`DATABASE_NAME=foundation_intelligence`, `DATABASE_USER=foundation_app`, and
`DATABASE_PASSWORD_FILE` set to the same file.

## Running the application

```bash
./start_backend.sh                              # terminal 1 — 127.0.0.1:8000
cd frontend && npm run dev -- --host 127.0.0.1  # terminal 2 — 127.0.0.1:5173
```

The frontend does not perform a login. It sends `credentials: "include"` and assumes a
session cookie exists. Establish one and then reload the page:

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"localdev","password":"<your password>"}'
```

Use `127.0.0.1` consistently. Mixing `127.0.0.1` and `localhost` produces two distinct
origins in the browser, and the session cookie will not be sent.

Interactive API documentation is available at `/docs` and the OpenAPI schema at
`/openapi.json`. These are generated from the code.

## Container stack

`docker-compose.yml` sets `AUTH_MODE: disabled`, so the containerized API rejects all
requests. For an authenticated container path, use `docker-compose.ecs-local.yml`, which
mirrors the ECS task layout and requires the development authentication variables to be
present in the shell.

| Service | Profile | Notes |
|---|---|---|
| `postgres` | default | Pinned by digest; SCRAM-SHA-256 |
| `backend` | default | Waits for a healthy database; readiness healthcheck |
| `frontend` | default | nginx, read-only root, all capabilities dropped |
| `migration` | `operations` | Runs `alembic upgrade head` and exits |
| `worker` | `operations` | Runs the consolidate pipeline and exits |

The runtime image contains no SQLite database and no raw data, so it cannot serve from a
local file.

## Configuration

Configuration is supplied from two sources: environment variables for runtime values and
secrets, and versioned JSON files under `config/` for policy and definitions.

Configuration errors raise at import time rather than allowing the process to start in an
unsafe state. The relevant validation is in `src/bff/config.py` and
`src/transition/runtime.py`.

`.env.example` serves as the environment-variable reference. It is commented and grouped by
concern, and `src/bff/config.py` holds the validation boundary for each variable. The root
`.env` is Git-ignored and is loaded only when `APP_ENV` is `development` or `test`. In
staging and production, values must be injected by the runtime.

| File | Governs | Read by |
|---|---|---|
| `runtime-transition.json` | Storage runtime mode, shadow-comparison bounds, 21 compared journeys | `src/transition/runtime.py` |
| `observability.json` | `expected_schema_version`, 21 metrics, 15 alarms | `src/observability/metrics.py` |
| `data-governance.json` | Owners, classifications, exposure policies, retention and backup policy | `src/governance/` |
| `source-pipelines.json` | Eight source definitions with owners, legal status and schedule state | `src/pipelines/durable.py` |
| `scoring.example.json` | Experimental score weights and target profile | `src/scoring/engine.py` |
| `golden/` | Asserted API and transition fixtures | `src/tests/` |

These files are executable configuration rather than documentation. Loaders validate
`configuration_version` and reject unknown versions.

Three defaults are restrictive by design: `destructive_deletion_enabled` is `false`,
`production_activation_approved` is `false`, and `legal_status` is `unresolved` for all
eight sources, which prevents schedule enablement. These represent outstanding approvals
rather than defects.

Secrets handling enforced in code: `DATABASE_PASSWORD_FILE` is preferred over a literal
password; a wildcard `CORS_ORIGINS` value is rejected; the proxy does not forward browser
`Authorization` or `Cookie` headers; and `VITE_*` values are compiled into the frontend
bundle and are therefore public.

## Health probes

| Probe | Checks |
|---|---|
| `GET /health/live` | Process viability only; no database dependency |
| `GET /health/ready` | PostgreSQL query, expected Alembic revision, exactly one active dataset, source and retention configuration synchronization, outbox availability |

Readiness uses an independent connection without pooling, so an exhausted analytical pool
cannot block it. It returns check states only, and does not return identifiers or
connection details.

Readiness should not be relaxed in order to route traffic around a missing schema or
dataset.

Logs are emitted as one redacted JSON object per line, including request identifier, trace
identifier, pseudonymous actor identifier, role, route template, duration and status. Paths
are recorded as route templates rather than raw identifiers, which bounds cardinality.

## Background work

Long-running work does not run inside a request. `POST /api/admin/pipeline/trigger`
enqueues a durable job and returns a job identifier. `src/pipelines/durable_worker.py`
claims jobs through PostgreSQL leases and records heartbeats, retries and dead-letter
state. Job state can be inspected through `/api/admin/pipeline/status`, `/jobs` and
`/logs`.

## Rebuilding the dataset

The deterministic path uses cached sources and calls no external APIs:

```bash
PYTHONPATH=src ./venv/bin/python src/pipelines/run_pipeline.py \
  --source consolidate --skip-contact-crawler

PYTHONPATH=src ./venv/bin/python -m migration.sqlite_to_postgres
```

The first step replaces `src/data/charities.db` atomically, and only after schema and
minimum-data validation; a failed staging load leaves the active file unchanged. The second
step loads a candidate dataset, reconciles it, and activates it in a single transaction
only if reconciliation passes.

The `full_run`, `refresh_charities` and `refresh_grants` modes call external sources. They
should be used deliberately and with conservative limits.

## Tests and gates

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./venv/bin/pytest -q -p no:cacheprovider src/tests
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
cd frontend && npm run lint && npm test && npm run build
```

The repository contains 44 backend test modules under `src/tests/`, three frontend unit
suites and one Playwright specification. Enforced gates are: 70% backend coverage on `bff`,
blocking Flake8 checks (E9, F63, F7, F82), mypy, bundle budgets of 120 KiB initial
JavaScript, 25 KiB CSS and 425 KiB per deferred chunk (gzip), axe accessibility checks at
six viewports, and a licence gate that rejects AGPL, SSPL and GPL declarations.

Two qualifications apply when interpreting a passing run:

- The default suite runs against the legacy SQLite runtime, because the fast fixtures
  target it. PostgreSQL behaviour is covered by opt-in modules, for example
  `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... pytest src/tests/test_postgres_application.py`.
- The 70% coverage floor applies to `bff` only. Coverage of `src/pipelines/`,
  `src/scrapers/` and `src/preprocessing/` is materially lower.

No test calls a live source, a paid API or AWS.

## Recovery procedures

The following procedures have been prepared and proven locally. None has been executed
against AWS or in production, and any production step requires approval. The verification
scripts are the executable form of each procedure.

These procedures assume immutable, Terraform-managed task definitions. The AWS environment
that is currently running was provisioned manually and does not meet that assumption, so
the rollback and cutover steps do not apply to it as written. See
[6. Deployment and status](06-deployment-and-status.md).

| Situation | Procedure |
|---|---|
| Readiness unavailable | Check liveness first, then inspect the readiness checks: secret file, host and port, Alembic revision, single active dataset, configuration synchronization, outbox tables. |
| Migration refuses the schema | Run `alembic current` and `alembic heads`. The gate reads the expected revision from `config/observability.json`. Upgrade with Alembic; do not edit `alembic_version`. |
| Shadow differences appear | Preserve the evidence, reproduce with `scripts/verify_transition.py`, and classify the cause. Only versioned allowlisted set ordering may be ignored. Broad tolerances should not be added, and responses should not be switched back to SQLite. |
| Bad dataset or release | Freeze writers, retain the failed target unchanged, then run `python -m migration.sqlite_to_postgres rollback --dataset-version <exact approved version>`. The target must already exist with `approved` or `rolled_back` status and a valid materialization. Status flags should not be forced in SQL. Local proof: `scripts/verify_local_rollback.py`. |
| Database recovery | Restore a full logical `pg_dump` archive into an isolated database and validate it before any promotion. Local proof: `scripts/verify_local_restore.sh`. RDS automated backups and PITR are defined in Terraform but untested. |
| Planned cutover | Freeze writers, take a final backup with checksum, load the delta as a new dataset version, reconcile, activate transactionally, run `python -m migration.release_gate`, run smoke tests, then observe. Any unexplained difference is a stop condition. The prior dataset is retained and the active version is not updated in place. |

Bidirectional replay is not part of the design. No conflict policy, ownership rule or
reconciliation design for it has been approved.

No local development command authorizes AWS access, deployment, live source refresh, paid
API calls or a Git push.

Per-alarm response procedures are documented in [runbooks.md](runbooks.md).
