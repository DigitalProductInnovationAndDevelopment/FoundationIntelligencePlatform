# Acceptance register

The formal delivered / not-delivered position at handover.

Prepared: 2026-08-06. Branch: `91-clean-up-code-for-aws-integration`.

**Overall production status: `NO-GO`.** AWS mutations performed: **none**. Paid external
API calls performed: **none**. Remote push performed: **none** — the work exists as local
commits on the handover branch.

## How to read this

The distinction that runs through this register is between **built** and **executed**.
A great deal of this system is implemented, tested locally, and correct as far as local
evidence can show. Very little of it has been exercised against real AWS infrastructure,
a real identity provider, or production load. Both facts are true simultaneously, and
conflating them is the main risk at handover.

## Delivered and verified locally

| Requirement | Status | Evidence | Residual limitation |
|---|---|---|---|
| PostgreSQL as default runtime | **PASS** | Transition config, Compose, production route tests | AWS unexecuted |
| No production SQLite dependency or fallback | **PASS** | Fail-closed modes; data-free image; subprocess import-boundary test | Legacy code remains for local migration compatibility |
| Lossless deterministic migration | **PASS** | Phase-4 manifest; 18 shadow projections with zero differences | New source deltas require a rerun |
| Duplicate and anomaly preservation | **PASS** | Migration controls and reports | Business-owner review outstanding |
| Authentication, RBAC, admin plane | **PASS locally** | 11 dedicated security tests; route inventory classifies every route | Real IdP never connected |
| Proxy, rate limiting, audit, redaction | **PASS locally** | Security and observability tests | Distributed edge behaviour untested |
| Performance and materializations | **PASS locally** | Phase-6 metrics; integration tests; 204,220 aggregate rows | Production load unknown |
| Durable pipelines and jobs | **PASS locally** | Phase-8 outbox and worker tests | SQS and S3 execution untested |
| Observability contract and runbooks | **PASS locally** | Phase-10 contract; 21 metrics, 15 alarms defined | No CloudWatch destination exists; thresholds unapproved |
| Shadow comparison and goldens | **PASS locally** | 18 projections, zero differences, golden fixtures | Production observation unexecuted |
| Backup, restore, rollback | **PASS locally** | Full `pg_dump`/restore and dataset switch-back proven | RDS and PITR restore untested |
| Frontend responsive and accessible | **PASS locally** | Phase-7 build, E2E, axe at six viewports | Limited browser matrix |
| CI/CD workflow definitions | **PASS locally** | Phase-12 workflows; offline validation | See blocker below — staging is `NO-GO` |
| Immutable audit baseline | **PASS** | Exact SQLite and audit checksums | Recheck before any push |

## Partially delivered

| Requirement | Status | What is missing |
|---|---|---|
| Governance, retention, privacy | **PARTIAL** | Controls implemented and destructive deletion disabled. **Data owners, legal review, licence status, RPO and RTO are unresolved.** `policy_status` is `proposed`; `production_activation_approved` is `false`. Production `NO-GO` |
| Terraform definitions | **PARTIAL** | 26 files, 103 resource blocks, 58 resource types pass offline validation. `fmt`, `init`, `validate`, `plan`, security scans and **provider locks are not tested** |

## Not delivered

| Item | Status | Note |
|---|---|---|
| AWS deployment of any kind | **NOT EXECUTED** | No API call, no state access, no plan, no apply, no image push, no OIDC exchange |
| GitHub repository configuration | **NOT EXECUTED** | Branch protection, environments, required reviewers, OIDC trust, action SHA resolution all absent |
| Remote push | **NOT PERFORMED** | Local commits only; explicit approval required |
| Complete DACH grant transactions | **MISSING** | No source currently supplies this coverage |
| Enrichment accuracy validation | **NOT VERIFIED** | Coverage is measured; labelled ground-truth data does not exist |
| Client-approved score definition | **BLOCKED** | No approved target, weights or decision policy exists in the repository |
| Production load and concurrency verification | **NOT EXECUTED** | Local benchmarks only |

## Open blockers, ranked

1. **Terraform provider locks cannot be generated** under the available registry
   authorization. The CI Terraform job uses `-lockfile=readonly` and requires committed
   locks. Until resolved, the GitHub required gate must remain unconfigured or
   expected-red. This blocks all infrastructure deployment.
2. **Governance ownership is unassigned.** `config/data-governance.json` lists six data
   owners but `policy_status` remains `proposed`, and legal/licence review has not
   occurred. At least one source in `config/source-pipelines.json` carries
   `legal_status: unresolved`, which fail-closes schedule enablement. This blocks
   production data operation.
3. **No identity provider has ever been connected.** OIDC validation is implemented and
   unit-tested against synthetic JWKS, but no real issuer, audience or key set has been
   exercised.
4. **No approved alarm thresholds or cost budget.** The USD 500 threshold in
   `config/observability.json` is a proposed fail-safe. Owner approval is required before
   it means anything.
5. **The relevance score has no approved definition.** It ships as
   `configuration_status: "experimental"` with example weights. It must not be presented
   as a client-approved measure or as a prediction.

## Known risks to accept or mitigate

| Risk | Impact | Mitigation available |
|---|---|---|
| Test suite defaults to the legacy SQLite runtime | PostgreSQL-specific regressions could pass the fast suite | Opt-in integration modules exist; run them in CI with `RUN_POSTGRES_INTEGRATION=1` |
| Coverage floor covers `bff` only | Pipeline, scraper and preprocessing coverage is materially lower | Extend the gate when those packages change |
| `App.tsx` is 4,102 lines | Concentrated change risk in the frontend | Sectioned and documented; decompose incrementally |
| Two API implementations in the tree | A developer may edit the legacy path by mistake | Documented in [02-architecture.md](02-architecture.md); import boundary is test-enforced. Consider deleting the legacy layer once migration work is closed |
| Dataset is a bounded snapshot | Coverage claims could be overstated externally | Capability register in [01-system-overview.md](01-system-overview.md) states every limit |
| Enrichment accuracy unvalidated | Classifications may be wrong in ways not measured | Evidence, confidence and review flags must remain visible in any UI |

## Data state at handover

| Item | Count |
|---|---|
| Organizations (reconciled active dataset) | 373 |
| Observed grant transactions | 302,546 |
| Charity Commission registry rows | 397,469 |
| Accepted registry-to-profile links | 345 |
| Philea organizations (`organization_level_only`) | 299 |
| Analytics aggregate rows | 204,220 |
| Grants explicitly unconverted (pre-1999 GBP) | 34 |
| Map known-country disclosure | 62.86% |
| Multi-country grant exclusions | 39 |

## Recommended first actions for the receiving team

1. Follow [08-running-and-operating.md](08-running-and-operating.md) end to end and
   confirm you get a running system. That validates the handover.
2. Run the full test suite plus the opt-in PostgreSQL integration modules.
3. Resolve blocker 1 (provider locks) before planning any AWS work.
4. Get named owners against blocker 2 (governance) before planning any production data
   operation.
5. Decide whether to delete the legacy SQLite API layer. It is the largest single
   simplification available.

## Source evidence

This register consolidates `docs/remediation/requirements-traceability.md`,
`docs/remediation/aws-postgres-progress.md` (the phase-by-phase ledger),
`docs/remediation/feature-test-matrix.md` and the Phase 4–13 evidence reports under
`docs/remediation/evidence/`. The immutable audit baseline is `docs/audits/` and must not
be edited.
