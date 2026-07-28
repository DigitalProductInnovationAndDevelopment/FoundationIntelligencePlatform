# Migration report: sqlite-v7-8fc0cce61c81-r2

- Run: `60af368e-c440-5521-9648-5ab272f9ddb6`
- Source checksum: `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`
- Target schema: `0003_grant_award_timestamp`
- Activation: `active`
- Rollback dataset: `sqlite-v7-8fc0cce61c81`
- Reconciliation failures: `0`

## Counts

| Table | Source | Target |
|---|---:|---:|
| `charities` | 373 | 373 |
| `charity_registry_organizations` | 397469 | 397469 |
| `grants` | 302546 | 302546 |
| `grant_beneficiary_countries` | 104309 | 104309 |
| `grant_beneficiary_terms` | 556719 | 556719 |
| `grant_programme_categories` | 358883 | 358883 |
| `grant_overview_facts` | 302546 | 302546 |
| `grant_source_funder_facts` | 104309 | 104309 |
| `organization_registry_links` | 345 | 345 |
| `source_funder_link_overrides` | 1 | 1 |
| `source_funder_profile_cache` | 1 | 1 |
| `exchange_rates` | 18964 | 18964 |

## Controls

| Control | Value |
|---|---:|
| `business_key_duplicate_groups` | 4271 |
| `classified_grants` | 134554 |
| `distinct_mapped_grants` | 104191 |
| `duplicate_charity_number_groups` | 9073 |
| `duplicate_source_identity_groups` | 0 |
| `foreign_key_violations` | 0 |
| `future_dated_grants` | 1 |
| `missing_eur_conversions` | 432 |
| `negative_grants` | 2 |
| `overview_total_eur` | 22435986707.70 |
| `zero_grants` | 2101 |
