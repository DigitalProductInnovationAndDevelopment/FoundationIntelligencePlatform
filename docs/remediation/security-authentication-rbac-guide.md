# Authentication, RBAC and administration guide

Staging and production fail closed unless `AUTH_MODE=oidc`. The issuer,
audience, JWKS and asymmetric algorithm allowlist are explicit. Browser sessions
use secure cookies; wildcard CORS, browser credential forwarding and anonymous
mutations are forbidden. Local development authentication requires an explicit
opt-in and localhost allowlist.

Roles are ordered by capability:

| Role | Read data | Score/news | Queue refresh/enrichment | Governance/admin |
|---|---:|---:|---:|---:|
| viewer | Yes | No | No | No |
| analyst | Yes | Yes | No | No |
| operator | Yes | Yes | Yes | Limited operational reads |
| administrator | Yes | Yes | Yes | Yes |

Mutation routes require role authorization, bounded request bodies, rate limits,
an idempotency key where declared, structured audit evidence and safe error
responses. Manual refresh only enqueues a durable job; it never starts a scraper
inside the API process. Pipeline workers and dispatchers are separate.

The optional core proxy is disabled by default. If enabled, its destination
host, path, method and headers must match explicit allowlists; browser
`Authorization` and `Cookie` headers are never forwarded.

## Temporary public read-only demo

`AUTH_MODE=public_readonly` is accepted only together with `APP_ENV=demo`.
Any other environment fails during startup. This mode is exclusively for a
temporary public, non-production demonstration. It creates no login, session,
cookie or shared token and requires the development login and core proxy to be
disabled. It also requires `DATA_RUNTIME_MODE=postgresql`; the legacy SQLite
response path cannot be exposed by this mode.

Anonymous access is a route-template and method allowlist in
`bff.security.PUBLIC_READONLY_ROUTE_ALLOWLIST`. Only `GET` and `HEAD` can use
the anonymous viewer principal. The reviewed allowlist contains the normal
read-only UI surfaces:

- `/api/charities`
- `/api/charities/stats`
- `/api/charities/{reg_charity_number}`
- `/api/charities/{reg_charity_number}/grants`
- `/api/charities/{reg_charity_number}/sankey`
- `/api/charities/{reg_charity_number}/score`
- `/api/charities/directory/organizations`
- `/api/charities/directory/organizations/{registry_id}`
- `/api/charities/grants/beneficiary-geographies`
- `/api/charities/grants/funders`
- `/api/charities/grants/funders/{source_funder_key}`
- `/api/charities/grants/map`
- `/api/charities/grants/overview`
- `/api/charities/grants/overview/trends`
- `/api/charities/grants/overview/entity-suggestions`
- `/api/charities/grants/overview/drilldown`
- `/api/charities/grants/map/connections`
- `/api/charities/grants/summary`
- `/api/charities/grants/trends`
- `/api/charities/grants/themes`

These routes provide the dashboard, statistics, maps, filters, rankings,
organization/registry directories, donor directory, details, grant evidence
and drill-downs shown by the browser. Profile-cache hydration, live news
research, exports, authentication, administration, system configuration,
jobs/pipelines, governance/retention, observability and all mutation routes
remain protected. Adding a viewer route does not make it public; every new
public route requires a separate schema, response and data-classification
review before it can be added to the exact allowlist.

Secrets must enter through mounted files, runtime secret stores or OIDC. Do not
commit `.env`, passwords, tokens, Terraform state/plans or generated cloud
secrets. Logs recursively redact credentials, connection strings, emails,
postal details, raw payloads and article bodies.

GitHub/AWS OIDC trust, branch protection and protected-environment reviewers
are defined/required but externally `NOT TESTED`. No long-lived AWS access key
is accepted by the deployment workflows.
