# Phase-1 API Route and Authorization Inventory

Status: implemented security contract. Role inheritance is `administrator > operator > analyst > viewer`; a higher role satisfies lower-role reads. All unlisted actions are denied by default.

## Public and authentication surface

| Method | Path | Classification | Required identity | Mutation/idempotency | Notes |
|---|---|---|---|---|---|
| GET | `/`, `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` | public read | none | read-only | Documentation/redirect surface; production edge policy may further restrict it. |
| GET | `/health`, `/health/live` | public read | none | read-only | Process liveness only; no database or analytics dependency. |
| GET | `/health/ready` | public read | none | read-only | Non-sensitive PostgreSQL/schema/dataset/configuration/queue readiness states through an independent bounded connection. |
| POST | `/api/auth/login` | development authentication bootstrap | explicit local/test mode and allowed client host | authentication exception | Returns 404 unless `AUTH_MODE=development` and `DEV_AUTH_ENABLED=true`; unavailable in staging/production. |
| POST | `/api/auth/logout` | authenticated action | viewer or higher | session cleanup | Clears only the local-development cookie; production OIDC logout belongs to the identity provider. |

## Organization and grant surface

| Method | Path | Classification | Minimum role | Mutation/idempotency |
|---|---|---|---|---|
| GET | `/api/charities` | authenticated read | viewer | read-only |
| GET | `/api/charities/stats` | authenticated read | viewer | read-only |
| GET | `/api/charities/{reg_charity_number}` | authenticated read | viewer | read-only |
| GET | `/api/charities/{reg_charity_number}/grants` | authenticated read | viewer | read-only |
| GET | `/api/charities/{reg_charity_number}/sankey` | authenticated read | viewer | read-only |
| POST | `/api/charities/{reg_charity_number}/score` | authenticated calculation | analyst | pure calculation; no persisted mutation |
| GET | `/api/charities/directory/organizations` | authenticated read | viewer | read-only |
| GET | `/api/charities/directory/organizations/{registry_id}` | authenticated read | viewer | read-only |
| POST | `/api/charities/directory/organizations/enrich` | operator action | operator | required `Idempotency-Key` |
| GET | `/api/charities/grants/beneficiary-geographies` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/map` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/map/connections` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/overview` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/overview/entity-suggestions` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/overview/trends` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/overview/drilldown` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/funders` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/funders/{source_funder_key}` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/funders/{source_funder_key}/profile-cache` | authenticated read | viewer | read-only |
| POST | `/api/charities/grants/funders/enrich` | operator action | operator | required `Idempotency-Key` |
| POST | `/api/charities/grants/funders/{source_funder_key}/profile-cache` | operator action | operator | required `Idempotency-Key` |
| POST | `/api/charities/grants/funders/{source_funder_key}/reset-to-observed` | administrator action | administrator | required `Idempotency-Key` |
| POST | `/api/charities/grants/funders/{source_funder_key}/relink` | administrator action | administrator | required `Idempotency-Key` |
| GET | `/api/charities/grants/summary` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/trends` | authenticated read | viewer | read-only |
| GET | `/api/charities/grants/themes` | authenticated read | viewer | read-only |

## Operational, external-call and proxy surface

| Method | Path | Classification | Minimum role | Additional controls |
|---|---|---|---|---|
| GET | `/api/admin/pipeline/status` | operator action/read | operator | Reads the latest durable PostgreSQL job state. |
| POST | `/api/admin/pipeline/trigger` | operator action | operator | Required `Idempotency-Key`; bounded mode validation; security audit event. |
| GET | `/api/admin/pipeline/jobs` | operator action/read | operator | Bounded durable PostgreSQL job history. |
| GET | `/api/admin/pipeline/logs` | administrator action/read | administrator | Last 100 structured PostgreSQL job events; output redacted; security audit event. |
| GET | `/api/admin/pipeline/sources` | operator action/read | operator | Reads governance-gated source schedules; credential references are masked. |
| PUT | `/api/admin/pipeline/sources/{source_name}/schedule` | administrator action | administrator | Required `Idempotency-Key`; unresolved legal/licence state blocks enablement. |
| GET | `/api/admin/governance/retention/policies` | governance read | administrator | Explicit proposed policies; destructive activation false. |
| POST | `/api/admin/governance/retention/dry-run` | governance action | administrator | Required `Idempotency-Key`; report/archive evidence only, never deletion. |
| GET/POST | `/api/admin/governance/holds` | governance read/action | administrator | Legal/incident hold list/create; create requires idempotency. |
| POST | `/api/admin/governance/holds/{hold_id}/release` | governance action | administrator | Required `Idempotency-Key`, actor and reason. |
| GET | `/api/admin/governance/exports/expiration-report` | governance read | administrator | Dry-run expiration report; no object mutation. |
| POST | `/api/admin/governance/data-subject-requests` | governance action | administrator | Required `Idempotency-Key`; accepts only a hashed subject reference. |
| GET | `/api/admin/observability/metrics` | observability read | administrator | Bounded process-local metric evidence and versioned definitions; no CloudWatch execution. |
| GET | `/api/news/{foundation_name}/summary` | authenticated resource-intensive read | analyst | Bounded parameters/timeouts; live paid-provider use remains separately approval-gated. |
| GET | `/api/news/{foundation_name}/summary/stream` | authenticated resource-intensive read | analyst | Same restrictions as the JSON endpoint. |
| configured allowlist only | `/api/core/{path}` | proxy action | administrator | Disabled by default; fixed destination host, exact/prefix path allowlist, method allowlist, request/response header allowlists, no browser credential forwarding, no redirects, timeout, request ID and idempotency for enabled mutations. Hidden from OpenAPI. |

There is no internal-service route in the current application. Queue/task callbacks introduced in later phases must use workload identity and a separate internal-service authorization dependency; they must not reuse user cookies.

## Phase boundary

Production/staging application data, job state, request idempotency, link
overrides, profile caches and audit records use PostgreSQL. Manual refresh
routes only enqueue durable jobs and return a job ID. Phase-8 workers claim
through PostgreSQL leases and the transactional outbox supplies the SQS
delivery contract; neither path uses a production local lock or API
subprocess. Production audit events are append-only PostgreSQL rows; local
unit tests may replace the sink with deterministic memory state.
Edge/distributed rate limiting remains a Terraform and deployment concern.
