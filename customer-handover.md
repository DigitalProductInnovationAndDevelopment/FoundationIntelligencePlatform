# Foundation Intelligence Platform — customer handover

This document is the operational and technical handover for the Foundation Intelligence
Platform proof of concept. It describes the code on `dev` at commit
`689de0d59ff5742def497662d8a69472f7a4e851`, which is also the starting point of the
`97-fix-documentation` branch.

The repository contains older audit and remediation notes written at different stages of the
project. They remain useful as evidence, but they are not all descriptions of the current
system. Where those notes disagree with this handover, check the executable configuration and
the implementation linked from this document before taking action.

## Contents

1. [Purpose and delivery status](#1-purpose-and-delivery-status)
2. [Current operational snapshot](#2-current-operational-snapshot)
3. [Architecture](#3-architecture)
4. [AWS demo environment](#4-aws-demo-environment)
5. [Application structure](#5-application-structure)
6. [Authentication and access control](#6-authentication-and-access-control)
7. [Data and domain rules](#7-data-and-domain-rules)
8. [Product and API capabilities](#8-product-and-api-capabilities)
9. [Jobs, workers and profile hydration](#9-jobs-workers-and-profile-hydration)
10. [Latest-news research](#10-latest-news-research)
11. [Local development](#11-local-development)
12. [Testing and quality gates](#12-testing-and-quality-gates)
13. [Deployment and release management](#13-deployment-and-release-management)
14. [Operations and troubleshooting](#14-operations-and-troubleshooting)
15. [Terraform and CI/CD](#15-terraform-and-cicd)
16. [Security, governance and known limitations](#16-security-governance-and-known-limitations)
17. [Handover checklist](#17-handover-checklist)
18. [Sources of truth](#18-sources-of-truth)

## 1. Purpose and delivery status

The platform is a proof of concept for exploring foundations, charities and observed grant
relationships. It brings together cached Charity Commission for England and Wales records,
cached 360Giving transactions and cached Philea member records. Its strongest coverage is UK
organization and grant data. DACH and wider European coverage is partial and mostly limited to
organization-level information.

The product supports:

- organization and charity-registry search;
- donor and recipient exploration;
- grant trends, programme allocation and beneficiary geography;
- source-funder profiles and evidence;
- deterministic programme and geography enrichment;
- an experimental, explainable relevance score;
- operator-triggered background work; and
- operator-only news research with a sourced summary when the external services are available.

It is not a predictive fundraising product. Missing source data is not treated as zero, and a
missing grant record must not be presented as evidence that an organization has never funded a
topic or location.

### Delivery position

| Area | Position at handover |
|---|---|
| Local application | Implemented for PostgreSQL and Docker Compose |
| AWS customer demo | Deployed as a non-production CloudFormation stack |
| Authentication | Cognito Managed Login with application RBAC |
| Background worker | Deployed in the application ECS task and backed by PostgreSQL leases |
| Production deployment | Not approved; the production workflow is deliberately disabled |
| Terraform platform | Intended staging/production design, not the infrastructure used by the demo |
| Monitoring | Container logs and health checks exist; full CloudWatch alarms and dashboards do not |
| Source schedules | Disabled and governance-blocked in the committed configuration |
| Destructive retention | Disabled |
| Load and availability evidence | Insufficient for a production claim |

The practical status is therefore:

- **customer demo:** available, subject to the dated checks below;
- **production use:** `NO-GO` until the security, monitoring, governance, resilience and release
  gaps in this document are resolved.

## 2. Current operational snapshot

This table records observations, not permanent configuration. Update the date whenever the
environment changes.

| Item | Last verified | Observation |
|---|---|---|
| Repository baseline | 14 August 2026 | `dev` at `689de0d`; includes the AWS work merged from `3c9d290` |
| Customer URL | 14 August 2026 | `https://d12pqv690k01m6.cloudfront.net/` returned HTTP 200 |
| Browser authentication config | 14 August 2026 | `/api/auth/config` returned `cognito_rbac` in `eu-west-1` |
| CloudFormation and ECS control plane | 13 August 2026 | Stack was `UPDATE_COMPLETE`; ECS service had one running task using application task definition revision 15 |
| Containers | 13 August 2026 | Backend and frontend were healthy; worker was running |
| Load balancer | 13 August 2026 | Target group reported healthy |
| Profile hydration | 13 August 2026 | One production hydration completed and its cache reached `ready` |

The AWS command-line session available during the 14 August documentation review had expired,
so the control-plane rows were not rechecked that day. The public CloudFront and authentication
configuration checks were repeated successfully. Do not read this snapshot as a substitute for
a fresh deployment check.

### Environment identifiers

| Item | Value |
|---|---|
| AWS account | `208337080387` |
| Region | `eu-west-1` |
| AWS CLI profile used during delivery | `netlight` |
| Main stack and ECS cluster | `foundation-intelligence-demo-e06ab1ea-v2` |
| ECS service | `application` |
| ECS task family | `foundation-intelligence-demo-e06ab1ea-v2-application` |
| Customer URL | `https://d12pqv690k01m6.cloudfront.net/` |
| Cognito user pool | `eu-west-1_ZdE7jvmHa` |
| Cognito browser client | `bpi62ujt3tl14kpsjsbci169` |
| Cognito managed-login domain | `https://foundation-intelligence-e06ab1ea-v2-08219dba.auth.eu-west-1.amazoncognito.com` |

The user-pool and browser-client identifiers are returned by the public authentication-config
endpoint and are not credentials. Passwords, tokens, database connection strings and secret
values must never be added to this document.

## 3. Architecture

### Deployed customer-demo path

```text
Browser
  │
  │ HTTPS
  ▼
CloudFront default domain
  │
  │ HTTP port 80; custom origin-verification header
  ▼
Internet-facing Application Load Balancer
  │
  │ HTTP to port 8080; security-group restricted
  ▼
One ECS Fargate application task (Linux/ARM64, awsvpc)
  ├── frontend: unprivileged nginx on 8080
  │       └── /api and /health proxy to 127.0.0.1:8000
  ├── backend: FastAPI on 8000, not directly exposed
  └── worker: PostgreSQL-backed durable job consumer
          │
          ├── private RDS PostgreSQL over TLS
          └── private, versioned S3 import/snapshot bucket over HTTPS
```

CloudFront, the ALB, the ECS service, RDS, Cognito, the S3 bucket, IAM roles, Secrets Manager
references and log groups are defined in the
[demo CloudFormation template](infra/cloudformation/demo.yaml). The separate
[database-access prerequisite template](infra/cloudformation/db-access-prerequisite.yaml)
creates the restricted writer and pipeline credentials and a one-off role-provisioning task.

### Request path inside the task

The ALB targets the frontend container on port 8080. Nginx serves the compiled React application
and proxies `/api/*` and `/health*` to the backend through the task's shared network namespace.
Port 8000 has no inbound security-group rule. The frontend container waits for the backend health
check before starting.

API requests are authenticated by the backend. Hiding a button in the frontend is not an access
control. PostgreSQL reads use the reader identity; explicitly mutating repositories use a
separate writer identity.

### Data path

```text
checked-in source caches
  └── collection/consolidation/enrichment
        └── coherent SQLite migration source
              └── checksum-bound migration and reconciliation
                    └── versioned PostgreSQL dataset
                          ├── materialized analytics
                          ├── serving repositories
                          └── durable jobs, overrides and profile cache
```

SQLite is a migration and local compatibility source. The deployed API uses PostgreSQL and is
configured with `DATA_RUNTIME_MODE=postgresql`.

### Architecture that is not deployed

The Terraform module under `infra/terraform/` describes a different target architecture with a
private S3 frontend, WAF, private API and worker tasks, SQS, Step Functions and EventBridge. Those
resources must not be used to describe the current customer demo. See
[Terraform and CI/CD](#15-terraform-and-cicd) for the boundary.

## 4. AWS demo environment

### 4.1 CloudFormation ownership

The demo is code-defined. It was deployed through CloudFormation and AWS CLI operations rather
than through the Terraform deployment workflows. The main definition is
[`infra/cloudformation/demo.yaml`](infra/cloudformation/demo.yaml).

This distinction matters during support:

- update the demo through a reviewed CloudFormation change set;
- do not run Terraform against the demo stack;
- do not assume Terraform state knows about these resources; and
- do not describe the environment as an undocumented console-only deployment.

The main stack defaults to zero application tasks and a disabled worker. A running environment
therefore reflects reviewed parameter overrides, not only the template defaults.

### 4.2 CloudFront and load balancing

CloudFront provides the customer-facing HTTPS endpoint using its default certificate. There is no
custom domain, Route 53 record or ACM certificate in the demo path. Caching is disabled for both
the application and `/api/*`. The API behavior forwards the browser `Authorization` header while
replacing the viewer `Host` header for the ALB origin.

The origin is an internet-facing ALB listener on HTTP port 80. The template supports two states:

| State | ALB ingress | Default listener action |
|---|---|---|
| Deployment A | Public port 80 | Forward to the frontend target group |
| Deployment B | CloudFront origin-facing prefix list only | Return 403 unless the origin-verification header matches |

The origin-verification value is a rotatable control, not a substitute for TLS and not a secret
that remains unknowable to privileged AWS administrators. Never commit, print or place its real
value in a parameter example.

The customer path has TLS only between the browser and CloudFront. CloudFront-to-ALB and
ALB-to-ECS are HTTP. This is an accepted temporary pilot risk and must be removed before a
production claim, particularly because API bearer tokens traverse the origin hop.

### 4.3 ECS service

The `application` service runs on Fargate using Linux/ARM64 and `awsvpc` networking. The task is
placed in the supplied public subnets with `AssignPublicIp: ENABLED`. Its security group allows
only:

- inbound port 8080 from the ALB security group;
- outbound HTTPS for AWS services and approved external services; and
- outbound PostgreSQL to the RDS security group.

The task contains:

| Container | Runtime | Health behavior |
|---|---|---|
| `frontend` | Unprivileged nginx, UID/GID 101, port 8080 | HTTP health check on `/` |
| `backend` | FastAPI, UID/GID 10001, port 8000 | Calls `/health/ready` |
| `worker` | Same backend image, `python -m pipelines.worker`, UID/GID 10001 | No ECS container health check; confirm through process state, logs and job progress |

Backend and worker use the same image. With the worker enabled, the task definition requests one
vCPU and 4 GiB memory. The application service uses a minimum healthy percentage of zero and a
maximum of 100, so deployments replace the single demo task rather than running two tasks at
once. A brief interruption is therefore possible.

ECS Exec is disabled. Images must be immutable ARM64 ECR references and must never use `latest`.

### 4.4 PostgreSQL

The main stack creates PostgreSQL 16 in two isolated database subnets. It is not publicly
accessible. RDS accepts port 5432 only from the ECS security group and forces TLS through
`rds.force_ssl=1`.

The demo configuration is deliberately cost-conscious:

- single-AZ database;
- encrypted gp3 storage, initially 30 GiB and allowed to grow to 100 GiB;
- one day of automated-backup retention;
- Performance Insights disabled;
- RDS deletion protection disabled; and
- snapshot retention on CloudFormation deletion or replacement.

These settings are not a production availability or recovery design.

Three runtime database identities are intentionally separate:

| Identity | Purpose |
|---|---|
| `foundation_app` | Read-only serving and readiness |
| `foundation_app_writer` | Bounded application mutations and durable job state |
| `foundation_pipeline_writer` | Worker-only versioned dataset publication and schema gate |

The application must fail closed when writer or pipeline credentials are incomplete. It must not
fall back to the RDS master user.

### 4.5 S3 and migration

The main stack owns one private, encrypted and versioned bucket used for:

- the approved SQLite migration source;
- migration evidence; and
- the worker's current pipeline snapshot.

Public access is blocked and insecure transport is denied. Objects below `imports/` expire after
seven days; the bucket itself is retained if the stack is deleted.

The migration task is a one-off three-container Fargate task:

1. `download-source` copies the approved source object into a shared ephemeral volume.
2. `migrate` verifies the checksum and schema, migrates and reconciles the candidate dataset, and
   activates it only after the gates pass.
3. `upload-evidence` publishes the generated evidence to S3 only after migration succeeds.

The migration task is not the continuously running worker.

### 4.6 Cognito

The demo uses Cognito Managed Login v2 and `AUTH_MODE=cognito_rbac`.

- Email is the sign-in identifier.
- Self-registration is disabled; administrators invite users.
- Passwords require at least 12 characters with upper case, lower case, a number and a symbol.
- TOTP MFA is required.
- The app client is public and has no client secret.
- The authorization-code flow uses PKCE S256; implicit flow is disabled.
- Scopes are `openid`, `email` and `profile`.
- Access and ID tokens are valid for 60 minutes; refresh tokens are valid for one day.
- Exactly one of the `customer`, `operator` or `admin` Cognito groups is required. Zero or more
  than one application group is rejected with HTTP 403.

The frontend stores tokens in browser session storage. It sends the access token as a bearer token
to the API and uses ID-token claims only for display. Logout clears local state and redirects to
the Cognito logout endpoint.

### 4.7 Secrets and IAM

Secret values are injected into individual containers when a task starts. They are not compiled
into an image or frontend bundle.

| Secret | Consumer |
|---|---|
| Application reader database secret | Backend and worker |
| Restricted writer database secret | Backend and worker |
| Pipeline database secret | Worker only |
| Anthropic API key secret | Backend only |
| Charity Commission API key secret | Worker only |

The configured external secret names are:

- `foundation-intelligence/demo/anthropic-api-key`
- `foundation-intelligence/demo/charity-commission-api-key`

Do not use `aws secretsmanager get-secret-value` as a health check. Do not put secret values in
shell history, tickets, documentation or deployment logs. After a secret rotation, start a new
ECS task because an existing task keeps the value injected at its own startup.

The application task role permits bounded Cognito user administration and access to the approved
pipeline snapshot objects. Frontend, backend and worker share the task role because they run in
one task; IAM is not isolated per container.

### 4.8 Logs and monitoring

CloudFormation creates CloudWatch log groups for:

- frontend;
- backend;
- worker; and
- migration.

Their retention is seven days. The database-access prerequisite has a separate log group whose
retention is parameterized to 7, 14 or 30 days.

The application defines 21 metric contracts and 15 alarm contracts in
[`config/observability.json`](config/observability.json), but the demo CloudFormation template
does not deploy those CloudWatch alarms or dashboards. ECS Container Insights is disabled. Do not
describe the demo as having no CloudWatch logs, and do not describe it as fully monitored.

## 5. Application structure

| Path | Responsibility |
|---|---|
| `src/bff/` | FastAPI entry point, authentication, API schemas and shared controls |
| `src/bff/postgres/` | Active PostgreSQL routes and repositories |
| `src/pipelines/` | Durable jobs, worker process and pipeline orchestration |
| `src/migration/` | PostgreSQL migration, reconciliation, release gates and AWS entry points |
| `src/preprocessing/` | Consolidation, deterministic enrichment and quality checks |
| `src/scrapers/` | Source-specific collectors |
| `src/scoring/` | Experimental deterministic relevance score |
| `src/governance/` | Retention, exposure and redaction policy |
| `src/observability/` | Local metric registry and alarm definitions |
| `frontend/` | React/Vite single-page application |
| `alembic/versions/` | PostgreSQL schema, currently through `0007_worker_execution` |
| `config/` | Versioned runtime, source, governance, scoring and observability policy |
| `infra/cloudformation/` | Actual non-production demo infrastructure |
| `infra/terraform/` | Separate intended staging/production architecture |

### Runtime selection

`DATA_RUNTIME_MODE=postgresql` selects the active asynchronous PostgreSQL route and repository
layer. `sqlite_migration_source` exists for local migration compatibility, and `shadow_compare`
serves PostgreSQL while comparing selected responses with a separate SQLite snapshot. Staging and
production reject the SQLite serving mode.

The runtime container is deliberately data-free. It contains application code and configuration,
but no SQLite database or raw source data.

## 6. Authentication and access control

### Supported modes

| Mode | Intended use |
|---|---|
| `disabled` | Fail closed; API requests receive 401 |
| `development` | Explicit local-only username/password bootstrap and signed cookie |
| `oidc` | Generic external OIDC bearer tokens |
| `public_readonly` | Temporary demo-only allowlist for selected GET/HEAD routes |
| `cognito_rbac` | Current AWS demo: Cognito access tokens and group-based roles |

Staging and production accept `oidc` or `cognito_rbac`; development authentication is rejected
outside development and test. The deployed demo uses Cognito, not the generic OIDC configuration
and not the public-readonly mode.

Interactive OpenAPI and ReDoc are disabled when `AUTH_MODE=cognito_rbac`. They remain available in
appropriate local modes at `/docs`, `/redoc` and `/openapi.json`.

### Application roles

Roles inherit upward: `admin` includes operator and customer capabilities; `operator` includes
customer capabilities.

| Capability | customer | operator | admin |
|---|:---:|:---:|:---:|
| Read dashboards, maps, directories, grants and stored profiles | yes | yes | yes |
| Read sanitized source status | yes | yes | yes |
| Calculate a custom score | no | yes | yes |
| Research latest news | no | yes | yes |
| Queue registry/funder enrichment and profile hydration | no | yes | yes |
| Read job status/history and local observability | no | yes | yes |
| Trigger supported pipeline work | no | yes | yes |
| Read pipeline event logs and change source schedules | no | no | yes |
| Relink or reset source funders | no | no | yes |
| Governance actions | no | no | yes |
| Manage Cognito users and application roles | no | no | yes |

Older names are accepted only as compatibility aliases: `viewer` and `analyst` map to `customer`,
and `administrator` maps to `admin`. New documentation and configuration should use the three
current names.

All mutating routes enforce authorization on the backend. Routes marked idempotent require an
`Idempotency-Key`; repeated use by the same actor and action is checked against a request
fingerprint. Request size, timeout and per-actor rate limits are enforced centrally.

User administration prevents an administrator from disabling or downgrading their own account and
prevents disabling or downgrading the last active administrator. Hard deletion is not exposed.

## 7. Data and domain rules

### 7.1 Presentation dataset

The reconciled evidence committed with the repository records:

| Record type | Count |
|---|---:|
| Enriched organizations | 373 |
| Charity Commission registry rows | 397,469 |
| Observed grants | 302,546 |
| Registry-to-profile links | 345 |
| Grant-beneficiary country associations | 104,309 |

The active evidence version is `sqlite-v7-8fc0cce61c81-r2`. These figures describe the bounded
presentation dataset, not complete Charity Commission, 360Giving, UK, DACH or European coverage.
The evidence source is
[`phase4-migration-manifest.json`](docs/remediation/evidence/phase4-migration-manifest.json).

### 7.2 Source boundaries

- Charity Commission data provides official England and Wales registration information and
  cached profile detail where available.
- 360Giving provides observed grant transactions. The repository contains a sample, not the
  whole 360Giving corpus.
- Philea provides organization metadata only. Membership must not be presented as grant activity.
- No current source provides complete DACH grant transactions.

### 7.3 Provenance and inference

Source facts, normalized facts, deterministic inferences and platform-derived values remain
separate. In particular:

- `programme_areas_source` is source-provided classification;
- `programme_areas_inferred` is produced by versioned deterministic rules;
- headquarters is where an organization is based;
- geographic focus is where an organization says it works or funds; and
- beneficiary geography is the destination attached to an observed grant.

The map uses beneficiary geography. It does not substitute funder headquarters, recipient office
or inferred focus. Unmapped grants remain reported as unmapped.

Original grant amount and currency are retained. Automatic EUR reporting uses stored historical
ECB conversion facts. Selecting a concrete currency limits results to grants originally recorded
in that currency.

### 7.4 Registry and enriched profiles

The organization directory has two layers:

- `charity_registry_organizations`: broad, lightweight official registry records; and
- `charities`: the smaller enriched profile set with classifications, observed relationships and
  other derived information.

`organization_registry_links` joins the layers. Exact identifier links may be accepted
automatically. Name-only fuzzy matches are not silently accepted. A registry-only record must be
shown as having no observed grant data, not as having no funding.

### 7.5 Dataset versioning

Every serving row belongs to a dataset version. Migration loads a candidate alongside the current
dataset, reconciles it, refreshes the candidate's materialized analytics and activates it in one
controlled transaction. A partial unique constraint permits exactly one active dataset. Previous
approved datasets can remain available for rollback.

The Alembic head expected by readiness and the worker is `0007_worker_execution`. Do not edit the
`alembic_version` table manually.

### 7.6 Scoring

The relevance score is deterministic and explainable, but its weights and target profile are an
example. It has not been approved as a customer decision model and must not be presented as a
probability of donation.

## 8. Product and API capabilities

### Main customer surfaces

- overview KPIs and filters;
- beneficiary-country map and bounded connection overlay;
- award trends and programme allocation;
- organization directory and profile detail;
- Charity Commission registry search;
- donor directory derived from observed source-funder facts;
- grant list and Sankey relationships;
- source and provenance labels;
- experimental score explanation; and
- saved UI state through typed URL parameters where implemented.

### API groups

| Prefix | Purpose | Minimum normal role |
|---|---|---|
| `/api/auth` | Public login configuration and authenticated identity | varies |
| `/api/charities` | Organizations, registry, grants, analytics, funders and scores | customer |
| `/api/scraper/status` | Sanitized source and freshness status | customer |
| `/api/news` | News discovery and sourced summary | operator |
| `/api/admin/pipeline` | Jobs, sources and manual pipeline control | operator; selected actions admin |
| `/api/admin/observability` | Metric definitions and local process snapshot | operator |
| `/api/admin/governance` | Holds, retention dry runs and governance evidence | admin |
| `/api/admin/users` | Cognito users and application roles | admin |

The generated OpenAPI schema is the detailed route and field reference in local modes. The
deployed Cognito environment intentionally does not publish it.

## 9. Jobs, workers and profile hydration

### 9.1 Durable job model

Long-running work does not run in an API request. Jobs are stored in PostgreSQL with status,
attempt, timeout, lease, heartbeat, result and safe failure fields. The worker claims the oldest
queued job with `FOR UPDATE SKIP LOCKED`, renews its lease while running, and records terminal
state and events.

The current deployment consumes the PostgreSQL queue directly. `job_dispatch_outbox` preserves an
SQS-compatible transactional boundary, but the demo does not deploy SQS.

### 9.2 Registered worker handlers

The production worker currently registers exactly these handlers:

| Job type | Behavior |
|---|---|
| `source_funder_profile_hydration` | Builds the profile cache from the existing organization-detail repository |
| `source_funder_enrichment` | Persists an exact linked profile and snapshot update |
| `registry_enrichment` | Links one exact Charity Commission profile and updates the snapshot |
| `full_run` | Runs the versioned pipeline publisher against an approved snapshot baseline |

There is a known contract mismatch: the admin API also accepts `quick_consolidate`,
`refresh_charities` and `refresh_grants`, but the production handler map does not register them.
Do not trigger those three modes in the deployed environment; the worker would finish them as
`UnsupportedJobType`. This is separate from profile hydration, which is registered and tested.

### 9.3 Funder-profile hydration lifecycle

When an operator requests a source-funder profile refresh:

1. The API confirms that the source funder has an effective linked profile.
2. It inserts or reuses a `source_funder_profile_hydration` job and writes a cache row with
   `status=pending` and the job token.
3. The worker claims the job and calls the existing organization-detail repository. There is no
   second profile-building implementation.
4. Success updates the same current cache row to `ready`, stores the payload and clears the error.
5. A terminal handler failure, unsupported-type failure or expired worker lease updates the cache
   to `failed`, clears the payload and exposes the safe message
   `Profile hydration could not be completed.`

A terminal worker error must not leave the cache indefinitely in `pending`. The frontend timeout
message means the polling window elapsed; it does not by itself identify the cause.

For investigation, check the job ID in `/api/admin/pipeline/jobs/{job_id}`, confirm the worker
container is running, inspect the worker log stream and then read the profile-cache endpoint. Do
not increase the frontend timeout until the job and cache states are understood.

### 9.4 Worker safety boundaries

- The worker requires PostgreSQL mode, the restricted writer and the pipeline publisher.
- It refuses to start when the database migration revision differs from
  `0007_worker_execution`.
- Leases are normally 90 seconds and are renewed while a handler runs.
- Expired work fails rather than being silently replayed.
- Snapshot mutation is serialized through a PostgreSQL advisory lock.
- `full_run` rejects the `fresh` option in the versioned RDS publisher.
- The previously active dataset remains the last-good dataset if a new publication fails.

## 10. Latest-news research

The news feature is an operator capability. It is not part of the anonymous or customer role.

The request path is:

1. Search a bounded Google News RSS candidate set.
2. Apply deterministic date filtering, deduplication and fallback rules.
3. Resolve and fetch a bounded amount of article text with redirect, DNS and SSRF checks.
4. Send only the selected source material and a constrained prompt to the configured Anthropic
   client.
5. Return a sourced briefing whose citations refer to the supplied articles.

The deployed backend sets `ANTHROPIC_BASE_URL=https://llm.netlight.ai/` and injects
`ANTHROPIC_API_KEY` from Secrets Manager. Requests therefore use the Anthropic SDK through the
Netlight gateway; they are not configured as direct browser-to-Anthropic requests. The default
model identifier in code is `claude-sonnet-5` unless `CLAUDE_MODEL` overrides it.

News discovery and summary failures are isolated from the rest of the profile. A failed RSS source
returns a source-unavailable result. If articles were found but the summary provider failed, the
API returns partial success with sources and a safe summary-unavailable message. News requests do
not persist article bodies or mutate the profile database.

The route performs live network requests and can incur external-provider cost. Do not use it as a
routine health probe or repeatedly retry it during an incident.

## 11. Local development

### 11.1 Prerequisites

- Python 3.12
- Node.js 22 and npm
- Docker Desktop with Compose
- a local file containing the PostgreSQL password

Install from the committed locks:

```bash
python3.12 -m venv venv
venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
venv/bin/python -m pip install --require-hashes -r requirements.txt
cd frontend
npm ci --ignore-scripts --no-audit --no-fund
cd ..
cp .env.example .env
```

Never commit `.env` or the password file.

### 11.2 Start PostgreSQL

```bash
export POSTGRES_PASSWORD_FILE=/absolute/path/to/local-postgres-password
export POSTGRES_HOST_PORT=55432
docker compose up -d postgres
docker compose --profile operations run --rm migration upgrade head
```

For a host-run backend, set the database host to `127.0.0.1`, port to `55432`, database to
`foundation_intelligence`, user to `foundation_app`, and point `DATABASE_PASSWORD_FILE` at the
same password file.

### 11.3 Enable local authentication

The shipped `.env.example` fails closed with `AUTH_MODE=disabled`. For browser development, set
unique local values in `.env`:

```dotenv
APP_ENV=development
DATA_RUNTIME_MODE=postgresql
AUTH_MODE=development
DEV_AUTH_ENABLED=true
DEV_AUTH_USERNAME=localdev
DEV_AUTH_PASSWORD=<local-only-password>
DEV_AUTH_SECRET=<at-least-32-random-characters>
DEV_AUTH_ALLOWED_HOSTS=127.0.0.1,::1,localhost
DATABASE_HOST=127.0.0.1
DATABASE_PORT=55432
DATABASE_NAME=foundation_intelligence
DATABASE_USER=foundation_app
DATABASE_PASSWORD_FILE=/absolute/path/to/local-postgres-password
```

Start the applications in separate terminals:

```bash
./start_backend.sh
```

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

Create the local development session before using protected API calls:

```bash
curl -i -c /tmp/fip-local-cookies.txt \
  -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"localdev","password":"<local-only-password>"}'
```

Use `127.0.0.1` consistently. Mixing it with `localhost` creates different browser origins and
can prevent the cookie from being sent.

### 11.4 Container layouts

`docker-compose.yml` runs PostgreSQL, backend, frontend, migration and the durable worker. The
default backend uses `AUTH_MODE=disabled`, so health checks work but protected API requests do not.

`docker-compose.ecs-local.yml` adds the shared-network frontend used to mirror the ECS task layout
and enables explicit development authentication. Supply all development credentials through the
shell; do not add them to either Compose file.

The worker mounts `src/data/charities.db` as a read-only baseline and publishes its local current
snapshot to a named volume. Do not start it when the approved baseline is absent or unverified.

### 11.5 Local health checks

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

`/health/live` checks only that the process is alive. `/health/ready` checks PostgreSQL, the
expected Alembic revision, one active dataset, critical source and retention configuration, and
the durable queue tables. Readiness uses an independent bounded connection so an exhausted
application pool does not hide the database condition.

## 12. Testing and quality gates

### Backend

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
```

PostgreSQL integration modules require an explicit test database:

```bash
RUN_POSTGRES_INTEGRATION=1 \
TEST_DATABASE_URL='postgresql+asyncpg://<test-user>:<password>@127.0.0.1:55432/<test-db>' \
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_postgres_application.py
```

Use a disposable database. Do not point tests at the customer demo.

### Frontend

```bash
cd frontend
npm run lint
npm test
npm run build
```

The build enforces bundle budgets. Browser and accessibility checks are available through the
Playwright scripts but need a suitable browser installation.

### Relevant AWS and worker contract tests

The focused tests for the current deployed design include:

- `src/tests/test_cloudformation_demo.py`
- `src/tests/test_cognito_rbac.py`
- `src/tests/test_database_access.py`
- `src/tests/test_worker_execution.py`
- `src/tests/test_profile_hydration_worker.py`
- `frontend/tests/authContracts.test.ts`
- `frontend/tests/workerUiContracts.test.ts`

The PostgreSQL portion of the hydration test is skipped unless an integration database is
explicitly supplied.

## 13. Deployment and release management

### 13.1 Demo deployment source

The customer demo is maintained with CloudFormation, not Terraform:

- main stack: [`infra/cloudformation/demo.yaml`](infra/cloudformation/demo.yaml)
- database-access prerequisite:
  [`infra/cloudformation/db-access-prerequisite.yaml`](infra/cloudformation/db-access-prerequisite.yaml)
- safe placeholder parameters:
  [`infra/cloudformation/parameters.demo.example.json`](infra/cloudformation/parameters.demo.example.json)

The example parameter file contains placeholders and template defaults. It is not a record of the
currently deployed parameter values.

### 13.2 Release order

Use a reviewed change process. The safe order is:

1. Confirm the AWS identity, account `208337080387`, region `eu-west-1`, branch, commit and clean
   worktree.
2. Run the focused tests for the changed area.
3. Build Linux/ARM64 backend and frontend images from the reviewed commit.
4. Push immutable ECR tags and resolve their image digests. Never deploy `latest`.
5. If database grants or identities changed, run and verify the database-access prerequisite task
   before updating the application stack.
6. If the Alembic head changed, run the one-off migration task and retain its evidence before
   starting code that requires the new revision.
7. Create a CloudFormation change set. Stop if it unexpectedly replaces or deletes RDS, the VPC,
   ALB, ECS cluster, target group, Cognito pool or import bucket.
8. Execute only the reviewed change set.
9. Wait for the new ECS task and target to stabilize.
10. Verify backend and frontend health, worker process state, target-group health, CloudFront HTTP
    status, authentication and one affected user journey.

Do not run a source refresh, full pipeline or paid-provider request as a generic deployment smoke
test. Test only the functionality changed by the release.

### 13.3 Secret rotation

ECS reads injected secret values only when a task starts. After updating an approved secret
version, force a new deployment of the existing application service and wait for the replacement
task to become healthy. Never print or compare the cleartext values. If equality must be checked,
use an explicitly approved in-memory SHA-256 fingerprint comparison and discard both values after
the comparison.

### 13.4 Rollback boundaries

- **Application regression:** restore the previously approved immutable image/task definition
  through a reviewed stack update.
- **Failed deployment before stability:** retain the stopped reason and deployment events, then
  roll back the CloudFormation update.
- **Bad candidate dataset:** reactivate only a previously approved version through the versioned
  rollback path; do not edit active flags manually.
- **Database loss:** restore into an isolated database, run schema and release validation, and
  promote only after evidence review.
- **Stack deletion:** not an application rollback. It can remove compute and identity resources;
  RDS snapshots and the import bucket have separate retention behavior.

The detailed historical recovery procedures are in
[`docs/remediation/rollback-runbook.md`](docs/remediation/rollback-runbook.md) and
[`docs/remediation/backup-restore-guide.md`](docs/remediation/backup-restore-guide.md). Validate their
commands against the current revision before use.

## 14. Operations and troubleshooting

### 14.1 Read-only deployment checks

After authenticating the AWS CLI, start with read-only checks:

```bash
aws sts get-caller-identity --profile netlight
aws cloudformation describe-stacks \
  --profile netlight --region eu-west-1 \
  --stack-name foundation-intelligence-demo-e06ab1ea-v2
aws ecs describe-services \
  --profile netlight --region eu-west-1 \
  --cluster foundation-intelligence-demo-e06ab1ea-v2 \
  --services application
curl -I https://d12pqv690k01m6.cloudfront.net/
```

Do not infer worker health from CloudFront alone. CloudFront can return the frontend while the
worker is stopped or unable to claim jobs.

### 14.2 Expected healthy state

- CloudFormation stack is in a completed state, not an in-progress or rollback state.
- ECS service desired and running counts match.
- Backend container is `RUNNING` and healthy.
- Frontend container is `RUNNING` and healthy.
- Worker container is `RUNNING` and producing normal idle/claim logs.
- The target group reports the frontend target healthy.
- CloudFront returns HTTP 200.
- `/health/ready` reports ready through the routed application path.
- `/api/auth/config` reports `cognito_rbac` with the expected region, pool and client.

### 14.3 Log locations

The main log-group prefix is:

```text
/foundation-intelligence/demo/foundation-intelligence-demo-e06ab1ea-v2/
```

Append `frontend`, `backend`, `worker` or `migration`. Logs are retained for seven days, so preserve
incident evidence promptly when needed. Application logs redact configured sensitive fields, but
operators must still avoid placing credentials or article bodies in diagnostic commands.

### 14.4 Common symptoms

#### CloudFront returns an error

1. Confirm the distribution is enabled and deployed.
2. Check the ALB target health.
3. Confirm the application ECS task is running.
4. Check frontend health and logs, then backend readiness and logs.
5. Verify the origin-lockdown prefix list and listener rule before changing security groups.

#### Backend is not ready

Check the readiness response and backend logs for:

- database connectivity or TLS failure;
- Alembic revision mismatch;
- no active dataset;
- missing source or retention configuration; or
- missing durable queue tables.

Do not weaken readiness to route around a missing schema or dataset.

#### Worker is running but jobs do not progress

1. Confirm the requested job type is one of the four registered handlers.
2. Inspect `/api/admin/pipeline/jobs/{job_id}`.
3. Check worker logs for schema-gate, credential, lease or handler failures.
4. Verify the writer and pipeline database identities are configured.
5. Verify the approved S3 baseline and checksum for jobs that need a snapshot.

#### Profile hydration times out in the browser

Use the procedure in [Funder-profile hydration lifecycle](#93-funder-profile-hydration-lifecycle).
The cache should become `ready` or `failed`; a terminal job with a cache still in `pending` is a
backend defect, not a reason to extend the browser timeout.

#### News research returns partial success

Differentiate RSS discovery failure from summary-provider failure. The response exposes
`source_status` and `summary_status`. Do not repeatedly call the provider while investigating.
Check backend logs for the safe exception class and confirm the gateway and injected key through
configuration and one approved request, never by printing the key.

### 14.5 Backups and recovery

The demo uses one day of RDS automated-backup retention and snapshot-on-delete/update-replace in
CloudFormation. This has not been established as an approved RPO or RTO. The migration source and
evidence in S3 are separate from the database backup.

Before any restore or dataset rollback:

- preserve the failing state and logs;
- identify the exact dataset and schema revision;
- restore into an isolated target;
- run reconciliation and release gates; and
- obtain approval before routing customer traffic.

## 15. Terraform and CI/CD

### 15.1 Pull-request CI

`.github/workflows/ci.yml` defines:

- backend compile, lint, mypy, tests and coverage;
- frontend lint, tests, build and browser/accessibility checks;
- dependency and licence security;
- CodeQL;
- container build, SBOM and vulnerability checks;
- Terraform static/security validation;
- PostgreSQL migration and integration checks; and
- an aggregate required gate.

The workflow currently requires `.terraform.lock.hcl` files in both Terraform environment roots.
Those files are absent from this revision, so the Terraform security job cannot pass as written.

### 15.2 Staging workflow

`deploy-staging.yml` is a manual workflow for the Terraform target architecture. It builds and
publishes immutable images, creates a reviewed Terraform plan, applies it through AWS OIDC, runs
migration and release tasks, updates separate API and worker services, publishes a frontend to S3,
and runs smoke and browser gates.

It is not the procedure used to deploy the current CloudFormation demo. Its AWS roles, GitHub
environment configuration, state backend and end-to-end execution must be verified before use.

### 15.3 Production workflow

`deploy-production.yml` contains a single job guarded by `if: ${{ false }}`. Production deployment
is intentionally disabled.

### 15.4 Terraform target compared with the demo

| Concern | Current CloudFormation demo | Terraform target |
|---|---|---|
| Frontend | nginx container in the application ECS task | private S3 origin behind CloudFront |
| API/worker | one shared ECS task definition | separate API and worker services |
| Queue | PostgreSQL job queue/outbox | SQS FIFO |
| Orchestration | worker process | EventBridge and Step Functions |
| Edge controls | CloudFront and ALB origin lockdown | CloudFront, WAF, DNS and certificate controls |
| Task networking | public subnets with public IP | private application subnets |
| Deployment ownership | CloudFormation stack | Terraform state and GitHub OIDC workflows |

Do not merge these descriptions into one diagram. They are separate environments and operating
models.

## 16. Security, governance and known limitations

### Implemented controls

- Cognito authorization-code flow with PKCE and required TOTP MFA.
- Exactly one recognized application role per Cognito user.
- Backend-enforced RBAC and idempotency for declared mutations.
- Non-root backend, frontend and worker processes.
- Restricted network ingress to the ECS frontend and RDS.
- Encrypted RDS storage and forced PostgreSQL TLS.
- Private, encrypted, versioned S3 bucket with public access blocked.
- Secrets injected at runtime rather than included in images.
- Data-free runtime image.
- Bounded requests, timeouts, rate limits and safe error responses.
- SSRF checks and response-size limits for article retrieval.
- Versioned datasets, reconciliation and last-good rollback boundaries.
- Destructive retention disabled and legal holds represented explicitly.

### Open risks and limitations

1. The environment is a non-production pilot. `production_activation_approved` is false.
2. TLS ends at CloudFront; the origin path is HTTP.
3. ECS tasks have public IP addresses, although inbound traffic is security-group restricted.
4. There is no WAF, custom domain or end-to-end certificate validation in the demo.
5. RDS is single-AZ, has one day of backup retention and has deletion protection disabled.
6. ECS Container Insights, CloudWatch alarms and dashboards are not deployed by the demo stack.
7. Frontend, backend and worker share one ECS task and one task IAM role.
8. A one-task deployment can cause interruption during replacement.
9. Production load, concurrency, failover and recovery objectives are not approved or proven.
10. The committed source schedules have unresolved legal and licence status and remain disabled.
11. Destructive deletion is disabled; retention endpoints produce plans and evidence only.
12. Terraform provider lock files are missing while CI requires them.
13. The Terraform staging workflow and GitHub/AWS OIDC path are not evidence for the current demo.
14. Three pipeline modes accepted by the admin API lack production worker handlers.
15. News research depends on Google News, publisher availability, the Netlight gateway and an
    Anthropic credential. It is neither deterministic nor suitable as a general health check.
16. The dataset is sampled and geographically uneven. Absence of evidence is not evidence of no
    funding.
17. Enrichment accuracy has not been validated against labelled ground truth.
18. The relevance score is experimental and not approved for decisions.

## 17. Handover checklist

### Repository

- [ ] Confirm `dev` and the handover branch have the expected commit history.
- [ ] Protect `main` and `dev` with reviewed pull requests and required checks.
- [ ] Assign owners for backend, frontend, data pipeline and AWS infrastructure.
- [ ] Decide whether historical audit documents remain in place or move to an archive.

### AWS access and ownership

- [ ] Confirm named owners for account `208337080387` and region `eu-west-1`.
- [ ] Record who can update CloudFormation, ECS, Cognito, RDS, ECR and Secrets Manager.
- [ ] Review and rotate delivery credentials where required.
- [ ] Confirm billing ownership and create an approved budget.

### Demo operations

- [ ] Recheck the stack, ECS service, task definition, target group and CloudFront distribution.
- [ ] Confirm Cognito customer, operator and admin journeys.
- [ ] Confirm there is at least one active administrator and no user has multiple app groups.
- [ ] Confirm backend/frontend health and worker job progress separately.
- [ ] Confirm log retention is sufficient for the support process.
- [ ] Record the current image digests and code revision after each deployment.

### Data and recovery

- [ ] Record the active dataset version and Alembic revision.
- [ ] Confirm the S3 baseline checksum and migration evidence location.
- [ ] Agree RPO and RTO.
- [ ] Perform and document an isolated RDS restore exercise.
- [ ] Resolve legal/licence ownership before enabling any source schedule.

### Production decision

- [ ] Choose CloudFormation or Terraform as the supported long-term infrastructure path.
- [ ] Add end-to-end TLS, WAF and private task networking.
- [ ] Add CloudWatch alarms, dashboards, budgets and on-call ownership.
- [ ] Establish Multi-AZ and backup requirements.
- [ ] Prove load, concurrency, failure and rollback behavior.
- [ ] Enable production deployment only after governance and security approval.

## 18. Sources of truth

Use these files before relying on older prose:

| Subject | Source |
|---|---|
| Current demo infrastructure | [`infra/cloudformation/demo.yaml`](infra/cloudformation/demo.yaml) |
| Database-access prerequisite | [`infra/cloudformation/db-access-prerequisite.yaml`](infra/cloudformation/db-access-prerequisite.yaml) |
| Authentication configuration | [`src/bff/config.py`](src/bff/config.py) |
| Token and role enforcement | [`src/bff/security.py`](src/bff/security.py) |
| Cognito browser flow | [`frontend/src/auth/AuthContext.tsx`](frontend/src/auth/AuthContext.tsx) |
| Cognito user administration | [`src/bff/user_management.py`](src/bff/user_management.py) |
| Active PostgreSQL routes | [`src/bff/postgres/routes.py`](src/bff/postgres/routes.py) |
| Durable job persistence | [`src/bff/postgres/job_repository.py`](src/bff/postgres/job_repository.py) |
| Production worker | [`src/pipelines/worker.py`](src/pipelines/worker.py) |
| Worker handler map | [`src/pipelines/worker_handlers.py`](src/pipelines/worker_handlers.py) |
| Profile-cache lifecycle | [`src/bff/postgres/funder_repository.py`](src/bff/postgres/funder_repository.py) |
| News behavior | [`src/bff/news.py`](src/bff/news.py) |
| Database schema | [`alembic/versions/`](alembic/versions/) |
| Governance flags | [`config/data-governance.json`](config/data-governance.json) |
| Source schedules | [`config/source-pipelines.json`](config/source-pipelines.json) |
| Readiness and alarm contract | [`config/observability.json`](config/observability.json) |
| Local container stack | [`docker-compose.yml`](docker-compose.yml) |
| CI | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |
| Staging target workflow | [`.github/workflows/deploy-staging.yml`](.github/workflows/deploy-staging.yml) |
| Production workflow status | [`.github/workflows/deploy-production.yml`](.github/workflows/deploy-production.yml) |

When code, configuration and a dated operational observation disagree, stop and establish which
version is actually deployed before changing the environment.
