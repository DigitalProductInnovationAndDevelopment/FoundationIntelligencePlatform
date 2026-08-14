# Foundation Intelligence Platform

A proof-of-concept platform for exploring philanthropic foundations, charities and the
grant relationships observed between them. The dataset combines cached Charity Commission
(England & Wales) records, cached 360Giving grant transactions and cached Philea
member-directory records. UK organization and grant coverage is the most complete part of
the prototype; DACH and European coverage is partial and primarily organization-level.

The platform separates source facts, deterministic inferences and platform-derived values,
and reports coverage gaps rather than substituting zeros. It does not predict whether an
organization will donate.

Technical documentation is in [`docs/`](docs/README.md).

## Status

The system runs locally against PostgreSQL. It has been deployed to AWS once, as an
environment provisioned manually through the console and CLI rather than from the Terraform
definitions in this repository. That environment is still running, is not reproducible from
source, and has no CloudWatch monitoring. The Terraform definitions and the CI/CD
deployment workflows remain unexecuted. Overall production status is `NO-GO`. See
[`docs/06-deployment-and-status.md`](docs/06-deployment-and-status.md).

## Architecture

```text
cached source JSON ──► scrapers ──► consolidation ──► deterministic enrichment
                                                              │
                                          SQLite migration source (build artifact)
                                                              │
                                   deterministic versioned migration + reconciliation
                                                              │
                                                              ▼
React/Vite UI ◄── authenticated JSON ── FastAPI BFF ──► PostgreSQL
                                             │
                                             ├── async repositories
                                             ├── versioned analytics
                                             ├── durable job/outbox workers
                                             └── experimental score engine
```

## Setup

Prerequisites: Python 3.12, Node.js 22 with npm, Docker Desktop with Compose.

```bash
python3.12 -m venv venv
./venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
./venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env

cd frontend && npm ci --ignore-scripts --no-audit --no-fund && cp .env.example .env && cd ..
```

### Authentication configuration

There is no anonymous access path. `.env.example` ships with `AUTH_MODE=disabled`. In that
mode `POST /api/auth/login` returns 404 and all `/api/*` routes return 401. Add the
following to the root `.env`:

```bash
AUTH_MODE=development
DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=localdev
DEV_AUTH_PASSWORD=<choose a local password>
DEV_AUTH_SECRET=<at least 32 characters>
```

A secret shorter than 32 characters fails validation at startup. This mode is accepted only
when `APP_ENV` is `development` or `test`. Staging and production require `AUTH_MODE=oidc`.

### Starting PostgreSQL

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker-compose up -d postgres
docker-compose --profile operations run --rm migration upgrade head
```

Configure `.env` accordingly: `DATABASE_HOST=127.0.0.1`, `DATABASE_PORT=55432`,
`DATABASE_NAME=foundation_intelligence`, `DATABASE_USER=foundation_app`,
`DATABASE_PASSWORD_FILE=<same file>`.

### Running

```bash
./start_backend.sh                              # terminal 1 — 127.0.0.1:8000
cd frontend && npm run dev -- --host 127.0.0.1  # terminal 2 — 127.0.0.1:5173
```

The frontend does not perform a login. It sends `credentials: "include"` and assumes a
session cookie exists. Establish one and then load the page:

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"localdev","password":"<your password>"}'
```

Use `127.0.0.1` consistently. Mixing it with `localhost` produces two distinct origins in
the browser, and the session cookie will not be sent.

Full instructions are in [`docs/05-operating.md`](docs/05-operating.md).

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./venv/bin/pytest -q -p no:cacheprovider src/tests
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
cd frontend && npm run lint && npm test && npm run build
```

The default suite runs against the legacy SQLite runtime. PostgreSQL behaviour is covered
by opt-in modules, enabled with `RUN_POSTGRES_INTEGRATION=1`.

## Documentation

The [`docs/`](docs/README.md) directory contains six pages and a set of alarm runbooks.
Detailed reference information is maintained in the code rather than duplicated in
Markdown:

| Subject | Source |
|---|---|
| API surface | `GET /docs` on a running instance (OpenAPI, generated from the code) |
| Request and response fields | `src/bff/schemas.py` |
| SQL and query behaviour | `src/bff/postgres/*_repository.py` |
| Database schema | `alembic/versions/` |
| Environment variables | `.env.example` and the validation in `src/bff/config.py` |
| Policy and thresholds | `config/*.json` |
| Enrichment taxonomy and rules | `src/preprocessing/enrichment.py` |
| Frontend filter semantics | `frontend/src/lib/grantScope.ts` |

Each module carries a docstring describing its responsibility and any constraints on
modifying it. `src/bff/main.py` is the entry point for the backend.

## Behaviour that is not apparent from the code structure

1. There is no anonymous access. See the authentication configuration above.
2. Two API implementations exist in the tree. `src/bff/postgres/routes.py` is the active
   surface and `src/bff/charity.py` is a legacy SQLite implementation. The selection is
   made at import time in `src/bff/main.py`.
3. SQLite is not the operational datastore. It is retained as a migration source and
   shadow-comparison fixture, and is rejected in staging and production.

## Limitations

- The one AWS environment that exists was provisioned manually and is not reproducible
  from the Terraform definitions in this repository. It has no CloudWatch monitoring, and
  the CI/CD deployment workflows have never been used.
- Test coverage is uneven. The enforced 70% floor applies to the `bff` package only;
  coverage of `src/pipelines/`, `src/scrapers/` and `src/preprocessing/` is materially
  lower. The default suite also runs against the legacy SQLite runtime, so
  PostgreSQL-specific regressions can pass unnoticed unless the opt-in integration modules
  are run.
- No load or concurrency testing has been performed against a deployed environment.
- The dataset is a bounded proof-of-concept snapshot rather than a comprehensive UK, DACH
  or European foundation database. 360Giving ingestion is a sample, so the absence of a
  grant record does not indicate the absence of funding.
- Philea contributes organization metadata only; no activity is inferred from membership.
- Enrichment coverage is measured; accuracy is not validated against labelled ground truth.
  Evidence and review flags should remain visible.
- The relevance score is an unapproved example and should not be presented as an indication
  of donation likelihood.
- When the backend is offline, KPI cards, detail views, news and admin simulation display
  labelled mock content. Grant charts, flows, map values and scores remain unavailable
  rather than fabricated.
- The news route depends on live external pages and credentials and is not deterministic.
- Live pipeline modes depend on upstream availability and can be slow. Cached consolidation
  is the reproducible path.

## Licence

See [LICENSE](LICENSE).
