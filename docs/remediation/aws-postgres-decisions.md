# AWS/PostgreSQL Architecture Decisions

Created: 2026-07-28

Status: active architecture contract through Phase 1; implementation evidence is recorded separately.

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

## Open external decisions

- OIDC issuer, audiences, role/group claims and identity owner.
- AWS accounts, region, DNS zone/domain and certificate ownership.
- Approved retention periods, legal/licence status and data owners.
- Staging/production RTO, RPO, availability tier and cost ceiling.

Secure configuration interfaces and deny-by-default behavior will be implemented without fabricating these approvals.
