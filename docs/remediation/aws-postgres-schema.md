# PostgreSQL Schema Contract

Status: implemented by Alembic revisions `0001_postgresql_foundation` through
`0005_durable_pipeline` on 2026-07-29.

## Version and activation model

`dataset_versions.dataset_version` is the preserved external version key.
`revision` is an identity-backed monotonic sequence. A partial unique index
permits exactly one active dataset, while a check constraint requires active
status and an activation timestamp to agree. Every serving row includes the
dataset version in its primary and foreign keys, so a candidate can be loaded
and reconciled without replacing the approved dataset. Prior versions remain
addressable for rollback.

`migration_runs` records the immutable source checksum/schema/fact versions,
code revision, actor, counts, reconciliation results and errors. It owns no
activation side effect by itself; Phase 4 performs activation in one explicit
transaction after reconciliation.

## Relational ownership

| Domain | Tables and invariant |
|---|---|
| Organisations | `charities`, `charity_programme_categories`, `charity_geographic_areas`; source identity and dataset-scoped charity IDs are preserved. |
| Official registry | `charity_registry_organizations`; `registry_id` remains textual and constituent rows sharing a charity number remain distinct. |
| Grants | `grants`; textual grant/source IDs, original and EUR amounts/currency/conversion facts, negative/zero values, review evidence and dates remain typed fields. |
| Grant relationships | `grant_beneficiary_countries`, `grant_beneficiary_terms`, `grant_programme_categories`; frequently filtered many-to-many facts are relational, not JSON. |
| Serving facts | `grant_overview_facts`, `grant_source_funder_facts`; dataset/revision provenance and minor-unit money facts remain explicit. |
| Curated links | `organization_registry_links`, `source_funder_link_overrides`, `source_funder_profile_cache`; accepted links, overrides and revision sequence are constrained. |
| Currency | `exchange_rates`; ISO-style currency/date primary key, positive high-precision rate and typed retrieval timestamp. |
| Jobs | `job_runs`, `job_events`, `source_ingestion_runs`, `job_dispatch_outbox`, `worker_heartbeats`; bounded statuses, durable idempotency, leases, retry/dead-letter state and event sequence constraints. |
| Pipeline storage/configuration | `source_configurations`, `storage_objects`, `ingestion_run_manifests`; fail-closed schedules, immutable raw object descriptors, version/checksum ownership and append-only run evidence. |
| Audit/control | append-only `audit_events` and durable `idempotency_records`. |
| Quality/governance | `data_quality_issues`, `materialization_versions`, `retention_actions`, `export_jobs`; quarantines retain original JSON values while queryable control fields remain relational. |
| Versioned analytics | `analytics_scope_totals`, `analytics_country_aggregates`, `analytics_country_connections`, `analytics_period_aggregates`, `analytics_programme_aggregates`, `analytics_entity_rankings`, `analytics_country_funder_rankings`, `analytics_funder_relationships`, `analytics_filter_values`; every row is dataset-scoped and deleted by cascade with its dataset. |

Every relationship declares update/delete behavior. The local catalog contains
40 application tables, 49 validated foreign keys and 161 check constraints. PostgreSQL enforces these
server-side for every connection; there is no connection-local equivalent to a
SQLite FK pragma.

## Types and constraints

- Timestamps use `TIMESTAMPTZ`; normalized source/business dates use `DATE`.
  `grants.award_date` preserves the source's date-or-full-timestamp text exactly,
  while `grants.exchange_rate_date` preserves its monthly `YYYY-MM` precision.
  Dataset-versioned fact tables retain normalized `DATE` columns for analysis.
- Grant and financial amounts use `NUMERIC(24,4)`; rates use
  `NUMERIC(24,12)`; derived minor-unit facts use `BIGINT`.
- Currency and country codes have uppercase length/shape checks.
- Coordinates, confidence values, classification methods, conversion states,
  job states, revision values and activation transitions are checked.
- JSONB is limited to raw payload/evidence, result manifests and flexible audit
  details; query relationships and control/status fields remain typed columns.
- Audit events reject update and delete. Link-override updates require the
  revision to advance by exactly one.

## Search and cursor contract

PostgreSQL `pg_trgm` is installed by the migration. Registry rows have a stored
`tsvector` over name, normalized name, charity number, postcode and activity.
The schema supplies a GIN vector index plus trigram GIN indexes for registered
and normalized names.

`RegistrySearchRepository` combines `websearch_to_tsquery`, `ts_rank_cd` and
trigram similarity. Rank is rounded to eight decimal places and ordered
descending; `registry_id` is the deterministic ascending tie-break. The opaque
cursor contains exactly that rank/ID pair. Limits are bounded to 100 and all
queries use SQLAlchemy async sessions over asyncpg.

## Runtime boundary

Staging and production select the PostgreSQL-only router at module import and
never import the legacy SQLite repository. A subprocess architectural test
blocks `sqlite3` and imports the production application to prove this boundary.
Development/test may still load the legacy implementation while fixtures and
journeys are ported. Unported staging/production journeys are intentionally
absent rather than receiving a hidden SQLite fallback; Phase 5 must complete
all route/domain implementations before production becomes a GO.

## Serving materialization contract

Revision `0004_versioned_analytics` creates `refresh_analytics_materializations(dataset_version)`. It deletes and deterministically rebuilds only the named dataset's aggregates, then upserts the `dashboard_analytics` materialization control row. The migration activation transaction calls the function before changing the single active dataset. Rollback verifies the approved target and builds its aggregate set if absent before reactivation.

The active local snapshot contains 204,220 aggregate rows: 10 scope totals, 241 countries, 176 country connections, 1,191 monthly/yearly periods, 68 programmes, 187,995 ranked entities, 1,155 country funders, 13,245 bounded funder-recipient relationships and 139 filter values. Original-currency scopes remain separate from converted EUR. Negative, missing and invalid monetary facts are counted for disclosure and excluded from additive totals.

Default map, trend, theme, summary and funder queries use these tables. Arbitrarily filtered requests retain fact-table execution. Heavy country relationships are exposed through a separate endpoint capped at 250; funder-recipient materialization is capped at 50 per funder. Exact current-registry lookup adds a dataset/name/ID B-tree index, while text search retains the stored-vector/trigram GIN strategy.

## Local gate evidence

- Host-venv `alembic upgrade head` succeeded.
- `alembic downgrade base` left only `alembic_version`, then zero-to-head
  upgrade succeeded again.
- The non-root, read-only Compose migration service independently upgraded from
  base to head.
- The catalog reports revision `0004_versioned_analytics`, 34 application
  tables, 39 validated FKs, zero unvalidated FKs, 136 checks, `pg_trgm`, the
  three original search indexes and the exact-current-name B-tree index.
- A real asyncpg integration test demonstrates FK rejection, full-text search,
  deterministic tie-breaking and cursor continuation.
