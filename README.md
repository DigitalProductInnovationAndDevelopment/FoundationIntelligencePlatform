# Foundation Intelligence Platform

An explainable proof-of-concept for exploring foundations, charities, and observed grant
relationships. The presentation dataset combines cached Charity Commission for England and
Wales records, cached 360Giving grant transactions, and cached Philea member-directory
records. UK organization and grant coverage is the strongest part of the prototype;
DACH/European coverage is partial and primarily organization-level.

The platform deliberately separates source facts, normalized source values, deterministic
inferences, and illustrative fallback content. **It does not predict whether an
organization will donate.**

> **Documentation lives in [`docs/handover/`](docs/handover/00-start-here.md).** Start
> there. This README is a quickstart and a map.

## Status

Runs locally against PostgreSQL. **Never deployed to AWS** — no Terraform apply, no image
push, no OIDC exchange has ever been executed. Overall production status is `NO-GO`. See
[`docs/handover/12-acceptance-register.md`](docs/handover/12-acceptance-register.md) for
the formal delivered / not-delivered position.

| Capability | Status |
|---|---|
| Organization directory and detail | Complete, PostgreSQL-backed |
| Cached 360Giving grant ingestion | Sampled — 302,546 transactions, not complete coverage |
| Cached Philea ingestion | Complete — organization-level only, no grants |
| PostgreSQL migration and cutover | Complete **locally**; AWS cutover unexecuted |
| Programme and geography enrichment | Complete, deterministic, accuracy not externally validated |
| Grant list, network summary, Sankey, map | Complete, observed transactions only |
| Relevance score | **Experimental** — not client-approved, not a prediction |
| Complete DACH grant transactions | **Missing** — no source supplies this |

The full capability register, with every limitation stated, is in
[`docs/handover/01-system-overview.md`](docs/handover/01-system-overview.md).

## Architecture at a glance

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

Details in [`docs/handover/02-architecture.md`](docs/handover/02-architecture.md).

## Quickstart

Prerequisites: Python 3.12, Node.js 22 with npm, Docker Desktop with Compose.

```bash
python3.12 -m venv venv
./venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
./venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env

cd frontend && npm ci --ignore-scripts --no-audit --no-fund && cp .env.example .env && cd ..
```

### Enable authentication — required

There is no anonymous access path. `.env.example` ships `AUTH_MODE=disabled`, and in that
mode `POST /api/auth/login` returns 404 while every `/api/*` route returns 401. Add to the
root `.env`:

```bash
AUTH_MODE=development
DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=localdev
DEV_AUTH_PASSWORD=<choose a local password>
DEV_AUTH_SECRET=<at least 32 characters>
```

`DEV_AUTH_SECRET` shorter than 32 characters fails validation at startup. This mode is
accepted only when `APP_ENV` is `development` or `test`. Staging and production reject it
and require `AUTH_MODE=oidc`.

### Start PostgreSQL

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker-compose up -d postgres
docker-compose --profile operations run --rm migration upgrade head
```

Point `.env` at it (`DATABASE_HOST=127.0.0.1`, `DATABASE_PORT=55432`,
`DATABASE_NAME=foundation_intelligence`, `DATABASE_USER=foundation_app`,
`DATABASE_PASSWORD_FILE=<same file>`).

### Run

```bash
./start_backend.sh                              # terminal 1 — 127.0.0.1:8000
cd frontend && npm run dev -- --host 127.0.0.1  # terminal 2 — 127.0.0.1:5173
```

The UI does **not** log in — it sends `credentials: "include"` and expects a cookie.
Establish one, then load the page:

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"localdev","password":"<your password>"}'
```

Use `127.0.0.1` consistently — mixing it with `localhost` gives the browser two origins and
the cookie will not be sent.

Full instructions, including the container stack and dataset rebuild, are in
[`docs/handover/08-running-and-operating.md`](docs/handover/08-running-and-operating.md).

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./venv/bin/pytest -q -p no:cacheprovider src/tests
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
cd frontend && npm run lint && npm test && npm run build
```

See [`docs/handover/11-testing.md`](docs/handover/11-testing.md).

## Documentation map

| I want to… | Read |
|---|---|
| Understand what this is and its limits | [`01-system-overview.md`](docs/handover/01-system-overview.md) |
| Understand how it fits together | [`02-architecture.md`](docs/handover/02-architecture.md) |
| Find where code lives | [`03-backend-reference.md`](docs/handover/03-backend-reference.md), [`04-frontend-reference.md`](docs/handover/04-frontend-reference.md) |
| Call the API | [`05-api-reference.md`](docs/handover/05-api-reference.md) |
| Work with the schema | [`06-data-model.md`](docs/handover/06-data-model.md) |
| Configure it | [`07-configuration.md`](docs/handover/07-configuration.md) |
| Run or operate it | [`08-running-and-operating.md`](docs/handover/08-running-and-operating.md) |
| Deploy it | [`09-deployment.md`](docs/handover/09-deployment.md) |
| Change or extend it | [`10-extending.md`](docs/handover/10-extending.md) |
| Test it | [`11-testing.md`](docs/handover/11-testing.md) |
| Know what was and was not delivered | [`12-acceptance-register.md`](docs/handover/12-acceptance-register.md) |
| Understand the business rules | [`13-domain-logic.md`](docs/handover/13-domain-logic.md) |

`docs/remediation/` holds the 29 technical contracts and runbooks produced during the
PostgreSQL/AWS remediation. `docs/audits/` is the immutable audit baseline and must not be
edited.

## Three things that will trip you up

1. **There is no anonymous access.** See the quickstart above.
2. **Two API implementations exist in the tree.** `src/bff/postgres/routes.py` is live;
   `src/bff/charity.py` is legacy SQLite, selected at import time in `src/bff/main.py:29`.
   Almost always you want the former.
3. **SQLite is not the operational store.** It survives only as a migration source and
   shadow-comparison fixture, and is rejected in staging and production.

## Known limitations

- A bounded proof-of-concept snapshot, not a comprehensive UK, DACH or European foundation
  database.
- Philea contributes organization metadata only; no activity is inferred from membership.
- Enrichment coverage is measured; accuracy is not validated against labelled ground truth.
  Evidence and review flags must remain visible.
- The relevance score is an unapproved example and must not be framed as donation
  likelihood.
- When the BFF is offline, KPI cards, detail, news and admin simulation show clearly
  labelled mock content. Grant charts, flows, map values and scores remain **unavailable
  rather than fabricated**.
- The news route depends on live external pages and credentials, so it is not
  deterministic.
- Live pipeline modes depend on upstream availability and can be slow. Use cached
  consolidation for reproducibility.

## Licence

See [LICENSE](LICENSE).
