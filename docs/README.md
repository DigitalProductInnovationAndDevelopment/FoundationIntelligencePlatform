# Foundation Intelligence Platform — technical documentation

This directory contains the technical documentation for the platform. It consists of six
pages and a set of alarm runbooks.

The documentation describes the system at a high level: its purpose, the relationship
between its components, the business rules it implements, how to run it, and its current
delivery status. Detailed reference information is maintained in the code rather than
duplicated here. Route tables, field lists, environment-variable catalogues and module
inventories are therefore not reproduced in this directory; the corresponding sources are
listed below.

| Page | Contents |
|---|---|
| [1. Overview](01-overview.md) | Purpose, data sources, capabilities and limitations |
| [2. Architecture](02-architecture.md) | Components, runtime selection, request lifecycle |
| [3. Data model](03-data-model.md) | Schema organization, dataset versioning, invariants |
| [4. Domain rules](04-domain-rules.md) | Classification, aggregation and display rules |
| [5. Operating](05-operating.md) | Setup, configuration, health, recovery procedures |
| [6. Deployment and status](06-deployment-and-status.md) | AWS design, CI, delivery status |
| [Alarm runbooks](runbooks.md) | One procedure per configured alarm |

## Reference information in the code

| Subject | Source |
|---|---|
| API surface | `GET /docs` on a running instance (OpenAPI, generated from the code) |
| Request and response fields | `src/bff/schemas.py` |
| SQL and query behaviour | `src/bff/postgres/*_repository.py` |
| Database schema | `alembic/versions/` |
| Environment variables | `.env.example` and the validation in `src/bff/config.py` |
| Policy and thresholds | `config/*.json`, read at runtime |
| Test coverage | `src/tests/` and `.github/workflows/ci.yml` |
| Enrichment taxonomy and rules | `src/preprocessing/enrichment.py` |
| Frontend filter semantics | `frontend/src/lib/grantScope.ts` |

Each module carries a docstring describing its responsibility and any constraints on
modifying it.

## Behaviour that is not apparent from the code structure

1. There is no anonymous access. With the shipped setting `AUTH_MODE=disabled`, the login
   route returns 404 and all API routes return 401. Development authentication must be
   enabled explicitly; see [5. Operating](05-operating.md).
2. Two API implementations exist in the tree. `src/bff/postgres/routes.py` is the active
   surface; `src/bff/charity.py` is a legacy SQLite implementation. The selection is made
   at import time in `src/bff/main.py`.
3. SQLite is not the operational datastore. It is retained as a migration source and a
   shadow-comparison fixture, and is rejected outside development and test.

## Status

The system runs locally against PostgreSQL. It has been deployed to AWS once, as a
manually provisioned environment that is still running and is not reproducible from the
infrastructure code in this repository. See
[6. Deployment and status](06-deployment-and-status.md) for the delivery position and the
known limitations.
