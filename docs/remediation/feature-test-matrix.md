# Remediation feature test matrix

| Journey/control | Unit/contract | Local PostgreSQL | Browser/container | Status |
|---|---:|---:|---:|---|
| Dashboard overview/totals | Yes | Golden projection | E2E | PASS |
| Beneficiary map | Yes | Country projection | E2E | PASS |
| Map relationships | Yes | 100-flow projection | E2E lazy-load | PASS |
| Date/country/programme filters | Yes | Exact projections | E2E URL state | PASS |
| Donor/recipient filters | Yes | Exact projections | E2E URL state | PASS |
| Monthly/yearly trends | Yes | Exact full periods | E2E | PASS |
| Donor/recipient rankings | Yes | Top-25 exact identity/order | UI tests | PASS |
| Registry search/pagination | Yes | Exact first page | E2E | PASS |
| Profile detail | Yes | Exact selected fields | E2E | PASS |
| Grant list/drill-down | Yes | Exact projection/repository | E2E | PASS |
| Sankey | Yes | Exact top flows | E2E | PASS |
| Experimental score | Yes | Golden components | E2E contract | PASS |
| News | Yes, empty/error paths | No live call | Network disabled | PARTIAL |
| Pipeline status | Yes | Durable job repository | Auth E2E | PASS locally |
| Manual refresh permissions | Yes | Transactional enqueue | Operator-only E2E | PASS locally |
| Auth/RBAC/rate/proxy | Yes | Audit/idempotency integration | API tests | PASS locally |
| Migration/reconciliation | Yes | Full active dataset | N/A | PASS |
| Shadow non-latency/differences | Yes | 18 projections, zero diff | Runtime middleware contract | PASS locally |
| Dataset rollback | Yes | Prior and original activated | N/A | PASS |
| Full logical restore | Script contract | Isolated full restore | Docker PostgreSQL | PASS |
| Docker no-data/non-root/health | Contract | Stack health | Image inspect | PASS locally |
| Terraform provider validation | Offline only | N/A | No provider tool | NOT TESTED |
| AWS staging deployment | Workflow only | N/A | No AWS execution | NOT TESTED |
| Production deployment | Hard-disabled | N/A | No execution | NOT TESTED |
