# AWS/PostgreSQL Architecture Decisions

Created: 2026-07-28

Status: active architecture contract through Phase 2; implementation evidence is recorded separately.

## ADR-001 — PostgreSQL is the operational database

- Decision: PostgreSQL 16-compatible is the only normal staging/production runtime database.
- Application access: SQLAlchemy 2.x async sessions with `asyncpg`, bounded pools and explicit transactions.
- SQLite allowance: read-only migration/reconciliation source and explicit legacy fixtures only.
- Consequence: production configuration fails closed without PostgreSQL; runtime architectural tests prohibit `sqlite3` imports outside migration/legacy tooling.

## ADR-002 — ECS Fargate for API and batch workloads

- Decision: run the FastAPI API as an ECS Fargate service and long-running ingestion/enrichment jobs as one-shot Fargate tasks.
- Rationale: current query/job duration, subprocess behavior and source dependencies are poor Lambda fits.
- Serverless-container exception: managed orchestration/storage is preferred, while long-lived or resource-heavy compute uses containers.

## ADR-003 — Separate serving, raw and analytical storage

- Decision: PostgreSQL serves transactional and interactive reads/writes. S3 stores immutable raw/validated/curated versions. Parquet/Glue/Athena supports historical reconciliation and broad analytical scans.
- Constraint: Athena does not serve interactive profiles, admin/job state or transactional overrides.

## ADR-004 — Preserve entity roles

- Decision: source funders, grant recipients, curated profiles, registry organisations and registry constituent records keep explicit keys and relationship tables.
- Consequence: migration cannot collapse them into a generic organisation identity or delete apparent duplicates without a separate governed correction.

## ADR-005 — Versioned materialisations

- Decision: overview, map/country facts, funder/recipient rankings, programme allocation, filter vocabularies and monthly/yearly trends are dataset-versioned materialisations.
- Activation: build and reconcile a candidate dataset, then transactionally activate one version. A failed build cannot replace the last approved version.
- Cache keys include the active dataset/materialisation version.

## ADR-006 — OIDC and default-deny RBAC

- Decision: OIDC/Cognito-compatible JWT validation with viewer, analyst, operator and administrator roles.
- Public surface: explicitly classified read-only routes only.
- Control plane: admin/mutation/proxy/external-cost routes require roles and immutable audit events.
- Development bypass: explicit, local-only and prohibited in staging/production.
- Missing production auth configuration causes startup/readiness failure.
- Implementation: asymmetric OIDC algorithms, issuer, audience, expiry, subject and JWKS key ID are verified. No application shared password or symmetric production signing secret remains.
- Browser contract: production supplies a bearer token through the selected OIDC client/gateway pattern; only explicit local development may use the HttpOnly session cookie.

## ADR-007 — Durable jobs and source ingestion state

- Decision: PostgreSQL stores job/run/dataset state. SQS/DLQ supplies durable work delivery; Step Functions/EventBridge definitions orchestrate/schedule; Fargate workers execute.
- API manual refresh enqueues an idempotent job and returns a job ID; it never runs a large subprocess in the request lifecycle.
- Local filesystem locks/status/logs are not a production coordination mechanism.

## ADR-008 — Migration activation and rollback

- Decision: read SQLite in immutable/read-only mode, load versioned PostgreSQL staging structures efficiently, reconcile mandatory controls, and activate transactionally.
- Rollback: preserve the prior approved PostgreSQL dataset and coherent SQLite baseline during the validation window. Do not attempt bidirectional replay without an approved conflict policy.

## ADR-009 — Retention defaults

- Decision: destructive retention is disabled. Initial operation is dry-run/report-only with holds overriding every deletion candidate.
- Production activation requires approved durations, owners, legal/privacy review, restore verification and an audit manifest.

## ADR-010 — Terraform and cloud execution boundary

- Decision: Terraform is the single AWS IaC system for reusable development/staging definitions.
- Local scope: formatting, static/security validation and non-destructive plan when possible.
- Absolute boundary: no apply/destroy/import/remote-state mutation/AWS change/paid call without later explicit user approval.

## ADR-011 — Frontend delivery and behavior

- Decision: production frontend is a Vite static build for a private S3 origin behind CloudFront/WAF; no Vite dev server in production.
- Preserve the current visual identity and map-first structure while fixing responsiveness, accessibility, loading isolation, request duplication, stable keys and bundle size.

## ADR-012 — Layered abuse controls and idempotency

- Decision: application request IDs, payload/time bounds, per-actor rate limits and at-most-once mutation keys are mandatory even when edge controls are present.
- Local transition: process-local stores are permitted only while the application runs as one local task during remediation.
- Production target: WAF/API edge limits provide distributed abuse protection; PostgreSQL supplies durable idempotency records and security audit events before ECS scales past one task.
- Retry safety: a server failure/timeout retains the idempotency reservation because the side-effect outcome may be uncertain; validation/client failures release it for a corrected request.

## ADR-013 — Reproducible dependency and image supply chain

- Decision: direct Python inputs are exact pins; generated transitive locks carry accepted SHA-256 hashes and every installation/build uses `--require-hashes`.
- Decision: npm installs use the committed lockfile, `npm ci` and disabled lifecycle scripts. Build-stage development tools never enter the static Nginx runtime.
- Decision: the Dockerfile frontend and every base image use a concrete version plus a Docker Hub manifest-list digest.
- Network boundary: dependency resolution is limited to PyPI/files.pythonhosted.org, registry.npmjs.org and the Docker Hub registry/auth endpoints explicitly authorized on 2026-07-29.
- Multiarch: `linux/amd64` and `linux/arm64` are declared in one bake contract; CI must assemble/test both because the current local CLI has no working buildx plugin.

## ADR-014 — Dataset-scoped relational schema and PostgreSQL search

- Decision: serving rows use composite dataset/source keys so candidates and the prior approved dataset coexist; one partial unique index controls active selection.
- Decision: external grant, registry, source and dataset identifiers stay textual where their source owns identity. Internal UUIDs are limited to runs/events/actions/jobs.
- Decision: normal staging/production import selects PostgreSQL-only async repositories. Unported routes are absent until Phase 5 rather than using a hidden SQLite fallback.
- Search: stored `tsvector` plus GIN and `pg_trgm` indexes; rounded rank descending and registry ID ascending form the stable cursor order.
- Integrity: PostgreSQL FKs/checks are server-enforced, audit rows are append-only and override revisions must increment exactly once per update.

## ADR-015 — Lossless temporal migration and atomic activation

- Decision: migration preserves the source representation when it carries more precision than a normalized analytical field. Raw grant award values remain ISO date/timestamp text and ECB reference periods remain `YYYY-MM`; normalized fact dates stay typed separately.
- Decision: global exchange rates and operator overrides are staged in memory and written only inside the candidate activation transaction. A rejected, interrupted or quarantined candidate cannot change global operational state.
- Decision: every full migration report includes a catalog-driven anti-join result across all declared PostgreSQL foreign keys, in addition to relying on validated server constraints.
- Consequence: apparent duplicate-count drift caused by truncating source timestamps is a schema defect, not an approved data correction. The schema must preserve the original business key before reconciliation can pass.

## ADR-016 — Domain-sized async repositories and queue-only control plane

- Decision: production/staging organization, registry, grant analytics, source-funder, job and audit journeys use separate async PostgreSQL repositories behind small protocol interfaces. A single cross-domain repository is prohibited.
- Decision: the 25 pre-existing organization/grant method-and-path contracts remain stable while their production implementation changes. Route parity is an executable contract.
- Decision: application mutations own explicit transaction boundaries. Source-funder override revisions are locked and incremented in the same transaction that invalidates profile cache state.
- Decision: manual refresh and enrichment HTTP requests only create idempotent `job_runs` plus structured `job_events`; they never start a scraper or local subprocess. Job execution and delivery semantics belong to Phase 8.
- Decision: production security audit events are awaited and persisted to the append-only PostgreSQL table with actual HTTP status. Development/test may use structured-log or memory sinks without creating a production fallback.
- Consequence: production startup selects PostgreSQL application/admin routers before legacy modules can import and fails when PostgreSQL connection settings are incomplete. Legacy SQLite modules remain test/migration compatibility code, not a production route.

## Open external decisions

- OIDC issuer, audiences, role/group claims and identity owner.
- AWS accounts, region, DNS zone/domain and certificate ownership.
- Approved retention periods, legal/licence status and data owners.
- Staging/production RTO, RPO, availability tier and cost ceiling.

Secure configuration interfaces and deny-by-default behavior will be implemented without fabricating these approvals.
