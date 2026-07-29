# Remediation requirements traceability

The immutable source audit remains in `docs/audits/requirements-traceability.md`.
This table records implemented remediation status and does not overwrite it.

| Requirement | Status | Implementation/evidence | Remaining limitation | AWS/production |
|---|---|---|---|---|
| PostgreSQL default runtime | PASS | transition config, Compose, production route tests | None locally | AWS unexecuted |
| No production SQLite dependency/fallback | PASS | fail-closed modes, data-free image/import boundary | Legacy code remains local migration compatibility | Production unexecuted |
| Lossless deterministic migration | PASS | Phase-4 manifest and 18 Phase-13 projections | New source deltas need rerun | No AWS impact |
| Preserve duplicates/anomalies | PASS | migration controls and reports | Business-owner review remains | No AWS impact |
| Authentication/RBAC/admin plane | PASS locally | security tests and security guide | Real IdP/settings unverified | External blocker |
| Proxy/rate limit/audit/redaction | PASS locally | security/observability tests | Distributed edge behavior untested | AWS unexecuted |
| Performance/materializations | PASS locally | Phase-6 metrics and integration tests | Production load unknown | AWS unexecuted |
| Durable pipelines/jobs | PASS locally | Phase-8 outbox/worker tests | SQS/S3 execution untested | AWS unexecuted |
| Governance/retention/privacy | PARTIAL | Phase-9 controls; deletion disabled | Owners/legal/licence/RPO/RTO unresolved | Production NO-GO |
| Observability/runbooks | PASS locally | Phase-10 contract and runbooks | CloudWatch destinations/threshold approval absent | AWS unexecuted |
| Terraform definitions | PARTIAL | 26 files/103 blocks/58 resource types offline pass | fmt/init/validate/plan/scans/provider locks not tested | No resources changed |
| CI/CD definitions | PASS locally | Phase-12 workflows/offline validation | GitHub settings/OIDC/workflow execution untested | Staging NO-GO |
| Shadow comparison/goldens | PASS locally | 18 projections, zero differences, golden fixture | Production observation unexecuted | AWS unexecuted |
| Backup/restore/rollback | PASS locally | full pg_dump/restore and dataset switch-back | RDS/PITR restore untested | AWS unexecuted |
| Frontend responsive/accessibility | PASS locally | Phase-7 build/E2E/axe evidence | Production/browser matrix limited | Production unexecuted |
| Immutable audit baseline | PASS | exact SQLite/audit checksums | Must recheck before push | No AWS impact |
| Git push | NOT TESTED | 19 local commits ahead at Phase-13 checkpoint | Explicit approval required | No push performed |
