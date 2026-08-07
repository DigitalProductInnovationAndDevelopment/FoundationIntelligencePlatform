# Temporary AWS public read-only demo

Status: design and read-only preflight snapshot prepared on 2026-08-06. This is
a temporary non-production demo design. Its HTTP listener is not
production-ready and must never transport login cookies, tokens or credentials.
This document describes controlled phases; it is not evidence that a particular
phase has or has not been executed.

## Confirmed read-only preflight

- AWS profile: `netlight`
- Account: `208337080387` (exact match)
- Region/configured region: `eu-west-1` (exact match)
- Default VPC: `vpc-0348ad96c2afcf8dd`, `172.31.0.0/16`
- Internet gateway: `igw-08ecd35c2bbf38c46`
- Public ALB/ECS subnets:
  - `subnet-04d4e3e5af84a7ea4`, `eu-west-1a`, `172.31.16.0/20`
  - `subnet-076f66fcdd86cd28f`, `eu-west-1b`, `172.31.32.0/20`
  - spare confirmed public subnet: `subnet-0f5b48470ee59b581`,
    `eu-west-1c`, `172.31.0.0/20`
- All three default subnets map public IPs and inherit the active
  `0.0.0.0/0 -> igw-08ecd35c2bbf38c46` route.
- No isolated RDS subnets exist. The template therefore creates
  `172.31.240.0/28` in `eu-west-1a` and `172.31.240.16/28` in `eu-west-1b`,
  associated only with a new route table whose sole route is VPC-local.
- Existing ECR repositories:
  - `foundation-intelligence-platform/backend`
  - `foundation-intelligence-platform/frontend`
- Both repositories currently contain zero images. The public AWS CLI
  `2.27.41` manifest was checked and contains both linux/amd64 and linux/arm64.
- PostgreSQL `16.14`, gp3, `db.t4g.medium` and `db.t4g.small` are orderable in
  `eu-west-1a`, `eu-west-1b` and `eu-west-1c`.
- No matching CloudFormation stacks, ECS clusters, RDS instances, ALBs,
  Secrets Manager secrets, CloudWatch log groups, S3 buckets, IAM roles,
  schedules or security groups were found.
- `iam:SimulatePrincipalPolicy` was denied for the current user. Per the
  requested contract the IAM result is `UNKNOWN`; no create call was used as a
  permission test.

The existing ECR repositories are mutable-tag repositories with AES256
encryption and scan-on-push. Deployment commands must use unique Git-SHA tags
and resolve the resulting image digests before the stack is updated; `latest`
is prohibited by the template input contract.

## Resource and sizing contract

The template is `infra/cloudformation/demo.yaml`.

| Component | Exact demo configuration |
|---|---|
| Application task | Fargate Linux/ARM64, 512 CPU units, 2,048 MiB, default 20 GiB ephemeral storage, desired count 0 initially then 1 |
| Frontend | Same task, unprivileged port 8080 behind the ALB listener on port 80; Nginx proxies `/api` to `127.0.0.1:8000` |
| Backend | Same task, port 8000 not exposed by any security-group ingress rule |
| Migration task | Fargate Linux/ARM64, 1,024 CPU units, 4,096 MiB, default 20 GiB ephemeral storage, one-off only |
| RDS import | PostgreSQL 16.14, `db.t4g.medium`, Single-AZ, 30 GiB gp3, autoscaling maximum 100 GiB |
| RDS steady state | Change the same stack parameter to `db.t4g.small` after import and loaded smoke tests |
| RDS safety | private/isolated subnets, public access false, encrypted storage, forced TLS, one-day backup retention, snapshot on delete/replace |
| ALB | internet-facing, two public subnets, HTTP port 80, IP targets, `/health/live` |
| S3 | private, bucket-owner enforced, AES256, full public-access block, TLS-only policy, import objects expire after seven days |
| Logs | separate frontend, backend and migration log groups; seven-day retention |

There is no NAT gateway, autoscaling, Multi-AZ database, replica, Performance
Insights or VPC endpoint. The application task receives a public IP solely for
outbound AWS/API access. Its inbound port 8080 accepts traffic only from the ALB
security group. RDS port 5432 accepts traffic only from the shared ECS security
group.

The demo uses asyncpg's `ssl=require` mode for every PostgreSQL connection. It
encrypts transport to RDS but does not verify the server identity. Production
hardening should use `verify-full` with the AWS RDS CA certificate after that
mode and certificate distribution are added to the runtime contract.

The RDS-managed master secret is injected only into the one-off migration task.
CloudFormation generates a second application secret. The migration bootstrap
applies Alembic and runs the existing migration as the master, then
creates/rotates `foundation_app` without superuser, role-creation or DDL
capabilities. The application role receives only database connect, schema usage
and table `SELECT`; its transactions default to read-only. Public application
startup also skips configuration synchronization and database-backed audit or
idempotency writes. The runtime task never receives the master secret.

## Source and migration gate

The approved local source was independently rechecked:

- path: `src/data/charities.db`
- size: `2,100,543,488` bytes
- SHA-256: `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`
- source schema version: `7`
- SQLite quick check: `ok`
- PostgreSQL Alembic head: `0006_governance_retention`

The migration task has three ordered containers:

1. An AWS CLI init container downloads exactly `imports/charities.db` from the
   stack's private S3 bucket into the shared 20 GiB task volume, creates the
   evidence directory and hands the volume to the non-root migration UID.
2. `migration.aws_entrypoint` acquires a PostgreSQL advisory lock, applies
   Alembic and invokes the existing `migration.sqlite_to_postgres.migrate`
   workflow with the master secret, then configures the SELECT-only application
   role and verifies the release gate through that role. SHA-256 and source
   schema are checked before loading. The remote-RDS capacity mode counts only
   the source plus a 4 GiB local safety margin; PostgreSQL data and WAL remain
   on the 30–100 GiB RDS volume and are not incorrectly counted against Fargate.
3. Only after migration success, a final AWS CLI container uploads the generated
   reconciliation evidence to `migration-evidence/<dataset-version>/`.

The existing loader stages a versioned candidate, validates types and foreign
keys, builds analytics materializations, reconciles baseline counts and controls,
and activates in a transaction only if every reconciliation passes. On any
failure the candidate is failed/rejected and the previous active dataset remains
unchanged. The release gate must also report ready before the task succeeds.

## Controlled phase sequence

Values such as the reviewed Git SHA and created bucket/ARNs must be resolved at
execution time; they must not be guessed.

1. Review, then locally commit only the listed repository changes. Do not push
   to GitHub during Phase A.
2. Build `backend-runtime` and `frontend-runtime-ecs` for `linux/arm64`.
3. Authenticate Docker to the two existing private ECR repositories, tag each
   image once with `demo-<full-git-sha>`, push, then read back and use immutable
   image digests. No ECR repository creation is planned.
4. Create, inspect and stop at a CloudFormation change set using
   `CAPABILITY_IAM`, the reviewed image digests, full Git SHA,
   `ApplicationDesiredCount=0` and `DatabaseInstanceClass=db.t4g.medium`.
   Do not execute the change set during Phase A.
5. In a separately approved later phase, execute the reviewed change set. Read
   the generated `ImportBucketName`, then upload exactly
   `src/data/charities.db` to `s3://<bucket>/imports/charities.db`, with the
   approved SHA-256 recorded as object metadata, then read back object metadata.
6. Run exactly one `MigrationTaskDefinition` task in the two confirmed public
   subnets with `AssignPublicIp=ENABLED` and the stack ECS security group. Wait
   for all containers to exit zero and inspect the migration logs/evidence.
7. Keep the web service at zero until release-gate, readiness and loaded API
   smoke checks pass. Then execute a second reviewed stack update setting only
   `ApplicationDesiredCount=1`.
8. After import and loaded browser/API tests pass, execute a third reviewed
   stack update changing RDS from `db.t4g.medium` to `db.t4g.small`. Do not use
   `ApplyImmediately`; inspect the planned maintenance behavior first.

S3 upload, change-set execution, CloudFormation deployment, ECS `RunTask`, RDS
modification, GitHub push and resource deletion are outside Phase A.

## Automatic refresh plan — intentionally not scheduled

The repository currently has solid versioned migration, reconciliation,
transactional activation, durable PostgreSQL job/outbox/lease primitives and
source limits. It does not yet have a production executable that connects the
current filesystem-oriented scraper/consolidation commands to immutable S3 and
a PostgreSQL candidate dataset. All source configurations are also disabled and
governance-blocked with unresolved legal/licence status. Creating a task or
schedule that pretends otherwise would not be fail-closed.

After source governance approval, implement and manually validate this separate
ARM64 Fargate refresh task; the web service must never run it in the background:

`EventBridge Scheduler (disabled initially)` → `ECS Refresh Task` → acquire
PostgreSQL advisory lock → fetch bounded approved sources into immutable S3 keys
→ validate schema/checksum and deduplicate → build a new versioned candidate →
reconcile counts/quality/materializations → transactionally activate → release
lock.

- Proposed task: 1,024 CPU units, 4,096 MiB, default 20 GiB ephemeral storage,
  public subnet/public IP for bounded source egress, no inbound rule.
- Parallel runs: `pg_try_advisory_lock` plus the durable job lease/unique
  idempotency key; a second run exits without scraping or changing data.
- Failure isolation: source and candidate identifiers are immutable/versioned;
  active-dataset pointers change only in the final successful transaction.
- Retry: task-level maximum two retries with exponential backoff for transient
  ECS/source failures; no retry for validation, governance or reconciliation
  failures; CloudWatch records class/counts without raw payloads or credentials.
- Initial scheduler state: absent/disabled until a manual AWS refresh succeeds.
- Later weekly default: `cron(0 2 ? * MON *)` in UTC, maximum event age one hour,
  maximum two delivery retries. Enable only in a separately reviewed stack
  update after the manual gate.

## Monthly cost estimate

The estimate uses the AWS Price List API values returned for EU (Ireland) on
2026-08-06, 730 hours/month, on-demand pricing and a low-traffic assumption of
0.1 average ALB LCU. It excludes VAT, support, internet egress and unexpected
traffic. CloudWatch assumes 1 GiB/month of logs; ECR assumes roughly 2 GiB of
images. Public IPv4 assumes two ALB addresses plus one running application task.

| Item | Initial import month | After RDS resize |
|---|---:|---:|
| RDS instance | $50.37 (`db.t4g.medium`) | $25.55 (`db.t4g.small`) |
| RDS 30 GiB gp3 | $3.81 | $3.81 |
| Fargate app 0.5 vCPU / 2 GiB | $17.02 | $17.02 |
| ALB hours | $18.40 | $18.40 |
| ALB 0.1 average LCU assumption | $0.58 | $0.58 |
| Three public IPv4 addresses | $10.95 | $10.95 |
| Two Secrets Manager secrets | $0.80 | $0.80 |
| CloudWatch, ECR and temporary S3 estimate | $0.78 | $0.78 |
| **Estimated total** | **about $103/month** | **about $78/month** |

The one-off 1 vCPU/4 GiB migration task is approximately $0.04662 per task-hour
plus one public IPv4 at $0.005/hour; even a six-hour import adds less than $0.32.
At one full ALB LCU instead of 0.1, add approximately $5.26/month. Internet data
transfer and request-driven load remain variable and require a budget/alarm
before any broader demo distribution.

## Hardening after the temporary demo

Before any production claim or use of login credentials, add an approved domain,
ACM certificate and HTTPS listener; redirect HTTP to HTTPS; replace the public
read-only mode with OIDC; add edge abuse protection/budgets; complete privacy and
source-governance approval; test restore/rollback; and reassess Multi-AZ, backups,
deletion protection and private ECS networking. None of those production claims
apply to the temporary HTTP demo.
