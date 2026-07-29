# AWS/PostgreSQL Remediation Progress

This file is the durable continuation ledger. Read it before resuming interrupted work.

## Overall status

- Target branch: `91-clean-up-code-for-aws-integration`
- Starting commit: `408eb879b05ec4d2caf92d9bbd782dda9b290e23`
- Current phase: Phase 10 — observability (next)
- Overall production status: `NO-GO`
- AWS mutations performed: none
- Paid external calls performed: none
- Push performed: none

## Phase ledger

### Phase 0 — Baseline and architecture contract

- Status: `COMPLETED`
- Files changed: baseline, progress, decisions, command log, architecture contract and migration-manifest JSON Schema under `docs/remediation/`; immutable `docs/audits/` remains byte-for-byte unchanged.
- Commands executed: initial Git state; immutable audit verification; source/backup SQLite validation and control SQL; repository/architecture inventory; backend/frontend tests/build; clean runtime start and HTTP checks; Docker build and storage inspection.
- Tests executed: Python compile; blocking Flake8; 286 backend tests with 76.29% coverage; `npm ci`; 8 frontend tests; frontend lint/build; SQLite quick/integrity/FK/control/anomaly/distribution queries; clean runtime health/auth/OpenAPI; cached API timing; Docker build attempt.
- Tests not executed: true empty-cache/concurrent runtime timing and successful current Docker image start/inspection are intentionally assigned to later performance and container gates.
- Known failures: frontend has five hook warnings and a 1.96 MB main chunk; current Docker build failed at `COPY src/` with `no space left on device` after an 8.81 GB context transfer.
- Technical blockers: Docker storage pressure for the legacy broad-copy build. This does not block creation of the corrected data-free image; safe cleanup/rebuild will be handled in Phase 2.
- External blockers: none established.
- Gate evidence: the active database checksum and 16 audit checksums were unchanged at the final check; the migration-manifest Schema parses as JSON; architecture boundaries, ownership, failure handling and rollback are documented.
- Baseline commit: `19e84ba11dd3567fc871b3411166ae59a5b6eef0` (`Document immutable AWS readiness baseline`).
- Architecture commit: the scoped commit containing this completed ledger, `aws-postgres-architecture.md` and the manifest Schema.
- Remaining work: none for Gate 0.
- Next exact action: classify every route and implement fail-closed OIDC/RBAC, local-development restrictions, proxy allowlists, request IDs, mutation idempotency and security audit evidence.

### Phase 1 — Security hardening

- Status: `COMPLETED`
- Files changed: typed/fail-closed security configuration; OIDC JWT/JWKS validation; hierarchical RBAC; request safety and audit middleware; local-only development authentication; restricted proxy; route-specific role/idempotency dependencies; log/admin-output redaction; frontend credential removal and mutation headers; route inventory; security tests.
- Gate evidence: all routes are classified in `aws-postgres-route-inventory.md`; no protected mutation/admin route is anonymous; proxy is disabled by default and requires administrator plus destination/path/method/header allowlists; browser Authorization/Cookie headers are never forwarded; OIDC signature/issuer/audience/expiry/role behavior is locally tested.
- Abuse controls: generated/validated request IDs, bounded request bodies, request/proxy timeouts, per-actor sliding-window rate limiting, required at-most-once keys on side-effecting routes, fixed CORS origins and structured audit events with the required fields.
- Tests executed: compile and blocking Flake8; 297 backend tests plus 8 mutation-route subtests with 76.69% coverage; 11 dedicated security test methods; 8 frontend tests; frontend lint/build; tracked-source secret scan; default-runtime HTTP checks; source/audit immutability checks.
- Known non-blocking warnings: 53 backend dependency/test-client deprecation warnings; five existing React hook warnings; 1.96 MB frontend main chunk. The browser warnings/chunk are assigned to Phase 7.
- Transitional limits: the current single-process rate limiter and idempotency store are deterministic local controls. WAF/API edge rate limiting and PostgreSQL durable idempotency/audit/job records are mandatory before horizontally scaled ECS production. The runtime audit sink is append-only structured output for the managed log pipeline; durable relational audit storage follows the PostgreSQL schema phase.
- External decisions: actual OIDC issuer/audience/role claim ownership remains unresolved. Production fails closed until those values are supplied; no identity provider or paid service was contacted.
- Gate result: `PASS`.
- Commit hash: the scoped Phase-1 commit containing this completed ledger.
- Next exact action: create a strict data-excluding `.dockerignore`, multi-stage non-root image and local PostgreSQL Compose foundation before attempting a bounded image build.

### Phase 2 — Docker and local PostgreSQL foundation

- Status: `COMPLETED`
- Files changed: strict `.dockerignore`; digest-pinned multi-stage backend/frontend Dockerfile; hash-locked Python inputs/outputs; same-origin static Nginx frontend; health-based Compose services for PostgreSQL/backend/frontend/migration/worker; database readiness manager; image contract script; multiarch bake declaration; dependency/digest record.
- Supply-chain evidence: Python installs/builds require exact hashes; npm uses lockfile version 3, `npm ci`, the approved registry and disabled lifecycle scripts; Dockerfile frontend and four base images are pinned to Docker Hub manifest digests.
- Gate evidence: local PostgreSQL 16.14, backend and frontend all healthy; direct and same-origin readiness report PostgreSQL healthy; frontend HTML loads; controlled shutdown completes; no arbitrary dependency sleeps.
- Container evidence: backend UID/GID `10001:10001`, read-only root, all capabilities dropped, no-new-privileges, explicit tmpfs and healthcheck; no SQLite, `.env`, application credential/key path, domain payload or compiler; image size `354092439` bytes under the 500 MB target. Frontend is a 56.2 MB static Nginx image with equivalent isolation.
- Multiarch evidence: versioned manifest-list digests and `linux/amd64` plus `linux/arm64` bake targets are present. The local Docker CLI lacks a working buildx plugin, so actual dual-platform manifest assembly remains a CI responsibility and is not falsely claimed as locally tested.
- Tests executed: 300 backend tests with 76.57% coverage; Python compile/Flake8/pip check; 8 frontend tests, lint and build; Compose config for default and operations profiles; complete local image builds; image export/contract scans; PostgreSQL SQL query; container/HTTP health, start and stop checks.
- Resolved failures: incompatible pip 26.1.2 resolver bootstrap pinned back to pip 25.3; absent Docker credential helper isolated via temporary empty config; npm build-stage dev omission fixed with `--include=dev`; occupied default PostgreSQL host port fixed through host-port parameters; Nginx health SPA fallback fixed with an explicit prefix proxy.
- Known non-blocking warnings: 53 backend test warnings; five existing React hook warnings; 1.96 MB frontend main chunk. Frontend warnings/chunk remain assigned to Phase 7.
- External actions: approved package/image downloads only. No AWS call, paid API, scraper/live model call, upload or push.
- Gate result: `PASS`.
- Commit hash: the scoped Phase-2 commit containing this completed ledger.
- Next exact action: implement the authoritative PostgreSQL schema as Alembic migrations and validate upgrade/downgrade behavior against the retained local PostgreSQL volume.

### Phase 3 — PostgreSQL schema

- Status: `COMPLETED`
- Files changed: Alembic async environment and initial authoritative revision; PostgreSQL schema contract; async session factory; deterministic registry search repository/cursor; PostgreSQL-only production router; runtime import boundary and real schema integration tests; container now carries Alembic files.
- Schema evidence: 25 application tables cover every required table plus migration/idempotency and normalized charity geography/programme relationships. Catalog evidence reports 30 validated FKs, zero unvalidated FKs and 117 check constraints.
- Search evidence: `pg_trgm`, one stored `tsvector` GIN index and two trigram GIN indexes exist. Real asyncpg tests demonstrate deterministic rank/registry-ID cursor pagination.
- Runtime evidence: staging/production selects the async PostgreSQL router before any legacy module import. A subprocess import hook fails on any `sqlite3` load and passes. Development/test retains the legacy repository only as a transition; unported production routes are absent, not fallback-enabled, until Phase 5.
- Migration evidence: host `upgrade head`; `downgrade base` with only `alembic_version` remaining; second host zero-to-head upgrade; second downgrade; non-root/read-only Compose migration-service zero-to-head upgrade; catalog revision verification.
- Tests executed: 304 passing unit/static tests plus one intentionally skipped live-PostgreSQL test in the normal suite (76.41% coverage); the same PostgreSQL test passes separately with real PostgreSQL, including FK rejection and search. Production-mode local process starts, reports PostgreSQL ready, denies anonymous search with 401 and shuts down cleanly.
- Resolved failure: the first real search test exposed an ambiguous asyncpg type for an optional null status; an explicit PostgreSQL text cast fixed it and all repetitions pass.
- Transitional limit: Phase 5 must port every application journey/domain before production can become a GO. Phase 3 proves that normal production does not import or fall back to SQLite, not that all routes are already available.
- External actions: local PostgreSQL/container operations only. No AWS, paid API, live scraper/model, upload or push.
- Gate result: `PASS` for schema creation, PostgreSQL runtime boundary, FK/search enforcement and zero-to-head migration.
- Commit hash: the scoped Phase-3 commit containing this completed ledger.
- Next exact action: implement deterministic, read-only SQLite-to-versioned-PostgreSQL loading with checksum/schema/integrity preflight, quarantine, reconciliation, activation and rollback.

### Phase 4 — SQLite to PostgreSQL migration

- Status: `COMPLETED`
- Files changed: deterministic read-only migration CLI and integration tests; Alembic revisions preserving exchange-rate month and raw grant timestamp precision; atomic global-record staging; migration-manifest Schema extensions; data-free runtime-image inclusion; committed JSON and Markdown reconciliation evidence.
- Source safety: immutable URI open, `PRAGMA query_only`, SHA-256 `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`, schema version `7`, `integrity_check=ok` and capacity preflight all pass. The final manifest records 64,959,250,432 bytes available against 23,340,679,168 required.
- Reconciliation evidence: all 12 source/target table counts match. Mandatory controls pass exactly: 373 charities; 397,469 registry rows; 302,546 grants; 345 registry links; 104,191 mapped grants; 134,554 classified grants; EUR 22,435,986,707.70; zero source-identity duplicate groups; zero FK violations; 432 missing conversions; 2 negative and 2,101 zero grants; 1 future-dated grant; 4,271 business-key duplicate groups; 9,073 duplicate-charity-number groups.
- Fidelity corrections: source ECB periods remain exact `YYYY-MM` strings and raw grant dates retain full ISO timestamp precision. Normalized analytical award dates remain typed `DATE` in fact tables. Alembic revisions `0002_exchange_rate_period` and `0003_grant_award_timestamp` both pass downgrade/upgrade tests.
- Activation safety: candidates load under a dataset version; global exchange-rate/override records remain staged until the same transaction that activates the candidate. Quarantine, retry, conflicting override, failed-candidate isolation and prior-active restoration are exercised against real PostgreSQL.
- Idempotency and rollback: repeating the final full command returns `idempotent_noop=true` with the same run ID. Full rollback from `sqlite-v7-8fc0cce61c81-r2` to `sqlite-v7-8fc0cce61c81` retained 302,546 grants and 397,469 registry rows; switching back to `r2` succeeded. Exactly one dataset is active in the final local state.
- Report evidence: `evidence/phase4-migration-manifest.json` validates against the Draft 2020-12 Schema; `evidence/phase4-migration-report.md` is the human-readable companion. Final run ID is `60af368e-c440-5521-9648-5ab272f9ddb6`; code revision is `d6d2b69d1f9ff7dd8bc6f58021060586b3c17757`.
- Tests executed: 308 passing normal tests, 2 intentional skips, 8 subtests and 53 known warnings; 5/5 real PostgreSQL migration integration tests; manifest Schema validation; 30/30 FKs validated; container image contract and isolated in-image migration/Alembic imports.
- Container evidence: rebuilt backend is UID/GID `10001:10001`, 354,209,929 bytes, healthchecked and free of SQLite/domain data, environment/credential files and compiler tools. Image ID is `sha256:e43491e5e7080e0923b9d777aa1f985bfd3c4897482d662d0be7bf7364758b91`; the pinned Python base manifest digest is unchanged.
- Protected state: active SQLite and the aggregate `docs/audits/` checksum remain byte-for-byte equal to baseline. No AWS, paid/live external API, scraper/model call, upload or push occurred.
- Gate result: `PASS`.
- Local implementation checkpoints: `262b4a8`, `9afbc4a`, `919aa96`, `a74d75c`, `d6d2b69`; the scoped closeout commit contains this ledger and durable evidence.
- Next exact action: port every application journey and repository to async PostgreSQL, remove production SQLite route gaps and prove parity before starting the performance gate.

### Phase 5 — Application conversion to PostgreSQL

- Status: `COMPLETED`
- Runtime coverage: all 25 legacy organization/grant route contracts are present in the PostgreSQL router. Organization list/detail/stats/grants/Sankey/score, registry directory/detail, map/overview/filter suggestions/trends/themes/drill-down/summary, source-funder list/detail, link overrides and profile cache now use domain-sized async PostgreSQL repositories.
- Operational state: production/staging admin status, manual triggers, history and logs use `job_runs`/`job_events`. Requests enqueue bounded idempotent work and never start a local scraper/subprocess. Production security audits use the append-only `audit_events` table.
- Runtime boundary: production/staging imports only PostgreSQL application and admin routers; the production startup test proves `sqlite3`, the legacy router and the SQLite repository are absent. Startup fails before serving when PostgreSQL configuration is missing, and readiness passes only after a real database query.
- Transaction evidence: Funder relink/reset/cache, durable jobs and audit writes were exercised through real asyncpg sessions inside an outer transaction and fully rolled back. No test artifacts remain in the active dataset.
- Integration evidence: five Phase-5 tests pass against PostgreSQL 16.14, including all read journeys with response-model validation, mutation/idempotency/audit behavior and production startup/router selection. The normal suite passes 310 tests, skips five explicit live-environment tests, and passes eight route subtests.
- Resolved failures: real PostgreSQL caught and fixed a reserved `grant` alias, an incorrectly typed interval bind, a missing CTE alias and ambiguous joined columns. These failures could not have been detected by mocked repositories alone.
- Protected state: active SQLite and aggregate `docs/audits/` checksums remain exactly at baseline. No dependency download, AWS access, live scraper/model/API call, upload or push occurred.
- Gate result: `PASS`.
- Commit hash: the scoped Phase-5 commit containing this ledger.
- Next exact action: measure cold/warm and concurrent PostgreSQL journeys, capture query plans, then fix query/index/materialization and duplicate-request root causes for Gate 6.

### Phase 6 — Performance and concurrency

- Status: `COMPLETED`
- Runtime changes: default map, monthly/yearly trends, programme themes, network rankings, country funders and top-recipient relationships read dataset-versioned PostgreSQL aggregates. Country connections are a separate lazy endpoint capped at 250 rows. Filtered journeys retain bounded fact-table queries instead of returning stale aggregate results.
- Cache and pool controls: the in-process serving cache is dataset-keyed, TTL-bounded, copy-safe and single-flight. Activation/rollback changes the dataset key and stale entries are pruned. The async SQLAlchemy pool remains bounded at size 5 plus overflow 5; statement, pool and connect timeouts remain explicit. Cancellation releases the connection and a subsequent query succeeds.
- Performance evidence: 20 application-cache-cold repository dashboards have p50 `157.56 ms`, p95 `255.31 ms` and p99 `539.80 ms`. Production-mode API p95 is `3.70 ms` health, `245.73 ms` organization list, `4.95 ms` map, `43.45 ms` overview, `6.04 ms` lazy connections, `18.01 ms` exact registry and `83.93 ms` text registry. Five concurrent dashboards complete in `472.80 ms` at `10.575` dashboards/s with zero errors.
- Query-plan evidence: `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` proves the default map uses `analytics_country_aggregates`, exact registry lookup uses `ix_registry_current_normalized_name`, and ranked text lookup uses a registry GIN/trigram index. The default paths no longer scan `grant_overview_facts` for every request.
- Materialization evidence: Alembic head `0004_versioned_analytics` contains nine aggregate tables and one transactional refresh function. The active `sqlite-v7-8fc0cce61c81-r2` materialization contains 204,220 rows; candidate activation builds it before switching the single active dataset, and rollback builds a missing prior materialization before reactivation.
- Tests executed: 311 passing normal tests, 9 explicit live-environment skips, 8 passing route subtests and 53 known warnings; five dedicated performance/cache/plan/concurrency/timeout tests; five PostgreSQL application tests; combined 15/15 PostgreSQL application/schema/migration tests; production-mode API load test and repository benchmark. All final measured request error rates are zero, the pool has zero checked-out connections after each run, and the final cache hit ratio is `0.8333` at the API layer.
- Resolved failures: the baseline default map, summary, dashboard and registry paths took 2–17 seconds. A first load script hit the application rate limit, an early test invocation omitted the database password, the schema fixture assumed an empty active-dataset state, and one cold timing/plan run exposed p95-versus-single-sample measurement ambiguity. The final harness uses local OIDC credentials, a test-only high request allowance, correct secret-file configuration, state restoration and percentile-based cold measurements.
- Evidence files: `evidence/phase6-performance-report.json` and `evidence/phase6-performance-report.md`.
- Protected state: active SQLite and aggregate `docs/audits/` checksums remain exactly at baseline. No dependency or image download, AWS call, paid/live API, scraper/model call, upload or push occurred. The final data-free backend rebuild succeeded with `--pull=false`; every dependency layer was cached. The local arm64 image is 354,456,439 bytes, runs as `10001:10001` and has ID `sha256:cf71388a8fc83cdc32632ea2cf8ea9b7b27d4d68b164f848cd6e97b49905af8a`.
- Gate result: `PASS`.
- Commit hash: the scoped Phase-6 commit containing this ledger and evidence.
- Next exact action: remove frontend request duplication and hook warnings, isolate loading/error states, implement responsive/accessibility fixes and split the oversized bundle without changing visual identity.

### Phase 7 — Frontend remediation

- Status: `COMPLETED`
- Runtime changes: the application shell no longer blocks on the health check; route, map and chart code is lazy; map connections are fetched only after explicit activation; obsolete requests are aborted and sequence-guarded; handled failures stay in panel-level UI rather than writing browser warnings/errors.
- Responsive/accessibility changes: the map remains the first major dashboard section; viewport shells and dense controls are bounded; KPI text wraps without cropping; mobile map controls wrap; filter drawers stay scrollable; skip navigation, active-page state, dialog focus traps, Escape handling and focus restoration cover Overview, Donor and Registry journeys.
- Bundle evidence: initial JavaScript is 87.81 KiB gzip against 120 KiB, initial CSS is 18.59 KiB against 25 KiB and the largest deferred JavaScript chunk is 392.36 KiB against 425 KiB. `npm run build` enforces these budgets.
- Tests executed: 13/13 frontend unit/contract tests; warn-free Oxlint; TypeScript/Vite production build; compressed bundle gate; local Chrome 150 runtime journeys at 320, 390, 768, 1024, 1440 and 1920 pixels. The runtime gate found no page overflow, clipped visible controls, cropped KPIs, unnamed visible controls, duplicate initial overview request, console warning/error or runtime exception. The named Playwright/axe suite passes eight journeys and intentionally skips four redundant secondary-journey viewport combinations. It reports zero axe violations across six Overview widths plus Donor/Registry journeys at 320 and 1024 pixels.
- Accessibility corrections from the named gate: active-state text now meets WCAG AA contrast, the mobile data-source disclosure has an explicit accessible name, the map SVG uses valid ARIA and the Overview heading hierarchy includes its H2 level.
- Supply-chain action: after explicit approval, exact `@playwright/test==1.62.0` and `@axe-core/playwright==4.12.1` development dependencies were resolved and downloaded only from `registry.npmjs.org`. The reviewed lock delta contains only those packages and their exact transitive dependencies. `npm ci --ignore-scripts` ran with browser download disabled; installed Chrome 150 is used locally.
- Protected state: active SQLite and aggregate `docs/audits/` checksums remain exactly at baseline. No AWS call, paid/live API, scraper/model call, browser download, upload or push occurred.
- Gate result: `PASS`.
- Commit hash: the scoped Phase-7 commit containing this ledger and evidence.
- Evidence files: `frontend-bundle-budget.md`, `evidence/phase7-frontend-report.json` and `evidence/phase7-frontend-report.md`.
- Next exact action: wait for explicit authorization before beginning Phase 8; do not perform S3/AWS, deployment, push or any other remote action.

### Phase 8 — Pipelines, S3 and durable jobs

- Status: `COMPLETED`
- Durable coordination: production/staging request idempotency is PostgreSQL-backed. Manual triggers transactionally create a job, first event and versioned dispatch-outbox envelope; API requests never launch a scraper or subprocess. Workers claim with `FOR UPDATE SKIP LOCKED`, publish leases/heartbeats and record bounded retry, timeout, success, failure and dead-letter transitions.
- Storage contract: `raw`, `validated`, `curated` and `export` object descriptors retain object version, SHA-256, byte length, content type and source/run ownership. Raw descriptors and canonical ingestion manifests are database-enforced immutable; corrections create new versions.
- Source controls: eight versioned configurations cover 360Giving, Charity Commission, Philea, Hinchilla, ECB, Google News RSS, bounded article content and optional Anthropic summaries. All legal/licence states remain `unresolved`; all schedules are disabled and governance-blocked. Application and database validation prevent activation before approval.
- Last-good preservation: jobs and ingestion runs retain the active dataset at enqueue/start. The retry/failure integration leaves `sqlite-v7-8fc0cce61c81-r2` active and never activates candidate data.
- Migration evidence: transactional `0005 -> 0004 -> 0005` cycle passes. The final catalog reports Alembic `0005_durable_pipeline`, 40 application tables, 49 FKs, 161 checks, eight source configs, zero enabled schedules and eight governance blocks.
- Tests executed: 318 passing normal tests, 10 explicit live-environment skips and 8 passing subtests; 8/8 dedicated Phase-8 PostgreSQL tests; 18/18 combined Phase-8/application/schema PostgreSQL tests; Python compile, blocking Flake8 and diff checks.
- AWS boundary: S3/SQS/DLQ/EventBridge/Step Functions interfaces and state contracts exist, but no AWS execution or deployment occurred and none is claimed.
- Evidence files: `pipeline-storage-contract.md`, `evidence/phase8-durable-pipeline-report.json` and `evidence/phase8-durable-pipeline-report.md`.
- Protected state: active SQLite and aggregate `docs/audits/` checksums remain exactly at baseline. No dependency download, live external call, paid API, upload or push occurred.
- Gate result: `PASS` for local code readiness; real AWS execution remains `NOT TESTED` by design.
- Next exact action: implement Phase-9 governance, explicit field exposure, configurable retention dry-run, holds, export expiry and auditable deletion manifests without enabling destructive deletion.

### Phase 9 — Governance and retention

- Status: `COMPLETED`
- Governance configuration: 14 required classifications, source/owner registers, privacy checklist, field allowlists, log-redaction keys, proposed backup/PITR policy and unresolved RTO/RPO are versioned under `config/`. Unknown owners and legal/licence states remain explicitly unresolved.
- Retention safety: automatic/destructive deletion and production activation are disabled; every destructive window is unset. Proposed archive windows generate dry-run reports only. PostgreSQL permits initial deletion manifests only for dry-run report/archive actions, and no destructive HTTP route exists.
- Hold/restore evidence: exact, retention-class and global legal/incident holds override retention. Deletion and restore-verification evidence is append-only; a restore record alone never enables deletion.
- Exposure/redaction: generic admin outputs use named allowlists and exclude job input, actor identity and credential references. Recursive redaction covers credentials, connection strings, email, postal address, raw payload and article content before serialization; plain log text also removes credential/email shapes.
- Workflow: export expiration is report-only; data-subject intake stores a hashed reference and requires identity verification, scoped review, hold/legal review, separate mutation approval and audit evidence.
- Migration/catalog: `0006 -> 0005 -> 0006` passes. Final catalog is revision `0006_governance_retention`, 45 tables, 55 FKs and 189 checks; 14 policies exist with zero destructive flags and zero delete windows.
- Tests executed: 326 normal tests, 11 explicit live skips and 8 subtests; 9/9 dedicated governance PostgreSQL tests; 27/27 combined Phase-9/Phase-8/application/schema tests; compile, blocking Flake8, JSON and diff checks.
- Evidence files: `data-governance-register.md`, `retention-privacy-guide.md`, `evidence/phase9-governance-report.json` and `evidence/phase9-governance-report.md`.
- Protected state/external boundary: SQLite and `docs/audits/` remain at baseline. No AWS, paid/live API, download, upload or push occurred.
- Gate result: `PASS` for the local governance framework; policy/owner/legal/licence/RTO/RPO approvals remain unresolved and production remains `NO-GO`.
- Next exact action: implement structured JSON request/job logging, richer readiness checks, metric/alarm definitions and operational runbooks without claiming deployed CloudWatch resources.

### Phase 10 — Observability

- Status: `PENDING`

### Phase 11 — Terraform AWS infrastructure definitions

- Status: `PENDING`
- Execution restriction: no apply/destroy/import/state mutation or AWS resource change.

### Phase 12 — CI/CD

- Status: `PENDING`

### Phase 13 — Shadow comparison and cutover preparation

- Status: `PENDING`

### Push checkpoint

- Status: `PENDING`
- Rule: stop and request explicit user confirmation before the first push.
