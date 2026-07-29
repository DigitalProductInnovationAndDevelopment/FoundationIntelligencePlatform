# Remediation documentation index

This index maps the required documentation without modifying `docs/audits/`.

| Requirement | Remediation document |
|---|---|
| README and local development | `README.md`, `local-development-guide.md` |
| PostgreSQL setup/migration | `postgresql-migration-guide.md`, `aws-postgres-schema.md`, Phase-4 evidence |
| AWS architecture | `aws-postgres-architecture.md`, `terraform-aws-infrastructure.md` |
| Terraform deployment | `terraform-validation.md`, `ci-cd-guide.md` |
| Authentication/RBAC/security | `security-authentication-rbac-guide.md`, Phase-2/5 progress evidence |
| Data model/entity identity | `aws-postgres-schema.md`, `aws-postgres-architecture.md`, ADR-014/015 |
| Source register | `data-governance-register.md`, `pipeline-storage-contract.md` |
| Retention/privacy | `retention-privacy-guide.md`, Phase-9 evidence |
| Performance | Phase-6 performance JSON/Markdown and `frontend-bundle-budget.md` |
| Reconciliation | Phase-4 migration manifest/report and Phase-13 transition fixture/report |
| Operations/incidents | `observability-runbooks.md`, `runtime-transition-guide.md` |
| Backup/restore/rollback | `backup-restore-guide.md`, `rollback-runbook.md`, `cutover-runbook.md` |
| Cost estimate | `terraform-aws-infrastructure.md` (planning envelope, not live price claim) |
| CI/CD | `ci-cd-guide.md`, Phase-12 evidence |
| Environment variables | `environment-variable-reference.md` |
| Troubleshooting | `troubleshooting-guide.md` |
| Requirements traceability | `requirements-traceability.md` |
| Feature test matrix | `feature-test-matrix.md` |
| Remediation status | `aws-postgres-progress.md`, final acceptance report |

The immutable audit versions of traceability and feature matrices remain in
`docs/audits/`; remediation status is maintained only in this directory.
