# Terraform Validation Status

Date: 2026-07-29

## Local result

- Terraform configuration: 26 `.tf` files, 101 resource blocks and 58 AWS
  resource types across one platform module plus dev/staging roots.
- Offline bracket/string/comment structure check: `PASS`.
- Offline required-resource and environment coverage check: `PASS`.
- Offline security contract: `PASS` for private/deletion-protected RDS,
  encrypted/non-public/non-destructive S3, immutable ECR/images, private and
  non-root ECS, fail-closed schedules, exact GitHub OIDC subject/audience and
  bounded wildcard-resource contexts.
- Python contract tests: `PASS`.
- `terraform fmt`, `fmt -check`, `init -backend=false`, `validate`: `NOT TESTED`.
- Provider lock generation: `NOT TESTED`; no `.terraform.lock.hcl` is claimed.
- Checkov/tfsec/tflint/trivy: `NOT TESTED` because none is installed locally.
- Terraform plan: `NOT TESTED` because Terraform/providers are unavailable and
  AWS read access is outside the approved boundary.
- AWS changes/state/DNS/certificate/provider downloads: none.

## Exact blockers

`terraform version` returned:

```text
zsh:1: command not found: terraform
```

No `hashicorp/terraform` image is present in the local Docker image store.
`tofu`, `tflint`, `checkov`, `tfsec`, `trivy` and `semgrep` are also absent.
Downloading Terraform providers would contact `registry.terraform.io`, which
is outside the approved dependency sources. No workaround was used and no
successful provider-dependent validation is claimed.

## Remaining exact validation

After explicit approval for the Terraform distribution/provider sources, pin
the Terraform CLI version and hashes, generate and commit provider locks for
supported platforms, then run:

```zsh
terraform fmt -recursive infra/terraform
terraform fmt -check -recursive infra/terraform
terraform -chdir=infra/terraform/environments/dev init -backend=false
terraform -chdir=infra/terraform/environments/dev validate
terraform -chdir=infra/terraform/environments/staging init -backend=false
terraform -chdir=infra/terraform/environments/staging validate
```

Run an approved, version-pinned Terraform security scanner and resolve every
high/critical finding. A staging plan additionally requires explicit AWS
read-access approval and real non-secret variables; it must use no remote
state/lock under the current workflow. Apply remains prohibited.
