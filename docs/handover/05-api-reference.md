# API reference

Complete route inventory for the PostgreSQL runtime (`DATA_RUNTIME_MODE=postgresql`, the
default). Verified against `src/bff/postgres/routes.py`, `admin_routes.py`,
`governance_routes.py`, `observability_routes.py`, `auth.py`, `news.py` and `proxy.py`.

Interactive documentation is served at `/docs` (Swagger) and `/redoc`; `/` redirects to
Swagger. The OpenAPI schema is at `/openapi.json`. The proxy route is hidden from OpenAPI.

## Authorization model

Roles inherit: **`administrator > operator > analyst > viewer`**. A higher role satisfies
a lower-role read. Everything not listed is denied by default.

Identity comes from an OIDC bearer token in staging and production, or from the
development session cookie locally. There is **no anonymous access** to `/api/*`.

Every mutating route requires an `Idempotency-Key` header (≤128 characters). The key is
reserved durably in PostgreSQL together with a SHA-256 fingerprint of method, path and
body before the handler runs, so a retried request cannot double-apply.

Rate limiting is per actor: `RATE_LIMIT_REQUESTS` per `RATE_LIMIT_WINDOW_SECONDS`
(default 120/60), returning 429 with `Retry-After`.

## Public surface

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/` | none | Redirects to Swagger |
| GET | `/docs`, `/redoc`, `/openapi.json` | none | Documentation surface; a production edge policy may restrict it |
| GET | `/health`, `/health/live` | none | Process liveness only; no database dependency |
| GET | `/health/ready` | none | PostgreSQL query, expected Alembic revision, exactly one active dataset, source/retention config sync, outbox availability. Uses an independent no-pool connection so exhausted analytical connections cannot block it. Returns check states only — never identifiers or connection details |

## Authentication

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/api/auth/login` | none | **Returns 404 unless `AUTH_MODE=development` and `DEV_AUTH_ENABLED=true`**, and the request host is in `DEV_AUTH_ALLOWED_HOSTS`. Sets an HTTP-only, `SameSite=Strict` cookie scoped to `/api`. Rate-limited per host. Unavailable in staging/production |
| POST | `/api/auth/logout` | viewer | Clears the development cookie. Production OIDC logout belongs to the identity provider |

## Organizations and grants — `/api/charities`

Router-level dependency: `require_roles(Role.VIEWER, action="charity.read")`.

### Reads

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/api/charities` | viewer | Search, filter and paginate organizations |
| GET | `/api/charities/stats` | viewer | Dataset KPIs, source counts, organization-type counts |
| GET | `/api/charities/{reg_charity_number}` | viewer | Organization detail, provenance, enrichment evidence |
| GET | `/api/charities/{reg_charity_number}/grants` | viewer | Observed transactions and coverage status. `role=all\|funder\|recipient` |
| GET | `/api/charities/{reg_charity_number}/sankey` | viewer | Donor-to-recipient flows. Auto mode converts to EUR; an explicit `currency` stays source-currency-only |
| GET | `/api/charities/directory/organizations` | viewer | Cursor-paginated registry directory. Max 100 rows (50 default) |
| GET | `/api/charities/directory/organizations/{registry_id}` | viewer | Registry detail plus accepted enriched-profile link where present |
| GET | `/api/charities/grants/beneficiary-geographies` | viewer | Distinct normalized beneficiary geographies |
| GET | `/api/charities/grants/map` | viewer | Beneficiary-country associations, currency-safe totals, connection groups, coverage and exclusion metadata |
| GET | `/api/charities/grants/map/connections` | viewer | Disclosed HQ-to-beneficiary connection groups. Capped at 250 |
| GET | `/api/charities/grants/overview` | viewer | Default overview payload |
| GET | `/api/charities/grants/overview/entity-suggestions` | viewer | Typeahead entity suggestions |
| GET | `/api/charities/grants/overview/trends` | viewer | Overview-scoped monthly trends |
| GET | `/api/charities/grants/overview/drilldown` | viewer | Drill-down into an overview segment |
| GET | `/api/charities/grants/summary` | viewer | Currency-separated network totals and rankings |
| GET | `/api/charities/grants/trends` | viewer | Award-date monthly totals with unknown-coverage months and exclusions |
| GET | `/api/charities/grants/themes` | viewer | Minor-unit-preserving programme allocations and classification coverage |
| GET | `/api/charities/grants/funders` | viewer | Filtered, paginated observed donor ranking. Supports `search`, `profile_status`, sort and canonical grant-scope filters |
| GET | `/api/charities/grants/funders/{source_funder_key}` | viewer | Source-funder detail. Summary-first with lazy full recipients/grants/evidence. Never a synthetic organization profile |
| GET | `/api/charities/grants/funders/{source_funder_key}/profile-cache` | viewer | Cached funder profile |

Common list filters: `search`, `reg_status`, `tags`, `foundation_regions`,
`funding_regions`, `min_annual_giving`, `min_avg_grant_size`, `skip`, `limit`.

Registry directory filters: `query`, `charity_number`, status, financial, registry
geography, accepted-link beneficiary geography, enriched/grant flags, `cursor`, `limit`,
`sort`.

Transaction endpoints report explicit statuses — `available`,
`organization_level_only`, `transaction_data_unavailable`, and mixed-currency
requirements — so absent data is never rendered as zero activity.

### Mutations

| Method | Path | Role | Idempotency |
|---|---|---|---|
| POST | `/api/charities/{reg_charity_number}/score` | analyst | Pure calculation; nothing persisted |
| POST | `/api/charities/directory/organizations/enrich` | operator | Required |
| POST | `/api/charities/grants/funders/enrich` | operator | Required |
| POST | `/api/charities/grants/funders/{source_funder_key}/profile-cache` | operator | Required |
| POST | `/api/charities/grants/funders/{source_funder_key}/reset-to-observed` | administrator | Required |
| POST | `/api/charities/grants/funders/{source_funder_key}/relink` | administrator | Required |

## Pipeline administration — `/api/admin`

| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/admin/pipeline/status` | operator | Latest durable job state |
| POST | `/api/admin/pipeline/trigger` | operator | Enqueues a durable job and returns a job ID. Bounded mode validation; emits a security audit event. Does not run work in-process |
| GET | `/api/admin/pipeline/jobs` | operator | Bounded durable job history |
| GET | `/api/admin/pipeline/logs` | administrator | Last 100 structured job events, redacted; emits an audit event |
| GET | `/api/admin/pipeline/sources` | operator | Governance-gated source schedules; credential references masked |
| PUT | `/api/admin/pipeline/sources/{source_name}/schedule` | administrator | Required idempotency. Unresolved legal/licence state blocks enablement |

## Governance — `/api/admin/governance`

All administrator-only. Retention is non-destructive: `destructive_deletion_enabled` is
`false` in `config/data-governance.json` and deletion is never performed by these routes.

| Method | Path | Idempotency | Notes |
|---|---|---|---|
| GET | `/api/admin/governance/retention/policies` | — | Proposed policies |
| POST | `/api/admin/governance/retention/dry-run` | Required | Report and archive evidence only |
| GET | `/api/admin/governance/holds` | — | Legal/incident hold list |
| POST | `/api/admin/governance/holds` | Required | Create a hold. Holds override retention actions |
| POST | `/api/admin/governance/holds/{hold_id}/release` | Required | Requires actor and reason |
| GET | `/api/admin/governance/exports/expiration-report` | — | Dry-run expiration report; no object mutation |
| POST | `/api/admin/governance/data-subject-requests` | Required | Accepts only a hashed subject reference |

## Observability

| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/admin/observability/metrics` | administrator | Versioned metric/alarm definitions plus bounded local-process evidence. No CloudWatch involvement |

## News (optional)

| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/news/{foundation_name}/summary` | analyst | Sourced summary. Bounded parameters and timeouts. Live provider use is separately approval-gated and requires credentials |
| GET | `/api/news/{foundation_name}/summary/stream` | analyst | Streaming variant, same restrictions |

## Proxy

| Method | Path | Role | Notes |
|---|---|---|---|
| allowlist only | `/api/core/{path}` | administrator | **Disabled by default.** Fixed destination host, exact/prefix path allowlist, method allowlist, request and response header allowlists, no browser credential forwarding, no redirects, timeout applied. Hidden from OpenAPI |

There is no internal-service route. Any future queue or task callback must use workload
identity and a separate internal-service authorization dependency — it must not reuse
user cookies.

## Error semantics

| Status | Meaning |
|---|---|
| 400 | Missing or malformed `Idempotency-Key`, or invalid parameters |
| 401 | No valid bearer token or session cookie |
| 403 | Authenticated, but the role does not permit the action |
| 404 | Resource absent — also returned by `/api/auth/login` when development auth is off |
| 429 | Per-actor or per-host rate limit exceeded; `Retry-After` supplied |
| 503 | Readiness dependency unavailable |

The authoritative security classification for every route, including the reasoning behind
each role assignment, is `docs/remediation/aws-postgres-route-inventory.md`.
