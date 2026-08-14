# 4. Domain rules

This page documents the rules governing how data is classified, aggregated and displayed.
These are deliberate decisions rather than incidental implementation details, and changing
one alters what the platform asserts about the underlying data.

## Geographic concepts

Three geographic concepts are represented separately and are not interchangeable.

| Concept | Field | Meaning |
|---|---|---|
| Headquarters | `headquarters_country` | Where the organization is registered or based |
| Geographic focus | `geographic_focus_source` / `_inferred` | Where the organization states that it works or funds |
| Beneficiary geography | `beneficiary_geography_normalized` | Where a specific transaction's funding was directed |

The world map is derived solely from beneficiary geography. Headquarters location is not
substituted for it and does not establish where a payment originated.

## Beneficiary map

Resolution uses `beneficiary_geography_normalized` first, falling back only to explicit
ISO country codes or explicit country names in the original source field. It does not
consult funder headquarters, recipient registered offices, or inferred operating regions.

In the reconciled snapshot this resolves 104,191 of 302,546 grants (34.44%). The remaining
198,355 are reported as unmapped rather than assigned a country. Each map response carries
its own coverage and exclusion metadata, which should be read at runtime rather than
reproduced as a fixed figure.

- England, Scotland, Wales and Northern Ireland roll up to the United Kingdom shape, and
  their original labels are retained in the country detail view.
- In grant-count mode a grant is counted once in each explicitly associated country. The
  interface labels the metric "grant-country associations" when multi-country records are
  present.
- In funding mode only non-negative, single-country amounts in one selected currency are
  included. Multi-country amounts are excluded and counted rather than divided or
  duplicated.
- The connection overlay draws associations between registered funder location and
  beneficiary country. These are labelled as illustrative associations rather than
  verified financial routes, in both the settings and the map.

## Currency

The original amount and currency are immutable source facts. EUR display values are
derived using the official ECB daily reference rate for the grant's award date, with the
previous published business-day rate applied for weekends and ECB holidays. Each converted
grant stores its conversion status, rate, rate date and source.

- Totals are not summed across currencies.
- 34 pre-1999 GBP grants remain unconverted because no ECB EUR rate exists for those
  dates. They are reported as gaps rather than converted approximately.
- The `Auto · EUR converted` mode includes every eligible source currency using stored
  historical rates. Selecting a specific currency restricts the result to grants
  originally recorded in that currency; it is a filter rather than a conversion.

The implementation is in `src/pipelines/backfill_ecb_exchange_rates.py`.

## Deterministic enrichment

The active rule version is `deterministic-enrichment-v2`, defined as `RULE_VERSION` in
`src/preprocessing/enrichment.py`. This module is the single active source of programme
and geography taxonomy; parallel taxonomies should not be introduced.

Rules compile and validate at import time, match on token boundaries, retain the matched
excerpt and rule identifier as evidence, and inspect a 48-character context window for
negation terms such as `not`, `without` and `exclude`. Ambiguous country names such as
Jordan and Georgia receive lower confidence and a review flag.

The programme taxonomy contains 15 categories, defined in `PROGRAMME_TAXONOMY`. The tuple
in the module is the authoritative list.

When modifying rules: write to `*_inferred` fields only and not to `*_source`; emit
evidence and a confidence value with each classification; version the rule set; and re-run
`src/pipelines/reclassify_grant_enrichment.py` to reclassify stored data atomically.

Coverage is measured and reported. Coverage figures are not precision or recall measures.
No labelled ground-truth set exists, so these figures do not demonstrate correctness.

## Grant allocation by programme area

1. `programme_area_source` is normalized. A valid taxonomy match takes precedence.
2. Otherwise `programme_area_inferred` categories are accepted where the stored score
   meets the 0.55 review threshold.
3. All remaining records are shown as `Unclassified` rather than hidden or redistributed.

A grant spanning multiple categories is split in minor currency units with deterministic
remainder assignment, so that allocated amounts reconcile exactly to qualifying source
amounts.

Negative source values are treated as possible corrections or reversals. They are excluded
from presentation sums and reported in exclusion metadata. Zero values remain included.

## Grant awards over time

Values are grouped by the calendar month or year of the award date. Auto mode uses monthly
aggregation for a selected period of up to 24 months and yearly aggregation beyond that.

Empty periods are returned as unknown coverage with null values rather than as confirmed
zero activity. Date presets are computed from the actual cached-source range, so the chart
does not extend into future months.

## Observed donor directory

The donor directory is a derived, paginated aggregation over the stored grant population.
It creates no new organization profile and uses no external data.

The deterministic key for a source funder is its source namespace combined with
`funding_org_source_id`, falling back to the normalized funding name only where that
identifier is unavailable. The identity does not change when an enriched-profile link is
added subsequently.

Amount rules for a selected country:

- Multi-country grants are counted once for activity and recency, but their full amount is
  excluded from the country-attributable funding total.
- In `currency=auto`, only stored EUR values with a recognized ECB conversion status are
  eligible for monetary aggregation.
- In an explicit currency mode, only grants originally recorded in that currency are
  considered.

A verified profile is linked only where one already exists. Source-only funders remain
source-only and are not converted into generated profiles.

Selecting a country on the map opens the donor directory rather than applying the country
as an organization-location filter. It uses the map's canonical beneficiary-country
association, so that a country with observed funding leads to the underlying evidence
rather than to a registered-address lookup.

## Partial profiles

Directory profiles without a cached raw Charity Commission detail object expose a
schema-valid partial detail view assembled from normalized fields. Registration status is
reported as `UNKNOWN`, unavailable sections remain empty, and the API neither substitutes
missing values nor fails the entire request.

## Relevance score

No client-approved score definition, target variable or set of weights exists. The included
`example-relevance-v2` configuration is marked `experimental` and measures relevance to a
selected target profile only. It is not a probability, recommendation, forecast, or
prediction of donation behaviour.

The example weights in `config/scoring.example.json` are thematic fit 0.35, geographic fit
0.25, funding-capacity fit 0.15, historical grant-size fit 0.15 and organization-type fit
0.10. Weights must sum to 1.0 or the configuration is rejected at load time.

Missing components contribute zero to the weighted total, and the score is not
renormalized. The only supported value for `missing_data_behavior` is
`zero_for_missing_components`. As the engine notes, a complete match on a single criterion
constitutes a partial fit rather than a full profile match. `confidence` and
`data_completeness` are returned separately from the score, so that a score based on
limited evidence is identifiable as such. Each component exposes its inputs, method,
confidence and missing reason. Financial and grant comparisons are performed only when
currencies match.

The implementation is in `src/scoring/engine.py`.

## Invariants

1. Source facts, inferred values and platform-derived values are held in separate fields.
2. Absent data is represented explicitly, for example `transaction_data_unavailable` or
   `organization_level_only`, and not as zero.
3. Amounts are not summed across currencies. Multi-country amounts are excluded and
   counted rather than split or duplicated.
4. Exactly one dataset is active, and activation occurs in a single transaction after
   reconciliation.
5. `audit_events` is append-only.
6. Every mutation requires an `Idempotency-Key`.
7. There is no anonymous access to `/api/*`, and no `AUTH_MODE` other than `oidc` is
   permitted in staging or production.
8. Configuration errors fail at import time rather than at first request.
9. The runtime image contains no data.
