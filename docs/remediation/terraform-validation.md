# Terraform Validation Status

Date: 2026-07-29

## Local result

- Terraform configuration: 26 `.tf` files, 103 resource blocks and 58 AWS
  resource types across one platform module plus dev/staging roots.
- Offline bracket/string/comment structure check: `PASS`.
- Offline required-resource and environment coverage check: `PASS`.
- Offline security contract: `PASS` for private/deletion-protected RDS,
  encrypted/non-public/non-destructive S3, immutable ECR/images, private and
  non-root ECS, fail-closed schedules, exact GitHub OIDC subject/audience and
  bounded wildcard-resource contexts.
- Python contract tests: `PASS`.
- `terraform fmt` and `fmt -check -recursive`: `PASS` with Terraform `1.9.8`
  in a network-isolated Docker container.
- `init -backend=false`: `BLOCKED` after installing the local platform module;
  the AWS provider cannot be resolved from the authorised registries.
- `validate`: `BLOCKED` on the missing AWS provider after local module
  initialization; no provider-dependent success is claimed.
- Provider lock generation: `NOT TESTED`; no `.terraform.lock.hcl` is claimed.
- Checkov/tfsec/tflint/trivy: `NOT TESTED` because none is installed locally.
- Terraform plan: `NOT TESTED` because Terraform/providers are unavailable and
  AWS read access is outside the approved boundary.
- AWS changes/state/DNS/certificate/provider downloads: none.

## Exact blockers

The host still has no Terraform binary, but the explicitly approved Docker Hub
download resolved `hashicorp/terraform:1.9.8` to manifest digest
`sha256:18f9986038bbaf02cf49db9c09261c778161c51dcc7fb7e355ae8938459428cd`
(local image ID `sha256:97aaea908f872c3c60b75e9bffd6eeae34386c0e9671d6b2a1e30418ea702269`).
The first real parser run rejected an invalid single-line nested WAF block;
that syntax and the remaining canonical formatting were corrected. The final
network-isolated `fmt -check -recursive` passes.

`terraform init -backend=false -input=false` was then attempted in an isolated
temporary copy with `--network none`. It installed the local module and stopped
at:

```text
Failed to query available provider packages
Could not retrieve the list of available versions for provider hashicorp/aws
```

The container had no network and made no successful external contact. A
follow-up `validate` reported `Missing required provider`. The temporary copy
was removed. `tofu`, `tflint`, `checkov`, `tfsec`, `trivy` and `semgrep` remain
absent. Downloading Terraform providers would contact `registry.terraform.io`,
which is outside the approved dependency sources.

## Remaining exact validation

After explicit approval for the Terraform provider source, generate and commit
provider locks for supported platforms, then run:

```zsh
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
