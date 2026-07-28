# Phase-1 API Route and Authorization Inventory

Status: implemented security contract. Role inheritance is `administrator > operator > analyst > viewer`; a higher role satisfies lower-role reads. All unlisted actions are denied by default.

## Public and authentication surface

| Method | Path | Classification | Required identity | Mutation/idempotency | Notes |
|---|---|---|---|---|---|
| GET | `/`, `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` | public read | none | read-only | Documentation/redirect surface; production edge policy may further restrict it. |
| GET | `/health` | public read | none | read-only | Liveness only; later phases add dependency-aware readiness. |
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
| GET | `/api/admin/pipeline/status` | operator action/read | operator | Security audit event; current local status is replaced by durable job state in Phase 8. |
| POST | `/api/admin/pipeline/trigger` | operator action | operator | Required `Idempotency-Key`; bounded mode validation; security audit event. |
| GET | `/api/admin/pipeline/logs` | administrator action/read | administrator | Last 100 lines only; output redacted; security audit event. |
| GET | `/api/news/{foundation_name}/summary` | authenticated resource-intensive read | analyst | Bounded parameters/timeouts; live paid-provider use remains separately approval-gated. |
| GET | `/api/news/{foundation_name}/summary/stream` | authenticated resource-intensive read | analyst | Same restrictions as the JSON endpoint. |
| configured allowlist only | `/api/core/{path}` | proxy action | administrator | Disabled by default; fixed destination host, exact/prefix path allowlist, method allowlist, request/response header allowlists, no browser credential forwarding, no redirects, timeout, request ID and idempotency for enabled mutations. Hidden from OpenAPI. |

There is no internal-service route in the current application. Queue/task callbacks introduced in later phases must use workload identity and a separate internal-service authorization dependency; they must not reuse user cookies.

## Phase boundary

Rate limiting and idempotency are thread-safe process-local controls in Phase 1 so the current single local process is deterministic. The target production layers are WAF/API edge rate limiting plus PostgreSQL-backed idempotency/job records before horizontal ECS deployment. Security audit records are append-only structured events through a sink interface; the runtime sink emits redacted JSON for the managed log pipeline, and tests replace it with a deterministic memory sink. Later schema/observability phases add the durable database audit table and least-privilege CloudWatch retention policy without changing route semantics.
