# 6. Deployment and status

## Deployment status

The platform has been deployed to AWS once. That environment was provisioned manually
through the AWS console and CLI under time pressure, and it is still running.

The manual deployment and the infrastructure code in this repository are separate. The
deployed environment was not created from the Terraform definitions, does not correspond to
the `environments/dev` or `environments/staging` roots, and was not deployed through the
CI/CD workflows. The Terraform definitions themselves remain unexecuted: no `init` against
a real backend, `plan`, `apply` or OIDC exchange has been performed.

This has several consequences, which are the main risks carried into the handover:

- The running environment is not reproducible from this repository. There is no committed
  definition that describes it, so it cannot be recreated from source if it is lost or
  modified.
- The state of the running environment is not known to the repository. Any difference
  between it and the architecture described below is undocumented.
- The metrics, alarms and log groups defined in `config/observability.json` are created by
  Terraform. Because Terraform was not applied, no corresponding CloudWatch resources
  exist for the running environment, and it is therefore unmonitored.
- Deployment did not use the GitHub OIDC path described below. The credentials used for
  the manual deployment sit outside that model and should be audited and rotated.
- The rollback and cutover procedures assume immutable, Terraform-managed task definitions
  and do not apply to the environment as provisioned.
- The environment incurs ongoing cost against no approved budget, and holds whatever data
  was loaded into its database.

The material below describes the intended target architecture. Except where stated, it
describes a validated design rather than the environment that is currently running.

## Target architecture

```text
Browser ──► CloudFront ──► private frontend S3 bucket
   │
   └──► Regional WAF ──► public ALB ──► ECS Fargate API ──► RDS PostgreSQL (isolated subnets)
                                              │
                                              └──► SQS FIFO ──► ECS Fargate workers
                                                                    │
                                              ┌─────────────────────┤
                                              ▼                     ▼
                          raw / validated / curated / exports S3   RDS
                                              ▲
             disabled EventBridge schedule ──► Step Functions
```

Network boundaries: the ALB is placed in public subnets only; API and worker tasks have no
public IP and run in private application subnets; PostgreSQL is placed in isolated subnets
with no internet route.

## Terraform

`infra/terraform/` contains 26 files and approximately 103 resource blocks. A single
`modules/platform` module covers network, compute, orchestration, edge, security,
observability, IAM, storage, RDS, KMS and GitHub OIDC. The `environments/dev` and
`environments/staging` roots consume the same module and differ only in variables.

No AWS access key is accepted as a Terraform input. Deployment identity uses GitHub OIDC,
with the trust policy pinned to `aud=sts.amazonaws.com` and to
`repo:<owner>/<repository>:environment:<environment>`. Infrastructure bootstrap and state
ownership are not granted to the artefact deployment role.

Cost figures in this repository represent a planning envelope rather than a price
quotation.

## Continuous integration

`.github/workflows/ci.yml` defines seven workstreams and an aggregate `required-gate` job.

| Job | Scope |
|---|---|
| `backend-quality` | Hash-locked install, compile, blocking Flake8, mypy, pytest, 70% coverage floor on `bff` |
| `frontend-quality` | `npm ci`, oxlint, unit tests, production build, bundle budgets, Playwright and axe at six viewports |
| `dependency-security` | pip and npm vulnerability, licence and secret scanning |
| `codeql` | Python and JavaScript static analysis |
| `container-security` | Digest-pinned build, CycloneDX SBOM, Trivy, non-root, size, no-data and health assertions |
| `terraform-security` | Offline contract check, `fmt`, read-only provider locks, `init -backend=false`, `validate`, Trivy configuration scan |
| `postgresql-migration` | Empty-database Alembic upgrade, integration tests, migration and reconciliation fixtures, API golden tests, pool smoke test |

`deploy-staging.yml` and `deploy-production.yml` are manual-dispatch only, require an exact
confirmation phrase, accept no access-key inputs, and stage through protected GitHub
environments.

## Delivery status

The distinction throughout this section is between components that have been built and
components that have been executed. A substantial part of the system is implemented and
tested locally. Little of it has been exercised against a real identity provider or
production load, and the one AWS deployment that exists was provisioned manually rather
than from the code in this repository.

Overall production status is `NO-GO`. The running environment is a one-off deployment
rather than an approved production environment.

### Delivered and verified locally

PostgreSQL as the default runtime; absence of a production SQLite dependency, supported by
fail-closed modes, a data-free image and a test-enforced import boundary; lossless
deterministic migration with 18 shadow projections and zero differences; authentication,
RBAC and the admin plane; proxy, rate limiting, audit and redaction; performance and
materializations; durable pipelines and jobs; the observability contract with 21 metrics
and 15 alarms defined; backup, restore and dataset rollback; a responsive and accessible
frontend; and CI/CD workflow definitions.

### Partially delivered

| Item | Outstanding |
|---|---|
| AWS deployment | One environment was provisioned manually and is running. It was not created from the Terraform definitions, is not reproducible from source, and has no CloudWatch monitoring. |
| Governance, retention, privacy | Controls are implemented and destructive deletion is disabled. Data owners, legal review, licence status, RPO and RTO are unresolved. `policy_status` is `proposed`. |
| Terraform definitions | Offline validation passes. `init`, `validate`, `plan`, security scans and provider locks are untested. |

### Not delivered

Reproducible, code-defined AWS deployment; automated deployment through the CI/CD
workflows; GitHub repository configuration including branch protection, environments,
required reviewers and OIDC trust; complete DACH grant transactions, for which no source
exists; enrichment accuracy validation, for which no labelled ground truth exists; a
client-approved score definition; and production load and concurrency verification.

## Open blockers

1. The running AWS environment is undocumented and unmanaged. It was provisioned by hand,
   is not described by any committed definition, and has no monitoring. It should be
   audited, its credentials rotated, and a decision taken on whether to reconcile it with
   the Terraform definitions or replace it with a code-defined environment.
2. Terraform provider locks cannot be generated under the available registry
   authorization, and the CI job uses `-lockfile=readonly`. Until this is resolved, the
   required gate must remain unconfigured or expected-red. This blocks reproducible
   infrastructure deployment.
3. Governance ownership is unassigned. `policy_status` is `proposed`, legal and licence
   review has not occurred, and all eight sources in `config/source-pipelines.json` carry
   `legal_status: unresolved`, which prevents schedule enablement. This blocks production
   data operation.
4. No identity provider has been connected. OIDC validation is implemented and unit-tested
   against synthetic JWKS, but no real issuer, audience or key set has been exercised.
5. No approved alarm thresholds or cost budget exist. The USD 500 threshold in
   `config/observability.json` is a proposed fail-safe.
6. The relevance score has no approved definition. It is configured as `experimental` with
   example weights and should not be presented as a client-approved measure or a
   prediction.

## Prerequisites for a reproducible AWS deployment

The steps below apply to replacing the current manual environment with one defined in code.

1. Audit the running environment, record what exists, and rotate the credentials used to
   create it.
2. Resolve the provider lock blocker and commit the locks.
3. Create GitHub environments with required reviewers and branch protection.
4. Create the AWS OIDC identity provider and deployment role with the pinned trust subject.
5. Provision Terraform state backend ownership separately from the deployment role.
6. Run `terraform plan` in the development environment and review each resource.
7. Obtain owner approval for the alarm thresholds and cost envelope.
8. Resolve the `legal_status` entries before enabling any ingestion schedule.
9. Follow the cutover procedure in [5. Operating](05-operating.md), which requires explicit
   approval at each production step.

## Limitations and future outlook

The items below are known weaknesses rather than defects. They are recorded so that the
receiving team can judge where the remaining effort lies.

### Infrastructure and delivery

| Limitation | Outlook |
|---|---|
| The running AWS environment was provisioned manually and is not reproducible from code | Audit what exists, then either import it into Terraform or recreate it from `infra/terraform/` and decommission the manual environment |
| The CI/CD deployment workflows have never been used; deployment was performed by hand | Complete the GitHub environment and OIDC configuration so that `deploy-staging.yml` becomes the only deployment path |
| The running environment has no CloudWatch metrics, alarms or dashboards, because those are created by Terraform | Applying the Terraform definitions creates the 21 metrics and 15 alarms already defined in `config/observability.json` |
| Terraform provider locks cannot be generated, so the CI Terraform job cannot pass | Resolve registry authorization and commit the locks |
| No identity provider has been connected; authentication has only been exercised against synthetic JWKS | Connect a real issuer and verify the OIDC path end to end |

### Test and quality coverage

| Limitation | Outlook |
|---|---|
| The 70% coverage floor applies to the `bff` package only. Coverage of `src/pipelines/`, `src/scrapers/` and `src/preprocessing/` is materially lower | Extend the coverage gate to the pipeline packages, at a threshold appropriate to each |
| The default test suite runs against the legacy SQLite runtime, so PostgreSQL-specific regressions can pass unnoticed | Run the opt-in PostgreSQL integration modules in CI with `RUN_POSTGRES_INTEGRATION=1` |
| No load or concurrency testing has been performed against a deployed environment | Establish a load profile and run it against a code-defined environment before production use |
| Accessibility and browser coverage is limited to axe at six viewports in a single browser | Broaden the browser matrix if wider support is required |

### Code structure

| Limitation | Outlook |
|---|---|
| Two API implementations exist in the tree; the legacy SQLite layer is retained for migration compatibility | Removing the legacy layer is the largest available simplification now that migration work is closed. The import boundary is test-enforced in the meantime |
| `App.tsx` is approximately 4,100 lines and concentrates frontend change risk | The file is sectioned and documented; decompose incrementally |

### Data and documentation

| Limitation | Outlook |
|---|---|
| The dataset is a bounded snapshot, and enrichment accuracy is unvalidated against labelled ground truth | State the limits from [1. Overview](01-overview.md) whenever coverage is discussed, and keep evidence, confidence and review flags visible in any interface |
| Prose documentation drifts from the code. Preparing this set surfaced several claims that no longer matched the implementation, including a stale enrichment rule version and an incorrect description of the scoring behaviour | Detailed reference is now kept in the code, generated OpenAPI and `config/*.json`. Prose should stay at the level of this documentation set |
| Local build artifacts, in particular `src/data/charities.db`, can fall out of step with the reconciled dataset | Treat the file as a build artifact and rebuild it as described in [5. Operating](05-operating.md) |

## Suggested first steps for the receiving team

1. Audit the running AWS environment, record its resources and cost, and rotate the
   credentials used to create it.
2. Follow [5. Operating](05-operating.md) and confirm that the system runs locally.
3. Run the full test suite together with the opt-in PostgreSQL integration modules.
4. Decide how to bring the running environment under code management.
5. Assign named owners against the governance blocker before planning production data
   operation.
6. Decide whether to remove the legacy SQLite API layer.
