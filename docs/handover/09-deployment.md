# Deployment

> **Read this first.** All infrastructure and deployment automation in this repository
> exists as code and has been checked offline. **None of it has ever been executed
> against AWS.** No Terraform `init` against a real backend, no `plan`, no `apply`, no
> image push, no OIDC exchange, no ECS deployment. Treat everything below as a validated
> design, not as a running system.

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

Network boundaries:

- The ALB occupies **public** subnets only.
- API and worker tasks have **no public IP** and run in private application subnets.
- PostgreSQL sits in **isolated** private database subnets with no internet route.
- Development uses one NAT gateway and omits interface endpoints to cap fixed cost.
  Staging uses one NAT per availability zone plus private ECR, logs, secrets and SQS
  endpoints. Both use the S3 gateway endpoint.

## Terraform layout

```
infra/terraform/
  modules/platform/          # the entire platform, ~103 resources
    network.tf         18    # VPC, subnets, routes, NAT, endpoints
    compute.tf         19    # ECS cluster, task definitions, services, autoscaling
    orchestration.tf   11    # SQS FIFO, Step Functions, EventBridge (disabled)
    edge.tf            10    # ALB, listeners, CloudFront, Route53, WAF
    security.tf        10    # Security groups and rules
    observability.tf    8    # Log groups, metric alarms
    iam.tf              7    # Task and execution roles
    storage.tf          7    # S3 buckets, ECR repositories, lifecycle policies
    rds.tf              5    # PostgreSQL instance, subnet group, parameters
    kms.tf / kms_policy.tf   # Customer-managed keys
    github_oidc.tf      3    # OIDC provider and deployment role trust
  environments/dev/
  environments/staging/
```

Both environments consume the same `platform` module and differ only in variables. Copy
`terraform.tfvars.example` and fill it in. **No AWS access key is a Terraform input** —
deployment identity is GitHub OIDC only, and infrastructure bootstrap/state ownership is
deliberately not granted to the artefact deployment role.

The OIDC trust policy pins `aud=sts.amazonaws.com` and exactly
`repo:<owner>/<repository>:environment:<environment>`.

## CI — `.github/workflows/ci.yml`

Seven required workstreams plus one aggregate `required-gate` job:

| Job | Enforces |
|---|---|
| `backend-quality` | Hash-locked install, compile, blocking Flake8 (E9,F63,F7,F82), mypy, pytest, **70% coverage floor** on `bff` |
| `frontend-quality` | `npm ci`, oxlint, unit tests, production build, bundle budgets, Playwright/axe at six viewports |
| `dependency-security` | pip and npm vulnerability, licence and secret scanning |
| `codeql` | Python and JavaScript SAST |
| `container-security` | Digest-pinned build, CycloneDX SBOM, Trivy, non-root/size/no-data/health assertions |
| `terraform-security` | Offline contract check, `fmt`, read-only provider locks, `init -backend=false`, `validate`, Trivy config scan |
| `postgresql-migration` | Empty-database Alembic upgrade, PostgreSQL integration, deterministic migration/reconciliation fixtures, API golden tests, pool/performance smoke |

SBOMs are generated deterministically from the committed lockfiles; committed evidence
records 70 Python and 128 npm components. The licence gate inspected 155 installed
components and found zero unknown declarations and no AGPL/SSPL/GPL declaration.

### Known blocker

The Terraform job requires committed provider locks and uses `-lockfile=readonly`. **Those
locks could not be generated under the available registry authorization.** Consequently the
GitHub required gate must remain either unconfigured or expected-red until this is
resolved. This has never been represented as a passing remote CI run — see
[12-acceptance-register.md](12-acceptance-register.md).

## Deployment workflows

`deploy-staging.yml` — manual dispatch only, requiring the exact confirmation phrase
`I_APPROVE_STAGING`. No access-key inputs. Three stages:

1. `build` — build and scan immutable artefacts
2. `publish-and-plan` — `staging-publish` protected environment; push images, Terraform plan
3. `deploy` — `staging` protected environment; requires approval

`deploy-production.yml` follows the same shape with production environment protection.

Both depend on GitHub configuration that does not exist yet: branch protection,
environments, required reviewers, OIDC trust and action SHA resolution. **Workflow files
alone do not prove configuration.**

## What must happen before any AWS deployment

1. Resolve the Terraform provider lock blocker and commit real locks.
2. Create GitHub environments with required reviewers and branch protection.
3. Create the AWS OIDC identity provider and deployment role with the pinned trust
   subject.
4. Provision Terraform state backend ownership separately from the deployment role.
5. Run `terraform plan` in development and review every resource.
6. Obtain owner approval for the alarm thresholds and cost envelope in
   `config/observability.json` — the USD 500 threshold is a proposed fail-safe, not an
   approved budget.
7. Resolve the `legal_status: unresolved` entries in `config/source-pipelines.json` before
   enabling any ingestion schedule.
8. Follow `docs/remediation/cutover-runbook.md`, which requires fresh explicit approval at
   every production step.

## Reference documents

| Topic | Document |
|---|---|
| Full infrastructure contract and cost envelope | `docs/remediation/terraform-aws-infrastructure.md` |
| Offline validation result and exact commands to run later | `docs/remediation/terraform-validation.md` |
| CI/CD detail | `docs/remediation/ci-cd-guide.md` |
| Cutover procedure | `docs/remediation/cutover-runbook.md` |
| Rollback procedure | `docs/remediation/rollback-runbook.md` |
| Target-state architecture rationale | `docs/remediation/aws-postgres-architecture.md` |

The cost figures in `terraform-aws-infrastructure.md` are a planning envelope, not a live
price quotation.
