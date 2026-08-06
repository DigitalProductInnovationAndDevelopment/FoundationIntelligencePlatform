# Local development guide

## Prerequisites

- Python 3.12
- Node.js 22 and npm
- Docker Desktop with Compose
- A local secret file containing the PostgreSQL password

## Authentication is required before the API will respond

There is no anonymous access path. With the shipped default `AUTH_MODE=disabled`,
`POST /api/auth/login` returns 404 and every `/api/*` route returns 401. Local work
therefore requires the explicit development mode. Add to your root `.env`:

```bash
AUTH_MODE=development
DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=<choose a local username>
DEV_AUTH_PASSWORD=<choose a local password>
DEV_AUTH_SECRET=<at least 32 characters>
```

`DEV_AUTH_SECRET` shorter than 32 characters fails validation at startup. This mode is
permitted only when `APP_ENV` is `development` or `test`, and requests must originate
from `DEV_AUTH_ALLOWED_HOSTS` (`127.0.0.1,::1,localhost` by default). Staging and
production reject it and require `AUTH_MODE=oidc`.

`docker-compose.yml` sets `AUTH_MODE: disabled` for the container stack. To exercise an
authenticated container path, use `docker-compose.ecs-local.yml`, which wires the
`DEV_AUTH_*` variables through from your shell.

Install only from the committed locks:

```bash
python3.12 -m venv venv
venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
venv/bin/python -m pip install --require-hashes -r requirements.txt
cd frontend && npm ci --ignore-scripts --no-audit --no-fund && cd ..
```

Start PostgreSQL and the complete stack:

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker-compose up -d postgres
docker-compose --profile operations run --rm migration upgrade head
docker-compose up -d backend frontend
docker-compose ps
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Compose explicitly sets `DATA_RUNTIME_MODE=postgresql`. The image is data-free;
it cannot silently serve from SQLite. To work only on legacy migration-source
compatibility outside Compose, set `DATA_RUNTIME_MODE=sqlite_migration_source`
and `APP_ENV=development`; this mode is rejected in staging/production.

Run local checks:

```bash
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
PYTHONPATH=src venv/bin/python -m pytest -q
cd frontend && npm run lint && npm test && npm run build
```

Stop cleanly:

```bash
docker-compose stop frontend backend postgres
```

No local development command authorizes AWS, deployment, live source refresh,
paid APIs or Git push.
