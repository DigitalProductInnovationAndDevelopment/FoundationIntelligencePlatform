# Testing

44 backend test modules (~9,140 lines) under `src/tests/`, three frontend unit suites, and
one Playwright end-to-end spec.

## Running

```bash
# Fast backend suite
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ./venv/bin/pytest -q -p no:cacheprovider src/tests

# The coverage gate CI enforces
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ./venv/bin/pytest src/tests --cov=bff --cov-fail-under=70

# Type checking
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini

# Blocking syntax/undefined-name gate
./venv/bin/flake8 src/ --count --select=E9,F63,F7,F82 --show-source --statistics

# Frontend
cd frontend
npm run lint          # oxlint
npm test              # tsc + Node test runner
npm run build         # typecheck, build, bundle budget
npm run test:e2e      # build + Playwright
npm run test:runtime  # build + rendered layout check
```

## Test environment

`src/tests/conftest.py` sets the process environment before anything imports the
application — this is why the suite works without a `.env`:

```python
APP_ENV=test
DATA_RUNTIME_MODE=sqlite_migration_source
AUTH_MODE=development, DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=admin, DEV_AUTH_PASSWORD=password
DEV_AUTH_SECRET=unit-test-signing-key-with-at-least-32-characters
RATE_LIMIT_REQUESTS=10000
```

Two consequences worth knowing:

- The **default suite runs against the legacy SQLite runtime**, because that is what the
  fast fixtures target. PostgreSQL behaviour is covered by the dedicated modules below.
- The credentials above are test fixtures. They are not defaults anywhere in the
  application, and no equivalent values exist in `.env.example`.

PostgreSQL integration tests are opt-in:

```bash
RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+asyncpg://... \
  PYTHONPATH=src ./venv/bin/pytest src/tests/test_postgres_application.py
```

## Suite map

### API and application

| Module | Covers |
|---|---|
| `test_bff.py` (937) | The main API surface — the broadest suite |
| `test_postgres_application.py` (372) | PostgreSQL runtime routes and repositories |
| `test_api_golden.py` | Response shapes against `config/golden/api-contract.json` |
| `test_security.py` (307) | Authentication, roles, rate limiting, idempotency |
| `test_impressum.py` | Contact/imprint extraction |
| `test_news.py` | Optional news summarisation, mocked provider |
| `test_transition_golden.py` | Transition domain golden fixtures |

### Data and migration

| Module | Covers |
|---|---|
| `test_sqlite_to_postgres_migration.py` (437) | Deterministic migration and exact reconciliation |
| `test_postgres_schema.py` (231) | Schema constraints and catalog assertions |
| `test_database.py`, `test_db_stability.py` (356) | Connection handling and stability |
| `test_consolidate.py` | Source-to-common-record mapping |
| `test_grant_transactions.py` (477) | Transaction integrity and coverage status |

### Domain

| Module | Covers |
|---|---|
| `test_source_funders.py` (462) | Funder ranking, overrides, profile cache |
| `test_grant_analytics.py` (286), `test_grant_overview.py` (258) | Aggregates and overview |
| `test_registry_directory.py` (223) | Registry search, cursors, tie-breaks |
| `test_scoring.py` (229) | Score determinism and explanation |
| `test_enrich.py` (282) | Deterministic enrichment rules |
| `test_philea_adapter.py` | Philea normalization and dedup thresholds |
| `test_backfill_ecb_exchange_rates.py` | ECB rate handling, including unconverted pre-1999 grants |

### Platform

| Module | Covers |
|---|---|
| `test_durable_pipeline.py` (411) | Job leases, retries, outbox |
| `test_pipeline_locking.py` | Concurrency control |
| `test_governance_retention.py` (351) | Hold-aware non-destructive retention |
| `test_observability.py` | Metric definitions and readiness contract |
| `test_shadow_transition.py` (195) | Shadow comparison and difference evidence |
| `test_postgres_performance.py` (315) | Query and pool performance |
| `test_release_gate.py` | Fail-closed release gating |
| `test_supply_chain.py` | Lockfile, SBOM and licence consistency |
| `test_http_load_smoke.py` | Bounded load smoke |

### Scrapers

`test_360giving.py`, `test_register_of_charities.py`, `test_hinchilla.py`,
`test_sample_360giving_publishers.py`, `test_curate_europe_tech_grants.py`,
`test_import_observed_360giving_grants.py` — all run against cached fixtures, never live
sources.

## Frontend tests

| File | Covers |
|---|---|
| `tests/grantScope.test.ts` | Grant scope normalization and URL round-tripping |
| `tests/numericRange.test.ts` | Numeric range validation |
| `tests/phase7Contracts.test.ts` | Frontend/API contract expectations |
| `e2e/phase7.spec.ts` | End-to-end journeys; CI runs it with axe at six viewports |

## Gates

| Gate | Threshold | Where |
|---|---|---|
| Backend coverage | 70% on `bff` | CI `backend-quality` |
| Flake8 | E9, F63, F7, F82 — blocking | CI + local |
| mypy | `mypy.ini` config | CI + local |
| Initial JS bundle | 120 KiB gzip | `npm run build` |
| Initial CSS | 25 KiB gzip | `npm run build` |
| Deferred chunk | 425 KiB gzip each | `npm run build` |
| Accessibility | axe at six viewports | CI `frontend-quality` |
| Licences | No AGPL/SSPL/GPL | `scripts/check_licenses.py` |

The 70% coverage floor applies to `bff` only, not to the pipeline packages. Coverage of
`src/pipelines/`, `src/scrapers/` and `src/preprocessing/` is materially lower — worth
knowing before you change them.

## Helper scripts

| Script | Purpose |
|---|---|
| `scripts/verify_transition.py` | Reproduce shadow comparison differences |
| `scripts/verify_local_restore.sh` | Prove a logical restore into an isolated database |
| `scripts/verify_local_rollback.py` | Prove dataset rollback |
| `scripts/benchmark_postgres.py` | Reproducible query benchmark |
| `scripts/load_test_api.py`, `scripts/http_load_smoke.py` | Bounded load checks |
| `scripts/validate_terraform_static.py` | Offline Terraform contract validation |
| `scripts/validate_ci_workflows.py` | Workflow definition checks |
| `scripts/generate_sbom.py`, `scripts/check_licenses.py` | Supply-chain evidence |
| `scripts/verify_container_image.sh` | Non-root, size, no-data and health assertions |

## Writing tests

- Use cached fixtures. No test may call a live source, a paid API or AWS.
- Assert on explicit absence (`transaction_data_unavailable`, `organization_level_only`)
  as much as on presence — silently turning unknown into zero is the failure mode this
  codebase most guards against.
- Changing a response shape means deliberately updating `config/golden/api-contract.json`.
- For PostgreSQL-specific behaviour, add to the opt-in integration modules rather than
  weakening the fast suite.
