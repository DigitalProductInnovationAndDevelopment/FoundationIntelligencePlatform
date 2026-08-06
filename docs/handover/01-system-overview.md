# System overview

## Purpose

The platform helps a user explore philanthropic foundations, charities and the grant
relationships observed between them. It answers questions like "which organizations fund
work in this country?", "what has this funder actually given, to whom, and when?" and
"how does this organization compare against a target profile I define?"

It is explicitly **not** a prediction system. It does not estimate whether an organization
will donate, and it does not infer activity that is absent from source data.

## Design principle: four kinds of truth, kept apart

This is the most important idea in the codebase, and it shapes the schema, the API and
the UI. Four categories of value are stored and displayed separately, never merged:

| Category | Example field | Meaning |
|---|---|---|
| Source fact | `programme_areas_source` | A value present in the source record, normalized but not invented |
| Deterministic inference | `programme_areas_inferred` | Derived by versioned regex/taxonomy rules, carrying evidence and confidence |
| Platform-derived | relevance score, analytics aggregates | Computed by the platform from the above, versioned and explainable |
| Absent | `transaction_data_unavailable` | Explicitly unknown — never rendered as zero |

The same discipline applies to geography. `headquarters_country` (where an organization
is based) is never substituted for `beneficiary_geography_normalized` (where grant money
went). The world map is built solely from the latter, and reports its own coverage gap.

## Data sources

| Source | Coverage in the shipped cache | Nature |
|---|---|---|
| Charity Commission for England and Wales | 62 raw records → 65 normalized UK-side organizations; 397,469 registry rows | Official UK register |
| 360Giving | 302,546 observed grant transactions | Published UK grant data, sampled — not complete coverage |
| Philea | 299 member organizations | European membership directory; organization metadata only, no grants |

The reconciled active dataset holds 373 organizations, 302,546 grants across GBP, USD,
EUR, ILS and CHF, and 345 accepted registry-to-profile links.

Every raw record retains its source name, source record ID, source URL where supplied,
ingestion timestamp and raw payload, so any displayed value can be traced back.

## Currency handling

Original amount and currency are immutable source facts. EUR display values are derived
using the official ECB daily reference rate for the grant's award date, with the previous
published business-day rate used for weekends and ECB holidays. Each converted grant
stores its conversion status, rate, rate date and source. 34 pre-1999 GBP grants remain
explicitly unconverted because no ECB EUR rate exists for those dates — they are reported
as gaps, not converted approximately.

Multi-currency totals are never summed across currencies. Amounts spanning multiple
beneficiary countries are neither divided nor duplicated; they are excluded and counted.

## The two-layer organization model

Organizations exist at two levels of depth, deliberately:

- **Registry layer** (`charity_registry_organizations`) — every available Charity
  Commission registration record. Lightweight: identity, status, income/expenditure,
  registered office, activity text. ~397,000 rows. Paginated, cursor-based, capped at 100
  rows per request.
- **Enriched layer** (`charities`) — the smaller set of profiles carrying Philea metadata,
  deterministic classifications, observed 360Giving relationships and scores.

`organization_registry_links` joins them. The automated importer creates only `accepted`
`exact_identifier` links where a Charity Commission number equals an enriched profile ID;
name-only fuzzy matches are deliberately never auto-accepted. Registry rows without a link
display "No observed grant data" rather than "No funding" — an unlinked row means unknown,
not zero.

## Capability register

| Capability | Status | Current limit |
|---|---|---|
| Cached UK organization ingestion | Complete | 65 normalized UK-side organizations |
| Cached 360Giving grant ingestion | Sampled | 302,546 transactions; not complete 360Giving coverage |
| Cached Philea organization ingestion | Complete | Organization-level only; no grants attached |
| DACH foundation intelligence | Partial | Organization-level discoverability only; not a DACH registry or grant dataset |
| Organization directory and detail | Complete | PostgreSQL-backed |
| PostgreSQL migration and cutover | Complete **locally** | Exact reconciliation, 18 zero-difference shadow projections, restore and rollback pass. AWS/production cutover unexecuted |
| Programme-area enrichment | Complete | Versioned deterministic rules; accuracy not externally validated |
| Geographic-focus enrichment | Complete | Distinct from headquarters and beneficiary geography |
| Grant list, network summary, Sankey | Complete | Observed 360Giving transactions only |
| Beneficiary map | Complete | Shown only above a coverage threshold; 62.86% known-country disclosure, 39 multi-country exclusions |
| Relevance score | **Experimental** | Example configuration; not client-approved, not a prediction |
| News summary | Partial | Requires credentials and network access; approval-gated |
| Offline dashboard fallback | Mocked | Clearly labelled prototype values. Grant, map and score data are never fabricated offline |
| Monthly awards and programme allocation | Complete | Auto mode uses historical ECB-converted EUR; explicit currency selection stays source-currency-only |
| Complete DACH grant transactions | **Missing** | No source currently supplies this |
| Enrichment predictive accuracy | **Not verified** | Coverage is measured; labelled ground truth does not exist |
| Client-approved score definition | **Blocked** | No approved target, weights or decision policy exists in the repository |

## Known limitations

- The dataset is a bounded proof-of-concept snapshot, not a comprehensive UK, DACH or
  European foundation database.
- Philea contributes organization metadata only; no activity is inferred from membership.
- Enrichment coverage is measured but accuracy is not validated against labelled data.
  Evidence and review flags must stay visible in any UI built on this.
- The relevance score is an illustrative example. Its weights carry no client approval.

For the formal delivered/not-delivered position, see
[12-acceptance-register.md](12-acceptance-register.md).
