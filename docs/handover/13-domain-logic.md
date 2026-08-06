# Domain logic and business rules

The rules that govern how data is classified, aggregated and displayed. These are
deliberate decisions, not implementation accidents — changing one changes what the
platform asserts about the world.

## Deterministic enrichment

Active rule version: `deterministic-enrichment-v1`. Implemented in
`src/preprocessing/enrichment.py`.

Rules compile and validate at import time, match on token boundaries, retain the matched
excerpt and rule ID as evidence, and inspect a 48-character context window for negation
(`not`, `without`, `exclude`). Ambiguous country names such as Jordan and Georgia receive
lower confidence and a review flag.

**Programme taxonomy — 15 categories:** Arts & Culture; Citizenship, Social Justice &
Public Affairs; Civil Society, Voluntarism & Non-Profit Sector; Diversity & Inclusion;
Education; Environment/Climate; Food, Agriculture & Nutrition; Health; Human/Civil Rights;
Humanitarian & Disaster Relief; Peace & Conflict Resolution; Sciences & Research;
Socio-economic Development, Poverty; Youth/Children Development; tech-enablement.

**Latest coverage report** — 2,958 organization-plus-grant records processed:

| Measure | Count |
|---|---:|
| With source programme areas | 2,035 |
| With inferred programme areas | 1,907 |
| Without any programme area | 300 |
| With source geography | 2,145 |
| With inferred geography | 1,035 |
| Without geography | 636 |
| Requiring review | 213 |
| Detected classification conflicts | 0 |
| Insufficient source text | 77 |

These are **coverage counts, not precision or recall**. They do not demonstrate
correctness.

## Three distinct geographic concepts

Conflating these is the single most consequential error possible in this codebase.

| Concept | Field | Meaning | Used by |
|---|---|---|---|
| Headquarters | `headquarters_country`, `headquarters_region` | Where the organization is registered or based | Foundation-region filters |
| Geographic focus | `geographic_focus_source` / `_inferred` | Where the organization says it works or funds | Focus filters |
| Beneficiary/project geography | `beneficiary_geography_normalized` | A transaction's destination | Recipient-region filters, the map |

## The Global Grant Distribution map

Resolves beneficiary-country geography for **104,191 of 302,546** ingested grants
(34.44%), producing 104,309 country associations because explicitly multi-country grants
retain every supported association.

Resolution order: `beneficiary_geography_normalized` first, falling back **only** to
explicit ISO country codes or explicit country names in the original
`beneficiary_geography` source field. It never consults funder headquarters, recipient
registered offices, or inferred operating regions.

England, Scotland, Wales and Northern Ireland roll up to the United Kingdom shape while
their original labels are retained in country detail. The remaining 198,355 records are
reported as **unmapped** rather than assigned a fabricated country.

**Grant-count mode** counts a grant once in each explicitly associated country — the UI
therefore labels the metric "grant-country associations" when multi-country records are
present. The current cache contains 58 such grants.

**Funding mode** includes only non-negative, single-country amounts in one selected
currency. It never repeats or invents allocations for multi-country awards. Country totals
must not be added to excluded multi-country amounts, nor interpreted as complete 360Giving
or global-market coverage.

**Connection layer** — an optional overlay drawing up to the 36 strongest
registered-funder-location-to-beneficiary-country associations. Origin uses an explicit
360Giving funding-organization address country where present, otherwise the matched
directory's registered headquarters country. These are labelled in both settings and the
map as **illustrative associations, not verified financial routes**. Headquarters never
substitutes for beneficiary geography or proves where a payment originated.

## Grant filter scope

The Overview `Filters` drawer applies one grant scope across KPIs, map, trends and
programme allocation. Accepted fields: award date range, `currency`, beneficiary
geography, programme area, donor, recipient, and time aggregation.

`Auto · EUR converted` includes every eligible source currency using stored historical ECB
rates. Selecting `GBP`, `USD`, `EUR`, `CHF` or `ILS` instead shows **only** grants
originally recorded in that currency — it is not a conversion.

Coverage counters explicitly change from *ingested* to *filtered* grants when a scope is
applied.

Organization-directory search, income/expenditure and headquarters filters remain
independent, because they describe organization records rather than the filtered grant
population. Grant filters must not silently change organization-level metrics.

The canonical implementation is `frontend/src/lib/grantScope.ts`.

## Observed Donor Directory

`GET /api/charities/grants/funders` is a derived, paginated aggregation over the stored
grant population. It creates no new organization profile and uses no external data.

**Identity** — a source funder's deterministic key is its source namespace plus
`funding_org_source_id`; only where that ID is unavailable does it fall back to the
normalized `funding_name`. The identity deliberately does **not** change when an optional
enriched-profile link is later added.

A narrow reproducible `grant_source_funder_facts` table keeps list filtering, aggregation,
sorting and pagination away from the wide raw-JSON grant column.

**Parameters** — requires `beneficiary_country` as a canonical ISO alpha-2 code. Accepts
the same grant-scope fields as the Overview (`currency`, `date_from`, `date_to`,
`beneficiary_geographies`, `programme_areas`, `donor`, `recipient`, `sources`) plus
backend `search`, `profile_status`, `sort`, `page`, `page_size`. Returns typed source
identity, observed activity, amount policy, evidence sources, and explicit zero/one/many
profile-link status.

`GET /api/charities/grants/funders/{source_funder_key}` supports
`detail_level=summary|full`; the UI loads full grant/recipient/evidence sections only when
opened.

**Amount rules for a selected country:**

- Multi-country grants count once for activity and recency, but their full amount is
  **excluded** from the country-attributable funding total.
- In `currency=auto`, only stored EUR values with `native_eur`, `ecb_award_date` or
  `ecb_previous_business_day` conversion status are monetary-eligible.
- In an explicit currency mode, only original grants in that currency are considered.

A verified Directory profile is linked only where one already exists. **Source-only
funders remain source-only and must never be converted into invented profiles.**

Selecting a map country opens the Donor Directory rather than applying the country as an
organization-profile location filter. It uses the map's canonical beneficiary-country
association (including the UK roll-up) and preserves the full grant scope through a shared
typed URL contract, so a country with observed funding leads to the source-funder evidence
that produced the map instead of an often-empty registered-address lookup.

State that survives refresh, back/forward and copied URLs: `funder_country`, canonical
`grant_*` filters, search, status, sorting, page, sources and selected donor.

## Partial profiles

Directory profiles without a cached raw Charity Commission detail object still expose a
schema-valid **partial** detail view assembled from normalized organization fields. Their
registration status is reported as `UNKNOWN`, unavailable contact and financial sections
remain empty, and the API does not invent missing source values or fail the entire profile
request.

## Grant overview aggregations

One authenticated `GET /api/charities/grants/overview` call serves the Overview's grant
KPIs, beneficiary map, time series and programme allocation. Accepts `currency`,
`date_from`, `date_to`, `beneficiary_geographies`, `programme_areas`, `donor`, `recipient`
and `granularity` (`auto`, `monthly`, `yearly`).

### Grant Awards Over Time

Groups `grants.amount` by the calendar month or year of `grants.date`, explicitly
interpreted as the **award date**. Auto uses monthly aggregation for a selected period up
to 24 months and yearly beyond that.

Empty periods are returned as **unknown coverage with null values**, never as confirmed
zero activity. Date presets are calculated from the actual cached-source range, so the
chart does not extend into arbitrary future months.

### Grant Allocation by Programme Area

1. Normalize `programme_area_source`. A valid taxonomy match takes precedence.
2. Otherwise accept `programme_area_inferred` categories whose stored score meets the
   0.55 enrichment review threshold.
3. Everything else remains visible as `Unclassified`.

A multi-category grant is split in **minor currency units** across its categories with
deterministic remainder assignment, so allocated amounts reconcile exactly to qualifying
source amounts.

Negative source values are treated as possible corrections or reversals, excluded from
presentation sums, and reported in exclusion metadata. Numeric zero values remain
included. No implicit currency conversion or upper-value rejection is applied.

The chart defaults to the largest substantive categories, groups the remainder as `Other`,
and keeps `Unclassified` visible in a neutral treatment unless the user explicitly selects
classified-only mode. Philea records are absent because the cache contains no Philea
grant-level transactions.

## Explainable relevance score

**No client-approved score definition, target variable or weights exist.** The included
`example-relevance-v2` configuration is explicitly `experimental` and measures only
relevance to a selected target profile. It is **not** a probability, recommendation,
financial forecast, or prediction of donation behaviour.

Default example weights:

| Component | Weight |
|---|---:|
| Thematic fit | 0.35 |
| Geographic fit | 0.25 |
| Funding-capacity fit | 0.15 |
| Historical grant-size fit | 0.15 |
| Organization-type fit | 0.10 |

Each component exposes its inputs, method, confidence and missing reason. Overall
confidence and data completeness are returned **separately** from the score. Missing
components are excluded by renormalizing the available weights — never silently scored as
zero. Financial and grant comparisons occur only when currencies match.

To use a reviewed configuration: copy the example, preserve the validated schema, set
`configuration_status` appropriately, and point `SCORE_CONFIG_PATH` at it. Approval should
cover the business target, weights, thresholds, missing-data policy and evaluation
criteria.

## Demonstration flow

A sequence that exercises the honest parts of the system in a defensible order:

1. **Overview** — full-width Global Grant Distribution map. Switch between grant-country
   associations and funding mode, select a country to open its explorer, then show Grant
   Awards Over Time and Grant Allocation by Programme Area below.
2. **Donor Directory** from a selected map country — observed/linked status, backend
   search/sort/page, right-side donor detail, ranked recipients, explicit source evidence.
   Organization Research and Advanced Charity Commission Search are secondary tools.
3. **Charity Projects** (`326568`, whose source grant records use the funder name Comic
   Relief) — Charity Commission identity and provenance, source versus inferred
   classifications, evidence and review state, observed 360Giving grants, donor-to-recipient
   Sankey.
4. **Coverage honesty** — the map's 62.86% known-country disclosure and 39 multi-country
   exclusions. Explain why headquarters is not substituted for missing transaction
   geography, and why multi-country amounts are neither divided nor duplicated.
5. **Women Win** (`-24788`) — Philea organization type and source, with the explicit
   `organization_level_only` transaction status.
6. **Score** — components, confidence, completeness, missing inputs, version, assumptions
   and the "not a prediction" label.
7. **Admin last** — prefer `quick_consolidate` for the cached rebuild. Never launch an
   uncontrolled external scrape during a demonstration.
