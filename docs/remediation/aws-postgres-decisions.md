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

## ADR-017 — Dataset-versioned serving aggregates and lazy relationships

- Decision: the unfiltered dashboard reads transactionally refreshed, dataset-versioned scope, country, period, programme, entity and country-funder aggregates. Filtered requests continue to query versioned facts so an aggregate is never silently applied outside its declared dimensions.
- Decision: country-to-country and funder-to-recipient relationships are independent secondary journeys. Country connections are capped at 250 rows; each funder stores only its top 50 recipients and returns at most 25 in detail. The primary dashboard never waits for either relationship graph.
- Decision: the small application cache is TTL- and size-bounded, uses one in-flight loader per key, returns defensive copies and prefixes every key with the active dataset version. Dataset activation therefore invalidates by identity without mutable global flush coordination.
- Decision: candidate migration builds all aggregates and the materialization control row in the same transaction that activates the candidate. Rollback refuses an unapproved target and creates a missing target materialization before changing active status.
- Performance contract: local gates measure repository and authenticated production-mode API p50/p95/p99, cold dashboard p95, concurrent throughput/error count, cache hit ratio, pool recovery, timeout/cancellation and index-backed `EXPLAIN ANALYZE` plans. Performance claims are evidence, not substitutes for later production telemetry.
- Consequence: PostgreSQL remains the source of truth; the cache is disposable and no response depends on filesystem state or an external cache service.

## ADR-018 — Transactional job outbox and PostgreSQL worker leases

- Status: accepted and implemented locally in Phase 8.
- Decision: every API-triggered job, initial event and queue envelope are committed atomically in PostgreSQL. Workers claim with row locks, maintain bounded leases/heartbeats and persist retry/dead-letter transitions. Staging/production request idempotency also uses PostgreSQL.
- Reason: horizontally scaled API and worker tasks cannot coordinate reliably through filesystem locks or process-local dictionaries, and direct queue publication cannot be atomic with a database write.
- Consequences: a separate dispatcher publishes the durable outbox envelope to SQS by job-ID deduplication. The API never starts pipeline subprocesses. Actual SQS/DLQ execution remains deployment-gated and unclaimed.

## ADR-019 — Immutable source objects and fail-closed schedules

- Status: accepted and implemented locally in Phase 8.
- Decision: raw/validated/curated/export objects have versioned descriptors and checksums; raw descriptors and ingestion manifests are immutable. Source schedules are configuration-as-code and cannot be enabled while legal/licence status is unresolved or governance-blocked.
- Reason: reproducible ingestion requires byte identity, watermarks and counts, while unknown source rights must never be converted into implicit approval.
- Consequences: corrections create new objects/manifests. All current schedules remain disabled until owners approve governance. S3 is the production adapter, but no AWS object operation is part of this decision's local evidence.

## ADR-020 — Non-destructive, hold-aware retention baseline

- Status: accepted and implemented locally in Phase 9.
- Decision: retention policies are configuration-as-code by classification; archive windows create reports only. Destructive activation is globally false, delete windows are unset and there is no delete endpoint or worker.
- Decision: legal and incident holds override every candidate. Any future deletion must additionally reference successful immutable restore evidence, administrator approval and an approved policy revision.
- Reason: owner, legal, licence, privacy, backup-retention, RTO and RPO decisions remain unresolved; silently choosing a 12-month delete policy would be unsafe and unauthorised.
- Consequences: initial PostgreSQL constraints accept only dry-run report/archive manifests. Enabling deletion requires an explicit reviewed migration/configuration change plus production approval.

## ADR-021 — Explicit exposure and recursive redaction

- Status: accepted and implemented locally in Phase 9.
- Decision: typed response models or named allowlists define every exposed field; unknown policies fail. Generic job/event/source dictionaries never serialize all columns.
- Decision: structured values are recursively redacted before output, including credentials, connection strings, email/postal fields, raw payloads and article bodies. Plain text receives credential and email pattern redaction.
- Consequences: new generic serializers require an exposure-policy update and tests. Raw operational evidence stays internal even when its parent record is readable.

## ADR-022 — Independent readiness and versioned observability contract

- Status: accepted and implemented locally in Phase 10.
- Decision: liveness never waits for PostgreSQL or analytics. Readiness uses an independent `NullPool` PostgreSQL engine and one bounded query for schema, active dataset, synchronized governance configuration and durable queue state.
- Decision: structured application/worker logs are redacted JSON with route templates and pseudonymous actor IDs. Metric/alarm definitions and runbook links are versioned configuration; local process snapshots are bounded and administrator-only.
- Reason: exhausted analytical connections must not hide process viability or prevent an orchestrator from making a readiness decision, and high-cardinality or sensitive data must not enter operational telemetry.
- Consequences: Phase 11 may map the definitions to CloudWatch, but no deployed metric, alarm, dashboard or notification is claimed until AWS execution is separately authorized and evidenced. Threshold and owner approval remains an explicit production blocker.

## ADR-023 — Isolated Terraform environments and fail-closed cloud controls

- Status: accepted and implemented as unexecuted definitions in Phase 11.
- Decision: dev and staging instantiate one platform module with separate CIDRs, size/availability/cost settings and no shared state declaration. Development chooses one NAT; staging chooses one per AZ plus interface endpoints. Neither environment enables schedules or DNS by default.
- Decision: application data uses private bucket-owner-enforced KMS buckets without destructive lifecycle expiration. PostgreSQL is private, encrypted, PITR-backed, deletion-protected and protected from Terraform destroy. ECS uses private IPs, digest-only images, non-root/read-only containers and RDS-managed runtime credentials.
- Decision: GitHub trust is OIDC-only and pins repository environment subject plus audience. Artefact deployment rights are distinct from infrastructure bootstrap/state ownership; AWS credentials are never Terraform variables.
- Reason: environment isolation and deny-by-default switches preserve deployability without fabricating DNS, owner, secret, legal/source schedule or cloud execution approval.
- Consequences: the absent Terraform/provider/scanner toolchain blocks fmt/validate/plan evidence but not independent CI/CD definition work. Apply, state, DNS and AWS execution remain prohibited.

## Open external decisions

- OIDC issuer, audiences, role/group claims and identity owner.
- AWS accounts, region, DNS zone/domain and certificate ownership.
- Approved retention periods, legal/licence status and data owners.
- Staging/production RTO, RPO, availability tier and cost ceiling.

Secure configuration interfaces and deny-by-default behavior will be implemented without fabricating these approvals.
