# Start here — Foundation Intelligence Platform handover

This directory is the handover package for the Foundation Intelligence Platform. It is
written for the team receiving the codebase, not as a record of how it was built.

Prepared: 2026-08-06. Source branch: `91-clean-up-code-for-aws-integration`.

## What you are receiving in one paragraph

A proof-of-concept platform for exploring philanthropic foundations, charities and
observed grant relationships. A Python/FastAPI backend serves an async PostgreSQL
datastore to a React/Vite single-page UI. Data originates from cached Charity Commission,
360Giving and Philea sources, is consolidated and deterministically enriched by an
offline pipeline, then migrated into versioned PostgreSQL datasets. The system separates
source facts from inferred values throughout, and reports coverage gaps rather than
substituting zeros. It runs locally today. **It has never been deployed to AWS** — see
[12-acceptance-register.md](12-acceptance-register.md) before making any assumption about
production readiness.

## Reading path by role

**If you will run or extend the code, in order:**

1. [02-architecture.md](02-architecture.md) — how the pieces fit, and the dual-runtime split you must understand before editing anything
2. [08-running-and-operating.md](08-running-and-operating.md) — get it running locally
3. [03-backend-reference.md](03-backend-reference.md) and [04-frontend-reference.md](04-frontend-reference.md) — where code lives
4. [06-data-model.md](06-data-model.md) — the schema and its versioning rules
5. [13-domain-logic.md](13-domain-logic.md) — the business rules you must not break
6. [10-extending.md](10-extending.md) — the pattern to copy for each kind of change

**If you are evaluating what was delivered:**

1. [01-system-overview.md](01-system-overview.md) — capabilities and their honest limits
2. [12-acceptance-register.md](12-acceptance-register.md) — delivered vs. not delivered, and open risk
3. [09-deployment.md](09-deployment.md) — what infrastructure exists as code and what has actually been executed

**If you are operating it:**

1. [08-running-and-operating.md](08-running-and-operating.md)
2. [07-configuration.md](07-configuration.md)
3. [09-deployment.md](09-deployment.md)

## Document index

| Document | Contents |
|---|---|
| [01-system-overview.md](01-system-overview.md) | What the platform does, data sources, provenance model, capability register |
| [02-architecture.md](02-architecture.md) | Components, request lifecycle, data flow, the dual-runtime split |
| [03-backend-reference.md](03-backend-reference.md) | Package-by-package backend reference |
| [04-frontend-reference.md](04-frontend-reference.md) | React application structure and conventions |
| [05-api-reference.md](05-api-reference.md) | Complete route inventory with roles and idempotency |
| [06-data-model.md](06-data-model.md) | PostgreSQL schema, dataset versioning, the two-layer organization model |
| [07-configuration.md](07-configuration.md) | Environment variables and the `config/*.json` files |
| [08-running-and-operating.md](08-running-and-operating.md) | Local setup, Docker, migrations, workers, health and observability |
| [09-deployment.md](09-deployment.md) | Terraform, CI/CD, and execution status |
| [10-extending.md](10-extending.md) | How to add a scraper, pipeline, endpoint, repository, migration or view |
| [11-testing.md](11-testing.md) | Test suite map and quality gates |
| [12-acceptance-register.md](12-acceptance-register.md) | Delivered vs. not delivered, known risks, open items |
| [13-domain-logic.md](13-domain-logic.md) | Enrichment rules, geography semantics, aggregation and scoring business rules |

## Where the rest of the documentation lives

Two other documentation trees exist. Both are retained unchanged as project evidence.

- **`docs/remediation/`** — 29 documents produced during the PostgreSQL/AWS remediation,
  organised by project phase. These are the authoritative technical contracts for
  security, observability, governance and infrastructure, and this handover set links
  into them rather than duplicating them. The most useful are
  `aws-postgres-route-inventory.md`, `aws-postgres-schema.md`,
  `terraform-aws-infrastructure.md`, `ci-cd-guide.md`, and the four runbooks
  (`rollback`, `cutover`, `backup-restore`, `troubleshooting`).
- **`docs/audits/`** — the immutable audit baseline, including the original requirements
  traceability and feature-test matrices. Do not edit these; the maintained versions are
  in `docs/remediation/`.

A caution when reading `docs/remediation/`: those documents are phase-scoped and many
carry status caveats that were accurate at the time of writing. Where this handover set
and a phase document disagree about current state, this set is newer.

## The three things most likely to trip you up

1. **There is no anonymous access.** With the default `AUTH_MODE=disabled`, login returns
   404 and every API route returns 401. You must enable development authentication
   explicitly — see [08-running-and-operating.md](08-running-and-operating.md).
2. **Two API implementations exist in the tree.** `src/bff/charity.py` (legacy, SQLite)
   and `src/bff/postgres/routes.py` (current, PostgreSQL) are selected at import time.
   The PostgreSQL one is the live path. See [02-architecture.md](02-architecture.md).
3. **SQLite has not been removed, and is not the operational store.** It survives only as
   a local migration source and shadow-comparison fixture.
