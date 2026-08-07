# AWS Migration Plan

Audit date: 2026-07-28  
Recommendation: **hybrid managed architecture, with ECS/Fargate for the API and batch workers, PostgreSQL for operational data, and S3/Parquet/Athena for raw history and analytical workloads.**

This is a plan only. No AWS resource was created and no deployment was attempted.

## Architecture options

| Option | Shape | Advantages | Disadvantages / fit | Verdict |
|---|---|---|---|---|
| A — Lambda-centric serverless | API Gateway + Lambda + Aurora Serverless + Step Functions/Lambda + S3 | Fine-grained scaling, low idle compute, managed integration. | Current 2.1 GB SQLite, 28–68 s synchronous queries, long scrapers, subprocess jobs, local locks and large dependencies are poor Lambda fits. Rework would be high and scraping duration/NAT cost risky. | Do not use as a lift-and-shift. Lambda may serve small control-plane functions later. |
| B — Container-first managed | CloudFront/S3 frontend + ALB/API Gateway + ECS/Fargate API and workers + RDS PostgreSQL + S3 | Closest safe evolution; supports long jobs, controlled resources and normal PostgreSQL connections. | More baseline cost and scaling configuration than Lambda; still requires data/query redesign. | Best near-term operational foundation. |
| C — Data-lake-first analytics | S3 raw/curated Parquet + Glue Catalog + Athena, with a thin API | Cheap durable history, reproducible raw lineage and strong ad-hoc analytics. | Athena is not suitable for every interactive profile write/read, admin mutation or low-latency UI call. | Required analytical complement, not the only serving store. |
| D — Hybrid (recommended) | B plus C: Fargate API/workers, RDS/Aurora PostgreSQL, S3/Parquet/Athena and precomputed caches | Separates operational, batch and analytical workloads; migration can be phased and rolled back. | More components and governance; demands explicit ownership/observability. | Recommended. |

## Recommended target architecture

| Current component | AWS target | Reason |
|---|---|---|
| React/Vite dev server | S3 private origin + CloudFront + WAF | Immutable static deploy, CDN caching and TLS; no frontend runtime container needed. |
| FastAPI BFF | ECS Fargate service behind internal/public ALB or API Gateway, autoscaled | Current API is container-suitable after removing embedded data and blocking I/O. |
| Public authentication | Cognito or enterprise OIDC at ALB/API layer; application RBAC | Admin and write APIs cannot remain anonymous. |
| Admin API | Separate private route/service, OIDC admin role, VPN/internal access if possible | Reduces public attack surface and isolates mutations. |
| Scrapers/imports | One-shot ECS Fargate tasks | Long, dependency-heavy, network-bound jobs do not fit short functions reliably. |
| Scheduling/orchestration | EventBridge Scheduler + Step Functions | Explicit schedules, retries, dependencies, timeouts and run history. |
| Job queue | SQS with DLQ | Durable backpressure and retry isolation for enrichment/profile jobs. |
| Operational DB | RDS PostgreSQL initially; evaluate Aurora only against measured concurrency/availability/cost | Relational constraints, concurrent reads/writes and normal migration tooling. RDS is simpler and predictable at current scale. |
| Raw source/cache files | Versioned S3 buckets/prefixes with KMS and lifecycle | Durable, immutable lineage; removes local filesystem and image coupling. |
| Historical/curated analytics | Partitioned Parquet on S3 + Glue Catalog + Athena | Lower cost for source history, reconciliation and broad scans. |
| Interactive aggregates | PostgreSQL materialized tables/views; optional ElastiCache only after measurement | Overview/map must not scan/rebuild large facts per request. |
| Secrets | Secrets Manager or SSM Parameter Store, KMS, task roles | Removes `.env` from deployed hosts and supports rotation. |
| Logs/metrics/traces | CloudWatch Logs/Metrics/Alarms, structured JSON, OpenTelemetry/X-Ray optional | Request IDs, job counts, quality metrics, errors and latency need durable observability. |
| Images | ECR with multi-architecture manifests, immutable digests and scanning | Current tested image is ARM64-only and 9.37 GB. |
| Delivery | GitHub Actions OIDC to AWS, Terraform or CDK, environment promotion | No static AWS credentials; reviewable and repeatable delivery. |

## Data placement and S3 strategy

Suggested logical prefixes/buckets, split further by account/environment:

```text
s3://fip-<env>-raw/<source>/ingest_date=YYYY-MM-DD/run_id=<id>/...
s3://fip-<env>-validated/<source>/schema_version=<n>/ingest_date=.../...
s3://fip-<env>-curated/<dataset>/fact_version=<n>/award_year=YYYY/...
s3://fip-<env>-exports/<tenant-or-job>/<expiry>/...
s3://fip-<env>-audit/<run_id>/manifests-and-reconciliation-only
```

- Enable versioning, bucket-owner enforcement, block public access, SSE-KMS and least-privilege task roles.
- Store manifests with source URL, retrieval time, HTTP metadata/checksum, schema version, record counts and job ID.
- Raw data is immutable. Corrections produce a new version rather than overwriting evidence.
- Lifecycle transitions raw historical versions to colder storage after an approved period; delete only under an approved retention policy.
- Do not place SQLite files or raw datasets in application images or Git.
- Encrypt exports separately and expire them quickly; avoid caching personal/contact data at CloudFront.

## Athena strategy

Athena is useful for:

- source-to-curated reconciliation and historical audits;
- broad grant trend queries across versions;
- data-quality dashboards and anomaly investigation;
- cost-efficient, infrequent scans of raw/historical datasets.

It is not the primary store for:

- organization profile mutations/link overrides;
- sub-second directory/detail endpoints;
- transactionally consistent admin workflow state;
- request-time map aggregation for every user.

Partition Parquet primarily by dataset, source and award/ingest year/month. Avoid high-cardinality partitions such as individual organization IDs. Compact small files per batch.

## PostgreSQL mapping

| SQLite construct | PostgreSQL design |
|---|---|
| Integer/text primary IDs | Preserve source IDs as text where semantically external; introduce generated surrogate keys only when needed. |
| JSON stored as TEXT | `jsonb` with validation/check constraints, or normalized child tables for queried fields. |
| FTS5 table/triggers | Generated/maintained `tsvector` plus GIN index; benchmark prefix/fuzzy behavior and ranking parity. |
| `INSERT OR REPLACE` | Explicit `ON CONFLICT (...) DO UPDATE`; never rely on delete/reinsert side effects. |
| `strftime` | `date_trunc`, `extract`, typed `date`/`timestamptz`. |
| `COLLATE NOCASE` | `citext`, normalized search columns or ICU collation chosen explicitly. |
| `rowid` | Declared primary key/identity and deterministic ordering. |
| `json_each` / `json_extract` | `jsonb_array_elements`, `->`, `->>` or normalized relationships. |
| SQLite file atomic publish | Load versioned staging schema/tables, validate, then transactional view/table-version switch. |
| `PRAGMA foreign_keys` | Always-enforced constraints; use controlled `NOT VALID`/deferred validation only during bulk load. |
| Local cache table | Versioned materialized aggregate tables, refreshed by worker; optional Redis after measurement. |

Use a migration tool such as Alembic. Every migration must be forward-tested on an empty DB and an anonymized/current-shape snapshot, with a documented rollback or restore strategy.

## Delivery phases and gates

### Phase 0 — Blocker remediation and contracts

Work:

- protect or disable all mutation/admin/proxy routes by default;
- add `.dockerignore`, separate data from image, non-root multi-stage build and pinned Python dependencies;
- define source, retention, privacy, schema and SLO contracts;
- add request IDs, structured job metadata and DB-aware readiness;
- isolate synchronous DB work and precompute overview/map facts.

Gate 0:

- no P0 and no unresolved public-deployment P1 security finding;
- cold primary view p95 target agreed and met on representative data;
- image target agreed (for example under 500 MB without data) and multi-arch build passes;
- retention/privacy/source approvals recorded.

Rollback: code-only; keep current local workflow and active DB unchanged.

### Phase 1 — AWS foundation

Work:

- create separate development/staging AWS account/environment via Terraform/CDK;
- VPC, private subnets, endpoints, KMS, S3, ECR, CloudWatch and budgets;
- GitHub Actions OIDC role with environment protection;
- static frontend deployment and a data-free API container;
- Cognito/enterprise OIDC and private admin plane.

Gate 1:

- IaC plan reviewed; security checks pass; destroy/recreate in non-production is demonstrated;
- no public S3/RDS/ECS admin access; secret rotation path tested;
- image SBOM, dependency and container scans pass policy.

Rollback: revert CloudFront/API origin to the existing non-AWS environment; destroy only tagged non-production stacks through IaC.

### Phase 2 — S3 ingestion and worker orchestration

Work:

- write raw/validated outputs to versioned S3;
- run one source at a time in ECS tasks through Step Functions;
- persist run manifests, watermarks, counts, checksums and validation results;
- SQS/DLQ for enrichment work; alarms for stale/failed runs.

Gate 2:

- repeated run is idempotent;
- failure preserves last approved dataset;
- source count/checksum and retry simulations pass;
- lifecycle is approved but destructive expiration remains disabled until sign-off.

Rollback: stop schedules, retain S3 versions, return pipeline reads to local/cached inputs.

### Phase 3 — PostgreSQL schema and bulk migration

Work:

- provision RDS PostgreSQL in private subnets with backups/PITR;
- implement Alembic schema and loader;
- migrate base organizations/grants, then child facts/evidence, registry/search, overrides/cache metadata;
- create serving indexes/materializations and query limits.

Gate 3 — mandatory reconciliation:

1. Table row counts equal expected counts.
2. Distinct grant/source IDs and registry IDs match.
3. FK/orphan checks return zero unexpected rows.
4. Duplicate/anomaly cohorts equal signed baselines.
5. Control totals match: 302,546 grants; EUR 22,435,986,707.70 under implemented non-negative semantics; 104,191 mapped; 134,554 classified.
6. API golden fixtures match or have approved documented differences.
7. Search quality and query plans meet budgets.
8. Backup restore to a fresh instance succeeds.

Rollback: keep source DB read-only snapshot and last approved PostgreSQL snapshot; point application feature flag back to SQLite/local environment. Do not discard source artifacts.

### Phase 4 — Shadow read and performance validation

Work:

- send representative read queries to both backends and compare asynchronously;
- exercise dashboard, directory, map, filters, drill-down, profiles, Sankey, score and news;
- load test concurrency and failure modes;
- tune indexes/materializations and autoscaling.

Gate 4:

- zero unexplained control-total or entity-identity differences;
- p95/p99 and error-rate SLOs pass sustained representative load;
- no request blocks unrelated health/readiness requests;
- security/DAST and accessibility/E2E suites pass.

Rollback: disable PostgreSQL read flag and continue shadow diagnostics.

### Phase 5 — Staging cutover

Work:

- stop staging writers, take final coherent snapshot, load/validate delta;
- switch staging API to PostgreSQL and S3;
- exercise operator runbooks, alerts, DLQ replay and restore.

Gate 5:

- 72-hour stability window or agreed equivalent;
- on-call ownership, dashboards, alarms and runbooks accepted;
- recovery objectives demonstrated.

Rollback: switch DB/origin feature flags back, preserve failed target for forensic comparison, replay no writes until reconciliation.

### Phase 6 — Production preparation and cutover

Work:

- repeat snapshot/delta/reconciliation with change freeze;
- progressive traffic shift/canary;
- monitor totals, p95/p99, 4xx/5xx, queue age, job failures and cost.

Gate 6:

- production security/privacy approval;
- rollback practiced; PITR and object recovery demonstrated;
- no open P0/P1; operational acceptance signed.

Rollback: weighted traffic back to previous origin, stop new writers, restore/reconcile from the last known-good point. Never attempt bidirectional replay without a documented conflict policy.

## CI/CD target gates

Pull requests should run:

1. backend compile, blocking lint/type checks, unit/integration tests and coverage;
2. frontend lint with warnings treated by policy, unit tests and production build;
3. Playwright E2E on representative seed data;
4. secret scan, SCA, licence/SBOM, SAST and container/IaC scan;
5. data migration on empty and snapshot-shaped PostgreSQL;
6. Docker multi-arch build with image-size and non-root assertions;
7. Terraform/CDK format, validation, policy and plan.

Staging promotion adds reconciliation, smoke/E2E, load, DAST, restore and rollback tests. Production promotion requires protected approval, immutable image digest and successful staging evidence.

## Observability and alarms

- Structured fields: `request_id`, `job_id`, `source`, `dataset_version`, `schema_version`, `record_count`, `retry`, `duration_ms`, `status`, `error_class`.
- Metrics: API p50/p95/p99, errors, DB pool/query duration, cache hit, overview refresh age, source watermark age, records accepted/rejected/quarantined, queue age, DLQ depth, ECS task failures and spend.
- Alarms: readiness failures, 5xx/error budget, latency, stale source, reconciliation mismatch, missing conversion spike, quality threshold breach, DLQ > 0 and budget anomaly.
- Redact query strings, authorization headers, article bodies, emails and addresses from general logs.

## Work required before AWS begins

The migration should not begin as a deployment exercise until Gate 0 is met. Architecture/IaC prototyping with synthetic data is acceptable, but no public or production dataset should be exposed. Required first actions:

1. OIDC/RBAC and private admin/control plane; disable anonymous writes/proxy.
2. Data-free, non-root, slim multi-arch image and reproducible dependency lock.
3. Fix event-loop blocking and meet a representative dashboard/map p95 budget.
4. Approve retention, privacy, source/licence and data ownership contracts.
5. Define PostgreSQL schema, reconciliation controls and reversible loader.
6. Add CI security/migration/container gates and DB-aware readiness/observability.
