# Phase 12 CI/CD Evidence

## Result

Gate 12 passes for local workflow/code readiness. Three syntactically valid
workflows define 24 required PR gate markers, manual OIDC-only staging
publish/plan/deploy through two protected environments, and a hard-disabled
production environment. No workflow or remote GitHub setting was executed or
verified.

## Local evidence

- Mypy 2.3.0 and four transitive dependencies are exact/hash locked; 13 typed
  production files pass.
- Deterministic CycloneDX SBOMs contain 70 Python and 128 npm components.
- The licence scan covers 155 installed components with no unknown or
  forbidden declaration; npm reports zero known vulnerabilities.
- 342 backend tests pass, 13 explicit live tests skip and 8 subtests pass.
- Frontend lint, 13 unit tests, build/bundle budgets and 8 Playwright/axe tests
  pass; 4 redundant viewport journeys intentionally skip.
- The local PostgreSQL performance smoke passes and the release gate confirms
  schema, active dataset, reconciliation, materialization, quality and DLQ.
- The `--pull=false` runtime image is data-free, 354,624,742 bytes, non-root,
  healthchecked and imports 14 governance policies, 21 metric definitions and
  8 source configurations. Image ID is
  `sha256:172dab7c1c7842b0b34f0991d97f8ae34391d36e6ece628db4a63672c36781e9`.

Python/container vulnerability scanners, Terraform/provider locks and remote
CI execution remain untested local/external blockers. The Terraform PR job
intentionally fails closed until reviewed provider locks exist. GitHub branch
rules, environment reviewers, OIDC trust and action commit-SHA pins are not
claimed.

SQLite and `docs/audits/` remain unchanged. No AWS action, state access,
Terraform plan/apply, image push, frontend upload, deployment or Git push
occurred.
