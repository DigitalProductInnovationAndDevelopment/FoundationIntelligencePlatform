# AWS/PostgreSQL Remediation Progress

This file is the durable continuation ledger. Read it before resuming interrupted work.

## Overall status

- Target branch: `91-clean-up-code-for-aws-integration`
- Starting commit: `408eb879b05ec4d2caf92d9bbd782dda9b290e23`
- Current phase: Phase 0 — baseline and architecture contract
- Overall production status: `NO-GO`
- AWS mutations performed: none
- Paid external calls performed: none
- Push performed: none

## Phase ledger

### Phase 0 — Baseline and architecture contract

- Status: `IN PROGRESS`
- Files changed: baseline, progress, decisions, command log, architecture contract and migration-manifest JSON Schema under `docs/remediation/`; immutable `docs/audits/` remains byte-for-byte unchanged.
- Commands executed: initial Git state; immutable audit verification; source/backup SQLite validation and control SQL; repository/architecture inventory; backend/frontend tests/build; clean runtime start and HTTP checks; Docker build and storage inspection.
- Tests executed: Python compile; blocking Flake8; 286 backend tests with 76.29% coverage; `npm ci`; 8 frontend tests; frontend lint/build; SQLite quick/integrity/FK/control/anomaly/distribution queries; clean runtime health/auth/OpenAPI; cached API timing; Docker build attempt.
- Tests not executed: true empty-cache/concurrent runtime timing and successful current Docker image start/inspection remain pending remediation or a safe temporary DB run.
- Known failures: frontend has five hook warnings and a 1.96 MB main chunk; current Docker build failed at `COPY src/` with `no space left on device` after an 8.81 GB context transfer.
- Technical blockers: Docker storage pressure for the legacy broad-copy build. This does not block creation of the corrected data-free image; safe cleanup/rebuild will be handled in Phase 2.
- External blockers: none established.
- Remaining work: final active-DB/audit checksum immutability recheck; validate JSON Schema syntax; review staged baseline; create the dedicated Phase-0 baseline commit.
- Commit hash: pending.
- Next exact action: run final Phase-0 immutability/static checks, update this ledger to complete, stage only immutable audit and remediation baseline documents, review, and commit.

### Phase 1 — Security hardening

- Status: `PENDING`
- Next exact action: begin only after Gate 0 is committed and green.

### Phase 2 — Docker and local PostgreSQL foundation

- Status: `PENDING`

### Phase 3 — PostgreSQL schema

- Status: `PENDING`

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
