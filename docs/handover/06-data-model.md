# Data model

PostgreSQL is the only authoritative operational datastore. The schema is managed
exclusively by Alembic — never edit `alembic_version` or apply DDL by hand.

The full schema contract, including every constraint class and the search index strategy,
is `docs/remediation/aws-postgres-schema.md`. This document covers what you need to work
with it.

## Migrations

| Revision | Adds |
|---|---|
| `0001_postgresql_foundation` | Core organizations, registry, grants, relationships, jobs, audit, idempotency |
| `0002_exchange_rate_period` | Exchange-rate month precision |
| `0003_grant_award_timestamp` | Award-date timestamp preservation |
| `0004_versioned_analytics_materializations` | The nine `analytics_*` aggregate tables and `refresh_analytics_materializations()` |
| `0005_durable_pipeline` | Source configurations, storage objects, ingestion manifests, outbox |
| `0006_governance_retention` | Retention policies, holds, restore verifications, deletion manifests, subject requests |

```bash
PYTHONPATH=src venv/bin/alembic current
PYTHONPATH=src venv/bin/alembic upgrade head
```

Readiness fails unless the live revision equals the expected revision declared in
`config/observability.json`. That is deliberate — a schema mismatch takes the instance out
of rotation rather than serving inconsistent data.

## Dataset versioning — the central idea

Every serving row carries its dataset version in its primary and foreign keys. This makes
a candidate dataset loadable and fully reconcilable **alongside** the approved one.

- `dataset_versions.dataset_version` is the preserved external version key.
- `revision` is an identity-backed monotonic sequence.
- A partial unique index permits exactly **one** active dataset.
- A check constraint requires active status and activation timestamp to agree.
- `migration_runs` records source checksum, schema and fact versions, code revision,
  actor, counts, reconciliation results and errors — immutably. It performs no activation
  by itself.

Activation is one explicit transaction, after reconciliation succeeds:
`refresh_analytics_materializations(dataset_version)` rebuilds that dataset's aggregates,
upserts the `dashboard_analytics` control row, then the active flag moves.

Because prior approved datasets stay addressable, **rollback is a status change, not a
restore**. The target must exist with `approved` or `rolled_back` status and a valid
materialization. See `docs/remediation/rollback-runbook.md`.

## Table groups

| Domain | Tables |
|---|---|
| Organizations | `charities`, `charity_programme_categories`, `charity_geographic_areas` |
| Official registry | `charity_registry_organizations` |
| Grants | `grants` |
| Grant relationships | `grant_beneficiary_countries`, `grant_beneficiary_terms`, `grant_programme_categories` |
| Serving facts | `grant_overview_facts`, `grant_source_funder_facts` |
| Curated links | `organization_registry_links`, `source_funder_link_overrides`, `source_funder_profile_cache` |
| Currency | `exchange_rates` |
| Jobs | `job_runs`, `job_events`, `source_ingestion_runs`, `job_dispatch_outbox`, `worker_heartbeats` |
| Pipeline storage | `source_configurations`, `storage_objects`, `ingestion_run_manifests` |
| Audit and control | `audit_events` (append-only), `idempotency_records` |
| Quality and governance | `data_quality_issues`, `materialization_versions`, `retention_actions`, `export_jobs` |
| Governance evidence | `retention_policies`, `data_holds`, `restore_verifications`, `deletion_manifests`, `data_subject_requests` |
| Versioned analytics | `analytics_scope_totals`, `analytics_country_aggregates`, `analytics_country_connections`, `analytics_period_aggregates`, `analytics_programme_aggregates`, `analytics_entity_rankings`, `analytics_country_funder_rankings`, `analytics_funder_relationships`, `analytics_filter_values` |

At head the catalog holds 45 application tables, 55 validated foreign keys and 189 check
constraints, all enforced server-side. (The "Local gate evidence" section of the schema
contract quotes lower counts — those were measured at revision `0004` and are stale
relative to `0006`.)

## The two-layer organization model

| | Registry layer | Enriched layer |
|---|---|---|
| Table | `charity_registry_organizations` | `charities` |
| Rows | ~397,469 | ~373 |
| Key | Stable textual `cc:{organisation_number}` | Numeric charity ID; **negative** for Philea |
| Contents | Registration identity/status, income/expenditure, registered office, activity text, source date, freshness | Philea metadata, deterministic classifications, observed 360Giving relationships, scores |
| Grants | None | Yes |

`organization_registry_links` joins them. The automated importer creates only `accepted`
`exact_identifier` links where a Charity Commission number equals an enriched profile ID.
Name-only fuzzy matches are deliberately never auto-accepted, so an unlinked registry row
means *unknown*, not *no funding*.

Philea IDs are negative deterministic local IDs, which is what prevents collision with
positive UK charity registration numbers.

## Types and invariants

- Timestamps are `TIMESTAMPTZ`; normalized business dates are `DATE`.
- `grants.award_date` preserves the source's date-or-timestamp text exactly.
  `grants.exchange_rate_date` preserves `YYYY-MM` precision.
- Money is `NUMERIC(24,4)`; rates `NUMERIC(24,12)`; derived minor units `BIGINT`.
- Currency and country codes carry uppercase length and shape checks.
- Coordinates, confidence values, classification methods, conversion states, job states,
  revision values and activation transitions are all check-constrained.
- JSONB is limited to raw payloads, evidence, result manifests and audit detail. Anything
  queried or filtered is a typed column.
- `audit_events` rejects UPDATE and DELETE.
- Link-override updates require the revision to advance by exactly one.
- Every relationship declares explicit update and delete behaviour.

## Registry search

`pg_trgm` is installed by migration. Registry rows carry a stored `tsvector` over name,
normalized name, charity number, postcode and activity text, with a GIN vector index plus
trigram GIN indexes on registered and normalized names.

`RegistryRepository` combines `websearch_to_tsquery`, `ts_rank_cd` and trigram similarity.
Rank is rounded to eight decimal places and ordered descending; `registry_id` ascending is
the deterministic tie-break. The opaque cursor contains exactly that rank/ID pair, which is
what makes pagination stable. Limits are capped at 100.

## Analytics materialization

The active local snapshot contains 204,220 aggregate rows: 10 scope totals, 241 countries,
176 country connections, 1,191 monthly/yearly periods, 68 programmes, 187,995 ranked
entities, 1,155 country funders, 13,245 bounded funder-recipient relationships and 139
filter values.

Original-currency scopes stay separate from converted EUR. Negative, missing and invalid
monetary facts are counted for disclosure and excluded from additive totals.

## The SQLite file

`src/data/charities.db` is a **migration source and shadow fixture only**. It is an
ignored build artifact, not an operational datastore, and `sqlite_migration_source` mode is
rejected in staging and production. The runtime container image contains no SQLite database
and no raw data.
