# Running and operating

## Prerequisites

- Python 3.12
- Node.js 22 and npm
- Docker Desktop with Compose
- A local file containing the PostgreSQL password

## First-time setup

```bash
python3.12 -m venv venv
./venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
./venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env

cd frontend
npm ci --ignore-scripts --no-audit --no-fund
cp .env.example .env
cd ..
```

Installs are hash-pinned. Do not `pip install` outside the lock files — the supply-chain
test (`src/tests/test_supply_chain.py`) checks this.

## Enable authentication — required, do this before anything else

**There is no anonymous access path.** `.env.example` ships `AUTH_MODE=disabled`, and in
that mode `POST /api/auth/login` returns 404 while every `/api/*` route returns 401. The
API will appear completely broken until you do this.

Add to the root `.env`:

```bash
AUTH_MODE=development
DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=localdev
DEV_AUTH_PASSWORD=<choose a local password>
DEV_AUTH_SECRET=<at least 32 characters>
```

Constraints enforced at startup (`src/bff/config.py:173-183`):

- `DEV_AUTH_SECRET` must be ≥32 characters, or the process refuses to start.
- `APP_ENV` must be `development` or `test`.
- The request host must be in `DEV_AUTH_ALLOWED_HOSTS` (`127.0.0.1,::1,localhost`).

Staging and production reject this mode entirely and require `AUTH_MODE=oidc`.

## Start PostgreSQL

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker-compose up -d postgres
docker-compose --profile operations run --rm migration upgrade head
```

Point `.env` at it: `DATABASE_HOST=127.0.0.1`, `DATABASE_PORT=55432`,
`DATABASE_NAME=foundation_intelligence`, `DATABASE_USER=foundation_app`, and
`DATABASE_PASSWORD_FILE` set to the same file.

## Run the backend and UI

Terminal 1:

```bash
./start_backend.sh          # PYTHONPATH=src uvicorn bff.main:app on 127.0.0.1:8000
```

Verify:

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Terminal 2:

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`.

**The UI does not log in.** It sends `credentials: "include"` and assumes a cookie exists.
Establish one, then reload the page:

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"localdev","password":"<your password>"}'

curl --fail -b cookies.txt http://127.0.0.1:8000/api/charities/stats
```

Use `127.0.0.1` consistently. Mixing `localhost` and `127.0.0.1` gives the browser two
different origins and the session cookie will not be sent.

## Full container stack

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
docker-compose up -d postgres
docker-compose --profile operations run --rm migration upgrade head
docker-compose up -d backend frontend
docker-compose ps
```

| Service | Port | Profile | Notes |
|---|---|---|---|
| `postgres` | `${POSTGRES_HOST_PORT:-5432}` | default | Pinned by digest; SCRAM-SHA-256 |
| `backend` | `${BACKEND_HOST_PORT:-8000}` | default | Waits for a healthy database; readiness healthcheck |
| `frontend` | `${FRONTEND_HOST_PORT:-8080}` | default | nginx, read-only root, all capabilities dropped |
| `migration` | — | `operations` | Runs `alembic upgrade head`, then exits |
| `worker` | — | `operations` | Runs the consolidate pipeline, then exits |

`docker-compose.yml` sets `AUTH_MODE: disabled`, so the containerized API rejects
everything. For an authenticated container path use `docker-compose.ecs-local.yml`, which
requires `DEV_AUTH_USERNAME`, `DEV_AUTH_PASSWORD` and `DEV_AUTH_SECRET` in your shell and
mirrors the ECS task layout.

The runtime image is data-free by design: no SQLite database, no raw data, cannot silently
serve from a local file.

Stop cleanly:

```bash
docker-compose stop frontend backend postgres
```

## Rebuild the presentation dataset

Deterministic, cached-source path. Calls no external APIs:

```bash
PYTHONPATH=src ./venv/bin/python src/pipelines/run_pipeline.py \
  --source consolidate \
  --skip-contact-crawler
```

Writes ignored JSONL and reports to `src/data/preprocessed/`, then atomically replaces
`src/data/charities.db` only after schema and minimum-data validation. A failed staging
load leaves the active database untouched.

`full_run`, `refresh_charities` and `refresh_grants` call external sources. Use them
deliberately, with conservative limits, and never during a demonstration.

Then migrate into PostgreSQL:

```bash
PYTHONPATH=src ./venv/bin/python -m migration.sqlite_to_postgres
```

This loads a candidate dataset, reconciles it exactly, and activates in one transaction
only if reconciliation passes. See [06-data-model.md](06-data-model.md).

## Health and observability

| Probe | Checks |
|---|---|
| `GET /health/live` | Process viability only. No database dependency |
| `GET /health/ready` | PostgreSQL query, expected Alembic revision, exactly one active dataset, source/retention config sync, outbox availability |

Readiness uses an **independent no-pool connection**, so exhausted analytical connections
cannot block the probe. It returns check states only — never identifiers or connection
strings.

Never relax readiness to route traffic around a missing schema or dataset. That is the
one thing it exists to prevent.

Logs are one redacted JSON object per line: timestamp, level, service, environment,
message, plus request ID, trace ID, pseudonymous actor ID, role, route template, duration,
status and error class on request completion. Raw exception payloads are excluded and
Phase-9 recursive redaction is applied. Paths use route templates, not raw IDs, to keep
cardinality bounded.

`GET /api/admin/observability/metrics` (administrator) returns the versioned definitions
from `config/observability.json` plus bounded local-process evidence. No CloudWatch metric,
dashboard or alarm has been created in AWS.

## Background work

Long work never runs in a request. `POST /api/admin/pipeline/trigger` enqueues a durable
job and returns a job ID; `src/pipelines/durable_worker.py` claims jobs through PostgreSQL
leases and records heartbeats, retries and dead-letter state. `job_dispatch_outbox` is the
transactional outbox an SQS delivery path would consume.

Inspect via `GET /api/admin/pipeline/status`, `/jobs` (operator) and `/logs`
(administrator).

## Routine checks

```bash
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./venv/bin/pytest -q -p no:cacheprovider src/tests
cd frontend && npm run lint && npm test && npm run build
```

See [11-testing.md](11-testing.md).

## Operational runbooks

Retained in `docs/remediation/`, all still current:

| Situation | Runbook |
|---|---|
| Something is wrong | `troubleshooting-guide.md` |
| Database recovery | `backup-restore-guide.md` |
| Reverting a bad dataset or release | `rollback-runbook.md` |
| Planned production cutover | `cutover-runbook.md` |
| Incident and alarm response | `observability-runbooks.md` |
| Retention, holds, subject requests | `retention-privacy-guide.md` |

Those runbooks describe procedures that have been prepared but, for anything touching AWS
or production, **never executed**. Treat them as decision steps requiring approval, not as
authorization.

No local development command authorizes AWS access, deployment, live source refresh, paid
API calls or a Git push.
