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

Secrets must enter through mounted files, runtime secret stores or OIDC. Do not
commit `.env`, passwords, tokens, Terraform state/plans or generated cloud
secrets. Logs recursively redact credentials, connection strings, emails,
postal details, raw payloads and article bodies.

GitHub/AWS OIDC trust, branch protection and protected-environment reviewers
are defined/required but externally `NOT TESTED`. No long-lived AWS access key
is accepted by the deployment workflows.
