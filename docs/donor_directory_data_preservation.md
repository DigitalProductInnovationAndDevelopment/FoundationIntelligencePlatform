# Donor Directory data-preservation report

Date: 2026-07-25

## Preserved entity layers

The implementation keeps these layers separate:

1. **Observed source funder** — stable source namespace plus source ID, or a namespaced normalized-name fallback.
2. **Enriched organization profile** — the existing `charities` record.
3. **Official registry organization** — the existing Charity Commission registry record.
4. **Link evidence** — explicit source-to-profile grant linkage and accepted profile-to-registry linkage.

No name-only presentation merge was added. A source funder may have zero, one, or multiple supported profile IDs. Multiple IDs remain unresolved and expose no selected profile.

## Before/after source counts

| Population | Before | After |
|---|---:|---:|
| grants | 200,000 | 200,000 |
| enriched profiles | 364 | 364 |
| current registry records | 397,469 | 397,469 |
| source records rewritten | 0 | 0 |
| unresolved grant foreign keys deleted | 0 | 0 |
| new derived facts | 0 | 71,403 |

The derived rows cover 71,286 distinct grants with usable beneficiary-country and funder identity evidence. A multi-country grant has one fact per associated country, which explains the row/grant difference.

## Integrity checks

Before applying the derived schema to the current database:

- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check` rows: 2,294

After the derived schema/index existed:

- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check` rows: 2,294

The 2,294 foreign-key findings pre-date this redesign and are unresolved stored grant profile IDs. The redesign neither deletes nor silently repairs them. A fact row stores `linked_profile_id` only when that ID exists in `charities`; the original unresolved value remains untouched in `grants`.

## Derived-table provenance

`grant_source_funder_facts` is generated exclusively from existing grant columns and normalized derived indexes. It stores no copied raw grant JSON. Every row includes `data_revision`, source namespace, source identity evidence, source record ID, and publisher record URL.

The current derived coverage is:

- 71,403 grant-country facts;
- 71,286 distinct grants;
- 133 source-funder identities;
- 113 beneficiary countries.

Facts with full multi-country awards remain activity evidence but their full amount is excluded from country-attributable funding totals.

## Evidence inventory

The current audit found:

- all 200,000 grants retain a source URL and source record ID;
- 183,947 raw grants contain `recipients[0].self`;
- all 200,000 raw grants contain a funder `self` reference;
- 72,906 raw grants contain a recipient-organization website value;
- 3,594 raw grants contain a funder-organization website value;
- 361 of 364 enriched profiles contain a source URL.

The new BFF exposes these stored values as typed safe links. It does not call the linked 360Giving organization JSON or any website. That preserves source provenance without turning read-time presentation into an external crawler.

## Loader and invalidation safety

- Schema creation is idempotent.
- `insert_grants`, JSONL load, and ECB backfill paths remove `grant_overview_index_revision` and cached Overview payloads.
- Atomic staging loads cannot preserve a stale revision after replacing grants.
- Rebuilds delete and regenerate only derived beneficiary, programme, source-funder, and cache rows.
- No generated database or cache artifact is added to Git.

## Backup and regeneration

For this local implementation audit, a full database copy was made at `/private/tmp/fip-donor-redesign-baseline.db` before testing the migration. `/private/tmp` is not a deployment backup and may be removed by the OS.

The durable recovery mechanism is the existing atomic database loader plus stored preprocessed/source inputs. A production rollout should take its own managed backup before first migration.

To regenerate the facts after a load, start the application or call `rebuild_grant_overview_indexes` through the existing repository/loader workflow. To roll back, drop only the derived fact table and metadata keys; source grants, profiles, registry rows, and links remain sufficient to regenerate prior indexes.

## Preservation gate

- Observed source funders remain searchable: passed.
- Unlinked enriched profiles remain available in Organization Research: passed.
- Complete registry remains available in Advanced Charity Commission Search: passed.
- Multiple links are not auto-selected: passed by automated test.
- Existing source counts preserved: passed.
- No new foreign-key violations: passed by before/after count.
- Legacy donor UI removed: **no**; retained until browser parity is validated.
