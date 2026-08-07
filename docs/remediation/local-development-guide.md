# Local development guide

## Prerequisites

- Python 3.12
- Node.js 22 and npm
- Docker Desktop with Compose
- A local secret file containing the PostgreSQL password

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
