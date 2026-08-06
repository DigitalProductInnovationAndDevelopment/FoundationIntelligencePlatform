# Backend reference

Python 3.12. Import root is `src/` — run everything with `PYTHONPATH=src`. Roughly 31,800
lines across 66 modules, plus 44 test modules.

## Package map

| Package | Role | Belongs to |
|---|---|---|
| `bff/` | FastAPI application, security, schemas, config | Serving |
| `bff/postgres/` | Async PostgreSQL repositories and the live route surface | Serving |
| `scrapers/` | Source-specific collectors | Pipeline |
| `preprocessing/` | Consolidation, deterministic enrichment, quality | Pipeline |
| `pipelines/` | Orchestration, durable jobs, targeted data operations | Pipeline |
| `data/` | SQLite migration source and registry storage | Pipeline |
| `migration/` | SQLite→PostgreSQL migration and release gating | Pipeline |
| `transition/` | Runtime mode selection and shadow comparison | Both |
| `governance/` | Retention planning and data exposure controls | Both |
| `observability/` | Metric and alarm definitions, local registry | Both |
| `scoring/` | Experimental relevance score | Serving |

---

## `src/bff/` — application core

| Module | LOC | Responsibility |
|---|---:|---|
| `main.py` | 378 | FastAPI construction, lifespan, middleware, runtime-mode router selection, health probes. **Start reading here.** |
| `config.py` | 249 | `SecuritySettings.from_env()` and `validate_security_settings()`. Fail-closed: raises `SecurityConfigurationError` at import rather than starting unsafely |
| `security.py` | 447 | OIDC and development authentication, `require_roles` dependency factory, rate limiting, idempotency reservation |
| `database.py` | 273 | `DatabaseSettings`, bounded async engine, session factory, independent no-pool readiness connection |
| `schemas.py` | 752 | All Pydantic request/response models — the API contract |
| `auth.py` | 77 | Development-only login/logout. Returns 404 outside development mode |
| `audit.py` | 66 | Structured audit events with a replaceable durable sink |
| `proxy.py` | 116 | Opt-in downstream proxy, fixed destination, path/method/header allowlists, disabled by default |
| `news.py` | 1,017 | Optional news discovery and sourced summarisation. Requires credentials; approval-gated |
| `utils/logging.py` | 100 | JSON log formatter and pseudonymous actor IDs |
| `charity.py` | 1,006 | **Legacy** SQLite route surface. Not used when `DATA_RUNTIME_MODE=postgresql` |
| `admin.py` | 329 | **Legacy** SQLite admin routes |
| `repositories.py` | — | **Legacy** synchronous SQLite data access |

`main.py:29` chooses between the PostgreSQL and legacy routers at import time — see
[02-architecture.md](02-architecture.md).

## `src/bff/postgres/` — the live data layer

Every module is async, uses SQLAlchemy over asyncpg, and receives a session factory rather
than constructing one. `interfaces.py` declares the `Protocol` each repository satisfies;
handlers depend on those protocols, not on concrete classes.

| Module | LOC | Responsibility |
|---|---:|---|
| `routes.py` | 631 | The complete organization and grant API surface. Handlers are thin; SQL lives in repositories |
| `analytics_repository.py` | 1,464 | Map facts, overview, trends, drill-downs, themes, summary. Reads versioned aggregate tables by default, falls back to fact scans for arbitrary filters |
| `funder_repository.py` | 1,024 | Source-funder ranking and detail, explicit link overrides, profile-cache jobs |
| `organization_repository.py` | 661 | Organization list, detail, stats, grant history, Sankey, score |
| `job_repository.py` | 651 | Durable job enqueue, status, history, event reads |
| `registry_repository.py` | 631 | Full-text (`websearch_to_tsquery` + `ts_rank_cd`) and trigram registry search with deterministic cursors |
| `governance_repository.py` | 380 | Retention policies, holds, non-destructive evidence |
| `pipeline_repository.py` | 288 | Source controls and immutable ingestion evidence |
| `governance_routes.py` | 202 | Administrator-only governance routes |
| `admin_routes.py` | 156 | Pipeline administration; enqueues durable jobs, never spawns subprocesses |
| `base.py` | 140 | Shared session primitives and `ANALYTICS_CACHE` |
| `idempotency_repository.py` | 120 | Durable idempotency records for horizontally scaled runtimes |
| `interfaces.py` | 66 | Repository `Protocol` definitions |
| `observability_routes.py` | 31 | Administrator-only metric definitions and bounded local evidence |

## `src/scrapers/` — source collectors

Live scraping is optional. The reproducible presentation build uses checked-in caches and
calls none of these.

| Module | Source |
|---|---|
| `register_of_charities.py` | Charity Commission API. Needs `CHARITY_COMMISSION_API_KEY` |
| `360giving.py` | 360Giving publisher grant feeds |
| `philea.py` | Philea member directory |
| `hinchilla.py` | Legacy source, retained for compatibility |

## `src/preprocessing/` — consolidation and enrichment

| Module | LOC | Responsibility |
|---|---:|---|
| `consolidate.py` | 832 | Maps Charity Commission and 360Giving records into common organization and grant records |
| `enrichment.py` | 710 | **The single active source of programme and geography taxonomy and rules.** Versioned, deterministic, keeps source and inferred values apart, emits evidence and confidence |
| `extract_impressum.py` | 465 | Website contact/imprint extraction. Optional; skipped by `--skip-contact-crawler` |
| `enrich_gemini.py` | 386 | Optional non-default LLM enrichment path. Requires `GEMINI_API_KEY`; not part of the deterministic build |
| `philea_adapter.py` | 337 | Normalizes Philea records, assigns stable negative local IDs, maps organization types, conservative cross-source dedup |
| `extract_geo_topic.py` | 150 | Backward-compatible entry points into `enrichment.py` |
| `quality.py` | 42 | Coverage and quality reporting |

Philea deduplication thresholds: exact normalized name or domain merges across sources;
fuzzy ≥ 0.92 auto-merges across sources only; 0.82–0.92 becomes a review candidate. In the
shipped cache all 299 records were added, none auto-merged, 19 ambiguous candidates
retained for review.

## `src/pipelines/` — orchestration and operations

| Module | LOC | Responsibility |
|---|---:|---|
| `run_pipeline.py` | 681 | **Main entry point.** Orchestrates collection, consolidation, enrichment, reports and database publication. Modes: `consolidate`, `full_run`, `refresh_charities`, `refresh_grants` |
| `backfill_ecb_exchange_rates.py` | 469 | Fetches and caches official ECB daily EXR rates, atomically backfills reproducible EUR values without altering source amounts |
| `curate_europe_tech_grants.py` | 364 | Screens the 360Giving cache for an EU/EEA/Switzerland tech-enablement profile without changing the active database |
| `durable.py` | 305 | Pure durable-pipeline contracts shared by API, workers and tests |
| `sample_360giving_publishers.py` | 268 | Resumable random sample of publisher grant feeds |
| `import_observed_360giving_grants.py` | 258 | Atomic append of observed grants to the active presentation database |
| `reclassify_grant_enrichment.py` | 190 | Atomic reclassification of stored enrichment against the current taxonomy |
| `durable_worker.py` | 183 | Worker lifecycle. Claims jobs via PostgreSQL leases; no subprocesses, no lock files |
| `extend_observed_360giving_pilot.py` | 180 | Safe, resumable pilot extension with overflow paging |
| `prewarm_grant_overview_cache.py` | 41 | Builds derived overview indexes and prewarms the default payload |

`full_run`, `refresh_charities` and `refresh_grants` call external sources. Use them
deliberately and with conservative limits.

## `src/data/`, `src/migration/`

| Module | LOC | Responsibility |
|---|---:|---|
| `data/db_loader.py` | 1,171 | Maintains the coherent SQLite migration source. Atomically replaces the database only after schema and minimum-data validation; a failed staging load leaves the active file untouched |
| `data/registry.py` | 519 | Charity Commission registry storage and import |
| `data/seed_db.py` | 175 | Local seeding |
| `data/benchmark_registry.py` | 73 | Reproducible local registry performance check |
| `migration/sqlite_to_postgres.py` | — | Deterministic versioned migration and reconciliation. Loads a candidate dataset, reconciles it exactly, then activates in one transaction |
| `migration/release_gate.py` | 103 | Fail-closed release/reconciliation gate for one-off ECS tasks |

The SQLite file is **not** an operational datastore. It exists as a migration source and
shadow fixture only.

## `src/transition/`, `src/governance/`, `src/observability/`, `src/scoring/`

| Module | LOC | Responsibility |
|---|---:|---|
| `transition/runtime.py` | 109 | `RuntimeMode` enum, `load_transition_settings()`, fail-closed validation |
| `transition/shadow.py` | 358 | Bounded async shadow reads, privacy-safe difference evidence, middleware |
| `transition/sqlite_source.py` | 293 | Shadow adapter; requires an explicit separate snapshot |
| `governance/retention.py` | 336 | Hold-aware, non-destructive retention planning. Destructive deletion is disabled by configuration |
| `governance/exposure.py` | 86 | Serializer allowlists and recursive sensitive-data redaction |
| `observability/metrics.py` | 234 | Metric and alarm definitions from `config/observability.json`, bounded local registry |
| `scoring/engine.py` | 343 | Deterministic, configurable, explainable target-profile relevance score. **Experimental** |

## Conventions

- `from __future__ import annotations` at the top of every module.
- Fail closed: configuration errors raise at import, not at first request.
- Repositories own SQL; route handlers own HTTP concerns only.
- Money is `NUMERIC(24,4)`, rates `NUMERIC(24,12)`, derived minor units `BIGINT`. Never
  float.
- Absent data is represented explicitly (`transaction_data_unavailable`,
  `organization_level_only`), never as zero.
- Mutating routes require an `Idempotency-Key` header.
