# AWS/PostgreSQL Remediation Progress

This file is the durable continuation ledger. Read it before resuming interrupted work.

## Overall status

- Target branch: `91-clean-up-code-for-aws-integration`
- Starting commit: `408eb879b05ec4d2caf92d9bbd782dda9b290e23`
- Current phase: Phase 3 — PostgreSQL schema
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

- Status: `IN PROGRESS`

### Phase 4 — SQLite to PostgreSQL migration

- Status: `PENDING`

### Phase 5 — Application conversion to PostgreSQL

- Status: `PENDING`

### Phase 6 — Performance and concurrency

- Status: `PENDING`

### Phase 7 — Frontend remediation

- Status: `PENDING`

### Phase 8 — Pipelines, S3 and durable jobs

- Status: `PENDING`

### Phase 9 — Governance and retention

- Status: `PENDING`

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
