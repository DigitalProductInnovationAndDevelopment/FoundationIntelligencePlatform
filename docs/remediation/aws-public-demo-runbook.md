# CloudFront and Cognito customer-pilot runbook

Status: repository-only implementation prepared and locally validated on
2026-08-12. No change set, deployment, Cognito user, image push, data operation
or other AWS write is evidenced by this document.

## Confirmed baseline

- AWS profile/account/region: `netlight`, `208337080387`, `eu-west-1`.
- Existing stack: `foundation-intelligence-demo-e06ab1ea-v2`, observed in
  `UPDATE_COMPLETE` before this implementation.
- Existing path: internet -> public ALB HTTP 80 -> ECS frontend 8080 -> nginx ->
  backend `127.0.0.1:8000` -> private RDS PostgreSQL.
- ECS port 8080 accepts only the ALB security group. Backend port 8000 has no
  direct ingress. RDS port 5432 accepts only the ECS security group.
- The confirmed CloudFront origin-facing prefix list in `eu-west-1` was
  `pl-4fa04526`, weight 55. This identifier is deliberately not hardcoded in the
  template and must be resolved again immediately before Deployment B.
- The ALB security-group inbound quota was 60 weighted rules. The observed
  current weight was 1, expected State B weight 55, and transition maximum 56.
- At preflight there were no CloudFront distributions, Cognito user pools or
  Cognito domains in scope.

## Architecture and accepted temporary residual risk

The customer address is the generated `https://<distribution>.cloudfront.net`
URL. There is no alias, custom domain, Route 53 record or ACM certificate in the
active pilot path.

```text
Browser --HTTPS--> CloudFront --HTTP:80--> ALB --HTTP--> ECS nginx/backend
                                                        |
                                                        +--> RDS (TLS required)
```

**ACCEPTED TEMPORARY RESIDUAL RISK:** CloudFront-to-ALB and ALB-to-ECS are
HTTP-only. This is not end-to-end TLS. In particular, bearer tokens are not
protected by TLS on the CloudFront-to-ALB hop. The origin-verification header is
an access-control aid, not transport encryption and not a permanently
unreadable secret.

The later hardening option is a reviewed custom domain plus ACM certificate,
HTTPS from CloudFront to the ALB, and origin-certificate validation. Earlier
repository work for direct ALB HTTPS informed this option, but
`CertificateArn` and `CustomDomainName` do not block the no-domain pilot.

## Authorization contract

The backend is the authoritative security boundary. Frontend visibility is UX
only. A validated Cognito access token must contain exactly one of the three
application groups; zero or multiple app groups fail closed with HTTP 403.

| Capability | customer | operator | admin |
|---|:---:|:---:|:---:|
| Analytics, maps, directories, search, details and drill-downs | yes | yes | yes |
| Sanitized scraper status, sources, freshness and counts | yes | yes | yes |
| Scraper/pipeline run and operational actions | no | yes | yes |
| Relevant operational observability | no | yes | yes |
| Pipeline logs, governance administration and critical settings | no | no | yes |
| Cognito users and application roles | no | no | yes |

User-management paths use Cognito `Username` as their stable opaque `{id}`.
Email is display/invitation data, never the administrative URL identifier.
Hard delete is intentionally absent. The backend blocks self-disable,
self-downgrade, last-active-admin disable and last-active-admin downgrade.

## Cognito and browser login contract

- User Pool sign-in identifier: email; self-registration is disabled.
- Password: at least 12 characters with lower/upper case, number and symbol.
- Recovery: verified email; no SMS dependency.
- MFA: `MfaConfiguration: 'ON'` and only
  `EnabledMfas: [SOFTWARE_TOKEN_MFA]`. `EnabledMfas` is the CloudFormation
  property that activates TOTP; `SoftwareTokenMfaConfiguration` belongs to the
  Cognito service API and is not a valid `AWS::Cognito::UserPool` property.
- Managed Login v2 with Cognito-provided branding.
- Public app client, no client secret, authorization-code flow only, PKCE S256,
  scopes `openid email profile`; implicit flow is disabled.
- Callback: `https://${CloudFrontDistribution.DomainName}/auth/callback`.
- Logout: `https://${CloudFrontDistribution.DomainName}/`.
- The frontend keeps tokens only in session storage, sends the access token as
  the API bearer token, uses the ID token only for display email, never logs
  tokens, validates OAuth state and removes callback parameters immediately.

AWS documents that required MFA makes Managed Login prompt users to configure
an additional factor. With email invitation and the configured pool, the first
login sequence is invitation -> temporary password -> new password -> TOTP
enrolment -> authenticated redirect. This must still be manually confirmed in
Deployment A before origin lockdown.

## CloudFront contract

- Default CloudFront certificate, no aliases, HTTPS redirect, HTTP/2 and HTTP/3.
- Default behavior allows `GET`, `HEAD`, `OPTIONS`; caching is disabled and all
  callback query strings are forwarded.
- `/api/*` allows all required methods. Both behaviors use AWS managed
  `Managed-CachingDisabled` (`4135ea2d-6df8-44a3-9df3-4b5a84be39ad`), avoiding
  invalid custom zero-TTL cache-policy combinations.
- The default behavior uses a minimal custom origin-request policy: all query
  strings, no viewer cookies and no viewer headers.
- `/api/*` uses AWS managed `Managed-AllViewerExceptHostHeader`
  (`b689b0a8-53d0-40ab-baf2-68738e2966ac`). It forwards `Authorization` and
  other viewer request context while replacing the viewer `Host` with the ALB
  origin host. API caching remains disabled. This managed policy forwards
  cookies as a documented tradeoff even though application auth uses bearer
  tokens rather than cookies.
- The ALB origin is obtained from `LoadBalancer.DNSName`, uses HTTP port 80 and
  receives `X-FIP-Origin-Verify` as a CloudFront custom origin header.

## Explicit deployment states

The same template supports both states through `OriginLockdownEnabled`.

| Contract | Deployment A (`false`) | Deployment B (`true`) |
|---|---|---|
| ALB SG port 80 | `0.0.0.0/0` | CloudFront origin prefix list only |
| ALB default action | forward to frontend target group | fixed HTTP 403 |
| Header rule | matching token forwards | matching token forwards |
| Prefix-list parameter | may be empty | required by template Rule |
| Cognito deletion protection | `INACTIVE` for rollback-safe first creation | `ACTIVE` after verified lockdown |

The public and prefix-list ingress rules are conditional alternatives; there is
no intended steady state in which both are active. Deployment B must not proceed
unless the current managed prefix-list ID, weight and ALB-SG quota are rechecked.

## Origin verification token handling

`OriginVerificationToken` is `NoEcho`, accepts only base64url text of length
43-128, and must be generated at deployment time from at least 32 bytes of
cryptographic entropy. Never commit, print, log, output, embed in the frontend,
or place a real value in the example parameter file.

`NoEcho` only redacts ordinary CloudFormation presentation. Principals with
sufficient CloudFront/ELB/configuration permissions can inspect relevant
configuration. Treat the value as a rotatable origin-verification token, not as
a permanently unknowable secret.

For a controlled rotation without losing customer reachability:

1. Create and review an update to State A; execute only after approval.
2. Generate a new token without printing it, update the State-A stack, and wait
   for CloudFront to report `Deployed` and the ECS service to remain stable.
3. Re-resolve the prefix list and quota, create/review State B, and execute only
   after approval.

This procedure temporarily restores public ALB access and therefore requires a
reviewed maintenance window. Do not attempt an unreviewed single-step rotation
in locked State B.

## Deployment A procedure (future authorized phase only)

Deployment A has a hard database prerequisite. First create and review the
separate `db-access-prerequisite.yaml` stack. Do not execute it without the
exact `EXECUTE DB ACCESS PREREQUISITE` gate. After execution is separately
approved, its one-off task must configure and verify the writer on the existing
RDS database before `ApplicationDatabaseWriterSecretArn` is supplied to this
main stack. Creating the prerequisite stack does not run the task or update the
application service. See `database-access-architecture.md` for the exact grants
and rollback boundary.

Deployment A uses `OriginLockdownEnabled=false`. It may create CloudFront,
Cognito, the three groups, origin policies, managed-login branding and the
origin-header listener rule; update the ECS task role/definition/service and
outputs; and retain public ALB port 80 plus the listener's default forward.
Cognito deletion protection is `INACTIVE` in this rollback-sensitive creation
state and becomes `ACTIVE` only with approved Deployment B.

Before a change set, recheck identity, account, region, stack stability, branch,
HEAD, worktree, exact image digests, and Cognito-domain-prefix availability.
Generate the real token only in that authorized phase. Create and inspect the
change set with `CAPABILITY_IAM`, but never execute it without the exact approval
required by the controlling deployment procedure.

Stop if the proposed update deletes or replaces RDS, VPC, ALB, ECS cluster,
target group or import bucket, changes the database unexpectedly, or includes a
data migration.

After an approved execution and stable stack, create the bootstrap user through
the normal Cognito invitation workflow without setting or displaying a static
password. Assign exactly the `admin` app group and verify the user is enabled
with no other app group. The invited administrator then manually verifies the
temporary-password/new-password/TOTP/PKCE login and logout flow.

Deployment A smoke gates are CloudFront HTTPS reachability, HTTP-to-HTTPS
redirect, safe `/api/auth/config`, unauthenticated protected API HTTP 401,
Managed Login reachability, exact callback/logout URLs, valid access-token
forwarding, correct `/api/auth/me`, and customer/operator/admin backend checks.
Direct ALB access remains expected in State A.

## Deployment B procedure (future authorized phase only)

Deployment B is allowed only after the user explicitly confirms the successful
manual login, logout and role behavior. Re-resolve
`com.amazonaws.global.cloudfront.origin-facing` in `eu-west-1`, verify its
current weight plus the ALB-SG quota, and pass that ID with
`OriginLockdownEnabled=true`.

The reviewed change set should only replace the public ALB ingress with the
prefix-list ingress and change the default listener action to fixed 403. It must
not replace protected resources or alter data. After separately approved
execution, direct ALB requests without the origin header must be denied while
CloudFront and authenticated APIs remain healthy.

## Data and runtime safety retained

The application task remains Fargate Linux/ARM64 and non-root. Nginx listens on
8080 and proxies `/api` to `127.0.0.1:8000`. PostgreSQL uses the restricted
application role and read-only transaction defaults for customer-serving
access. RDS remains private, encrypted and TLS-required. The established
versioned SQLite-to-PostgreSQL migration, reconciliation, materialization and
atomic dataset activation workflow is unchanged and is not run by either
security deployment state.

The template continues to use the existing stack resources and retains the
private import bucket, isolated RDS subnets, log groups, task definitions and
migration controls. Image tags must be immutable full-SHA tags and resolved to
digests before any future change set.

## Cost and operational caveats

The existing Fargate, ALB, RDS, public IPv4, secrets and log costs remain the
dominant monthly spend. CloudFront adds request/data-transfer charges and
Cognito adds monthly-active-user charges above applicable service allowances;
both depend on pilot traffic. The previous low-traffic estimate must be refreshed
with the AWS Pricing Calculator before execution because prices and usage are
time-dependent. Add budgets/alarms before broader distribution.

No WAF, custom domain, end-to-end TLS, Multi-AZ database, private ECS egress or
production availability claim is introduced by this pilot design.
