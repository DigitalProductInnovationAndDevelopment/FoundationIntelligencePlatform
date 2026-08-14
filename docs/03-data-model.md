# 3. Data model

PostgreSQL is the authoritative operational datastore. The schema is managed exclusively
by Alembic. The `alembic_version` table should not be edited and DDL should not be applied
by hand.

The migrations in `alembic/versions/` are the authoritative reference for every table,
column, constraint and index. This page describes the organizing concepts required to read
them.

## Dataset versioning

Every serving row carries its dataset version in its primary and foreign keys. This allows
a candidate dataset to be loaded and reconciled alongside the approved one.

- A partial unique index permits exactly one active dataset.
- `migration_runs` records source checksum, schema and fact versions, code revision,
  actor, counts and reconciliation results immutably. It does not perform activation.
- Activation occurs in a single explicit transaction after reconciliation succeeds:
  aggregates are rebuilt for the dataset, the control row is upserted, and the active flag
  is then moved.
- Because previously approved datasets remain addressable, rollback is a status change
  rather than a restore.

Any new dataset-scoped table must include the dataset version in its keys.

## Two-layer organization model

Organizations are represented at two levels of detail.

| | Registry layer | Enriched layer |
|---|---|---|
| Table | `charity_registry_organizations` | `charities` |
| Rows | approximately 397,469 | approximately 373 |
| Key | Textual `cc:{organisation_number}` | Numeric charity ID; negative for Philea |
| Contents | Registration identity and status, income and expenditure, registered office, activity text | Philea metadata, deterministic classifications, observed grants, scores |
| Grants | None | Yes |

`organization_registry_links` joins the two layers. The automated importer creates only
`accepted` `exact_identifier` links, where a Charity Commission number equals an enriched
profile ID. Name-only fuzzy matches are not auto-accepted. An unlinked registry row
therefore indicates that the relationship is unknown, not that no funding exists, and the
interface reports it accordingly.

Philea identifiers are negative deterministic local IDs, which prevents collision with
positive UK charity registration numbers.

## Table groups

| Domain | Tables |
|---|---|
| Organizations | `charities`, `charity_programme_categories`, `charity_geographic_areas` |
| Official registry | `charity_registry_organizations` |
| Grants | `grants`, `grant_beneficiary_countries`, `grant_beneficiary_terms`, `grant_programme_categories` |
| Serving facts | `grant_overview_facts`, `grant_source_funder_facts` |
| Curated links | `organization_registry_links`, `source_funder_link_overrides`, `source_funder_profile_cache` |
| Currency | `exchange_rates` |
| Jobs | `job_runs`, `job_events`, `source_ingestion_runs`, `job_dispatch_outbox`, `worker_heartbeats` |
| Pipeline storage | `source_configurations`, `storage_objects`, `ingestion_run_manifests` |
| Audit and control | `audit_events` (append-only), `idempotency_records` |
| Governance | `retention_policies`, `data_holds`, `restore_verifications`, `deletion_manifests`, `data_subject_requests` |
| Versioned analytics | Nine `analytics_*` aggregate tables |

At head the catalog contains 45 application tables, 55 validated foreign keys and 189
check constraints, all enforced server-side rather than in application code.

## Invariants enforced by the schema

- Monetary values use `NUMERIC(24,4)`, rates use `NUMERIC(24,12)`, and derived minor units
  use `BIGINT`. Floating-point types are not used for these values.
- Timestamps use `TIMESTAMPTZ` and normalized business dates use `DATE`.
  `grants.award_date` preserves the source date or timestamp text exactly, and
  `exchange_rate_date` preserves `YYYY-MM` precision.
- Any value that is queried or filtered is a typed column. JSONB is limited to raw
  payloads, evidence, manifests and audit detail.
- `audit_events` rejects UPDATE and DELETE.
- Every relationship declares explicit `ON UPDATE` and `ON DELETE` behaviour.
- Currency codes, country codes, coordinates, confidence values, classification methods,
  conversion states and job states are check-constrained.

## Registry search

The `pg_trgm` extension is installed by migration. Registry rows carry a stored `tsvector`
over name, normalized name, charity number, postcode and activity text, with a GIN vector
index and trigram indexes on the name columns.

Search combines `websearch_to_tsquery`, `ts_rank_cd` and trigram similarity. The opaque
cursor contains the rank and identifier pair used for ordering, which makes pagination
stable. The implementation is in `src/bff/postgres/registry_repository.py`.

## Adding a migration

```bash
PYTHONPATH=src venv/bin/alembic revision -m "0007_your_change"
PYTHONPATH=src venv/bin/alembic upgrade head
PYTHONPATH=src venv/bin/alembic downgrade -1
PYTHONPATH=src venv/bin/alembic upgrade head
```

A functioning `downgrade()` is required; CI upgrades from an empty database and verifies
the reverse operation. After adding a migration, update `expected_schema_version` in
`config/observability.json`. The readiness probe compares the live Alembic revision
against this value and removes the instance from rotation on mismatch.

## The SQLite file

`src/data/charities.db` is a migration source and shadow fixture. It is an ignored build
artifact rather than an operational datastore, and `sqlite_migration_source` mode is
rejected outside development and test. The runtime container image contains no SQLite
database and no raw data.
