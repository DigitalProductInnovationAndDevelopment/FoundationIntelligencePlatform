# Database Integrity Report

Audit date: 2026-07-28  
Source database: `src/data/charities.db` (read-only during audit)  
Staging copy: `/private/tmp/fip-audit-staging-20260728.db`  
Restore copy: `/private/tmp/fip-audit-restore-20260728.db`

## Result

The active SQLite database is structurally intact and internally consistent: `quick_check` and `integrity_check` returned `ok`, and `PRAGMA foreign_key_check` returned zero rows. The database is not ready for direct PostgreSQL cutover. The repository relies heavily on SQLite-specific SQL, a 2.10 GB single-file topology, JSON-in-TEXT fields, FTS5, filesystem publication and connection-local foreign-key enforcement.

No active table was changed, no domain record was deleted and no destructive repair was attempted. A coherent backup was created with SQLite's `.backup`, then migrations and restore checks were performed only against temporary copies.

## Copy and restore evidence

| Artifact | Size | SHA-256 | Verification |
|---|---:|---|---|
| Staging immediately after backup | 2,100,543,488 B | `609208373d9a832c6d54e5d0a6679bed801bc35c59dc11847011d3c98b4f895d` | `quick_check=ok`, `integrity_check=ok`, FK violations 0 |
| Restore copy after staging migration exercise | 2,100,543,488 B | `6f665f81e900123b995f3dd92a0c47bcf379fe5241fa53a0d47bbfd94c360755` | `quick_check=ok`, row counts equal, FK violations 0 |

The byte hashes differ because schema initialization/migration calls can change SQLite page layout. Logical checks and core row counts were equal. `create_tables(reset=False)` was executed twice on staging in 8.1 ms and 2.2 ms; both calls were idempotent and `validate_database(require_foreign_keys=True)` returned `valid`.

## Engine settings and versioning

| Setting | Observed value | Assessment |
|---|---:|---|
| File size | 2,100,543,488 B | Too large to package with application code; needs managed persistence/object storage separation. |
| Page size / count | 4,096 / 512,828 | Consistent with file size. |
| Freelist | 35 pages | Little free-page fragmentation. |
| Journal mode | `delete` | Single-host design; not a cloud HA strategy. |
| Synchronous | `2` (`FULL`) | Durable for local SQLite. |
| Auto-vacuum | `0` | No automatic reclamation. |
| `PRAGMA user_version` | `0` | Engine schema version is not used. |
| `metadata.schema_version` | `7` | Application migration version. |
| Registry schema version | `1` | Separate version domain. |
| Overview fact version | `2026-07-overview-facts-v5` | Separate materialization version. |
| Foreign keys on a fresh connection | `0` | Constraints exist but enforcement depends on each application connection enabling it. |

The multiple version domains are understandable but need one migration ledger and explicit compatibility contract for PostgreSQL.

## Tables and row counts

| Table | Rows | Purpose / relationship |
|---|---:|---|
| `charities` | 373 | Curated organization profiles and grant-linked entities. |
| `charity_registry_organizations` | 397,469 | Charity Commission directory records. |
| `charity_registry_organizations_fts` | 397,469 | FTS5 search projection plus FTS shadow tables/triggers. |
| `exchange_rates` | 18,964 | Currency conversion observations. |
| `grants` | 302,546 | Core grant awards. |
| `grant_beneficiary_countries` | 104,309 | Many-to-many grant/country associations. |
| `grant_beneficiary_terms` | 556,719 | Geography evidence/terms. |
| `grant_programme_categories` | 358,883 | Grant/programme category associations. |
| `grant_overview_facts` | 302,546 | Precomputed overview facts. |
| `grant_source_funder_facts` | 104,309 | Funder/geography aggregates. |
| `grant_overview_cache` | 5 | Cached overview payloads. |
| `organization_registry_links` | 345 | Curated-to-registry organization links. |
| `source_funder_link_overrides` | 1 | Operator override/revision record. |
| `source_funder_profile_cache` | 1 | Cached/enriched funder profile. |
| `metadata` | small | Schema and materialization versions. |

There are no application views. FTS5 creates internal tables and synchronization triggers.

## Storage concentration

`dbstat` shows the dominant objects:

| Object | Approximate bytes |
|---|---:|
| `grants` table | 1,244,413,952 |
| `charity_registry_organizations` | 169,472,000 |
| `grant_overview_facts` | 106,946,560 |
| `grant_source_funder_facts` | 54,087,680 |
| FTS content/data | about 33 MB each |
| Large secondary indexes | several at 20–30 MB each |

The database is primarily grant payload and duplicated/materialized lookup data. This is a candidate for normalized PostgreSQL operational tables plus S3/Parquet historical/raw storage, not an image layer.

## Foreign keys and relationships

- `grant_beneficiary_countries`, `grant_beneficiary_terms`, `grant_programme_categories`, `grant_overview_facts` and `grant_source_funder_facts` cascade to `grants`.
- Optional grant funder/recipient references point to `charities` with `NO ACTION` semantics.
- Registry links point to both registry records and curated charities.
- `PRAGMA foreign_key_check` returned **0 violations** on staging and restore copies.
- Orphan checks for the optional grant organization links returned **0**.
- Risk: `foreign_keys=OFF` is the SQLite default on a fresh connection. PostgreSQL will enforce constraints globally, so load order and deferred validation must be designed explicitly.

## Keys, duplicates and identity

| Check | Result | Interpretation / action |
|---|---:|---|
| Distinct grant IDs | 302,546 / 302,546 | Primary identity is unique. |
| Duplicate `(source, source_record_id)` | 0 | Source identity constraint is effective. |
| Exact business-key duplicate groups | 4,271 groups / 14,529 rows beyond one per group | Do not delete automatically. These can be distinct tranches/awards with similar business attributes. Review against source award IDs. |
| Registry IDs | 397,469 distinct | Unique registry record identity. |
| Registry duplicate charity numbers | 9,073 groups / 40,226 rows beyond one per charity number | Mostly constituent funds/linked registrations; examples show many legitimate records under umbrella numbers. Keep. |
| Organization registry links | 345 | All are accepted exact-identifier links with confidence 1.0. |

## Grant completeness and numeric quality

| Check | Result | Assessment |
|---|---:|---|
| Missing grant ID, recipient, amount, award date or currency | 0 | Pass for required core fields. |
| Invalid award-date shape | 0 | Pass for stored format. |
| Zero award amount | 2,101 | Retain; may represent corrections or source semantics. Review analytically. |
| Negative award amount | 2, min −10,000 | Likely corrections/reversals. Excluded from dashboard awarded-total logic; keep with explicit classification. |
| Maximum award | 465,755,576.5 | High-value outlier; source review recommended. |
| Date range | 1991-04-18 to 2026-10-30 | Includes one future-dated award. |
| Future-dated grant | `360G-TheSeafarersCharity-1756`, GBP 50,000 | Quarantine/review candidate, not deletion candidate. |

## Currency conversion reconciliation

| Currency/status | Grants |
|---|---:|
| GBP / `ecb_monthly_average` | 300,388 |
| GBP / `unavailable_missing_rate` | 419 |
| USD / `ecb_monthly_average` | 1,615 |
| USD / `unavailable_missing_rate` | 2 |
| EUR / native | 65 |
| ILS / monthly | 23 |
| CHF / monthly | 10 |
| CAD / monthly | 7 |
| ZAR / monthly | 6 |
| KES / missing rate | 10 |
| TZS / missing rate | 1 |

- 302,114 grants have an EUR amount.
- 302,112 have an EUR amount and a non-negative source amount.
- Sum of every stored EUR amount: EUR 22,435,967,498.63.
- Sum used by the overview after excluding two negative corrections: EUR 22,435,986,707.70.
- API overview returned EUR 22,435,986,707.70 and 302,546 grants, exactly matching the implemented SQL semantics.
- 432 grants lack a usable conversion.
- The README describes daily award-date/previous-business-day ECB behavior, while stored statuses and tests implement monthly averages. Documentation and actual semantics have drifted.

## Geography quality

| Metric | Result |
|---|---:|
| Grants with no mapped beneficiary country | 198,355 |
| Grants with exactly one country | 104,128 |
| Multi-country grants | 63 |
| Maximum countries on one grant | 7 |
| Country associations | 104,309 |
| Distinct mapped grants | 104,191 |
| Overview country coverage | 34.44% |

Methods: `unavailable` 180,682; source normalization 103,260 (135 reviews); deterministic regex 18,604 (236 reviews). The map discloses that multi-country full amounts are excluded from country funding totals. Preserve that rule and its evidence during migration.

## Programme quality

| Provenance/method | Result |
|---|---:|
| Unclassified provenance | 167,843 |
| Inferred provenance | 120,792 |
| Source provenance | 13,911 |
| Low-confidence records | 72 |
| Invalid source-label flag | 234,774 |
| Qualifying/classified grants in overview | 134,554 |
| Programme coverage | 44.54% |

The category association table includes an `Unclassified` row, so its row count exceeds the number of classified awards. Rule versions, provenance and invalid-label flags must be migrated with the facts, not recomputed silently.

## Organization and registry anomalies

- Curated organizations: 299 Philea, 66 Charity Commission, and 8 records with blank/unknown source provenance. The blank-source records need provenance repair, not deletion.
- Philea's 299 organizations have no attached grants; that is consistent with organization-level source scope and must not be interpreted as failed linking.
- Registry status: 185,377 `Registered`, 212,092 `Removed`.
- Two registry records contain negative income. Retain as source evidence and place on a data-quality review queue.
- Registry name search uses FTS5. Benchmark on staging: `foundation` returned 50 rows in 651.171 ms; exact charity number search returned 3 rows in 1.997 ms; registered/income query returned 50 in 8.89 ms.
- Query plans used `idx_registry_charity_number` and covering `idx_registry_status_income` as intended.

## PostgreSQL portability

Static inventory found widespread SQLite coupling: approximately 34 `PRAGMA` references, 9 `sqlite_master`, 16 `INSERT OR REPLACE`, 6 `ON CONFLICT`, 9 `strftime`, 8 `rowid`, 9 `json_extract`, 16 `json_each`, 4 FTS5 and 15 `COLLATE NOCASE` references, plus 111 direct `sqlite3` references. Counts identify migration surface, not unique statements.

Required rewrites include:

- `?` placeholders and direct connection/row handling to a PostgreSQL driver/data-access layer;
- FTS5 virtual table and triggers to `tsvector`/GIN or a dedicated search service;
- JSON-in-TEXT to validated `jsonb` or normalized tables;
- `INSERT OR REPLACE` semantics to explicit `INSERT ... ON CONFLICT ... DO UPDATE` without accidental delete/reinsert behavior;
- `strftime`, `rowid`, case collation and SQLite-specific pragmas;
- file-copy atomic publish to transactionally versioned staging tables and view/schema switching;
- load ordering, sequences, numeric precision, timestamps/time zones and constraint validation;
- synchronous SQLite calls inside async endpoints.

## Migration readiness gates

1. Freeze and document schema v7 plus fact-version semantics.
2. Create PostgreSQL DDL with explicit keys, checks, numeric precision, time zones and JSON types.
3. Build a deterministic bulk loader from the coherent SQLite backup/raw artifacts.
4. Reconcile every table count, distinct key, FK/orphan count and the dashboard control totals above.
5. Run duplicate/anomaly policy checks without deleting records.
6. Run API golden responses and query-plan/performance tests against PostgreSQL.
7. Dual-write is optional; dual-read/shadow comparison is required before cutover.
8. Take a final source snapshot, stop writers, load delta, switch read traffic behind a flag.
9. Preserve the SQLite snapshot and application rollback path until production acceptance.

## Retention, archive and quarantine candidates

No listed item was deleted.

- Preserve: active DB, raw source artifacts, source IDs, enrichment evidence, rule versions, negative/zero awards and registry constituent records until policy approval.
- Archive to S3 with lifecycle: versioned raw downloads, JSONL/preprocessed batches, superseded SQLite snapshots and generated audit exports.
- Quarantine for review: the one future-dated grant, two negative grants, two negative-income registry rows, 432 missing conversions and eight blank-provenance organizations.
- Potential technical deletion after explicit approval: temporary staging/restore DB copies, incomplete temporary virtualenv, browser profiles, test caches/build outputs and the 9.37 GB local Docker image.

See `data-retention-and-deletion-candidates.md` for approval and risk details.
