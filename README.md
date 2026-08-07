# Foundation Intelligence Platform

An explainable proof-of-concept for exploring foundations, charities, and observed grant relationships. The current presentation dataset combines cached Charity Commission for England and Wales records, cached 360Giving grant transactions, and cached Philea member-directory records. UK organization and grant coverage is the strongest part of the prototype; DACH/European coverage is partial and primarily organization-level.

The platform deliberately separates source facts, normalized source values, deterministic inferences, and illustrative fallback content. It does not predict whether an organization will donate.

## Current status

| Capability | Status | Current evidence / limitation |
|---|---|---|
| Cached UK organization ingestion | Complete | 65 normalized UK-side organizations in the current rebuilt database |
| Cached 360Giving grant ingestion | Sampled | 302,546 observed transactions with source provenance in the current migration source; coverage is not complete 360Giving |
| Cached Philea organization ingestion | Complete | 299 records; organization-level only, with no grants assigned |
| DACH foundation intelligence | Partial | Philea and deterministic geography normalization provide some European/DACH discoverability; this is not a complete DACH registry or grant dataset |
| Organization directory and detail | Complete | PostgreSQL-backed operational API/UI; SQLite is retained only as a local migration source |
| PostgreSQL migration/cutover | Complete locally | Exact reconciliation, 18 zero-difference shadow projections, full restore and dataset rollback pass; AWS/production cutover is unexecuted |
| Programme-area enrichment | Complete | Versioned deterministic taxonomy/rules and evidence; accuracy has not been externally validated |
| Geographic-focus enrichment | Complete | Versioned deterministic rules and evidence; distinct from headquarters and beneficiary geography |
| Grant list and network summary | Complete | Observed 360Giving transactions only |
| Sankey | Complete | Donor-to-recipient flows from stored transactions, never operating-cost estimates |
| Beneficiary map | Complete | Displays only when normalized beneficiary geography meets the coverage threshold |
| Relevance score | Experimental | Explainable example configuration; not client-approved and not a prediction |
| News summary | Partial | Live Google News/Claude path requires credentials and network access |
| Offline dashboard fallback | Mocked | Clearly labelled local prototype values; grant/map/score data are not fabricated offline |
| Monthly grant awards and programme allocation | Complete | Auto mode aggregates historical ECB-converted EUR values; a concrete currency selector remains source-currency-only |
| Complete DACH grant transactions | Missing | No source currently supplies this coverage |
| Enrichment predictive accuracy | Not verified | Coverage is reported; labelled validation data do not exist |
| Client-approved score definition | Blocked | No approved target, weights, or decision policy was found in the repository |

## Architecture and component connections

```text
cached source JSON / optional source APIs
        │
        ▼
scrapers ──► consolidation ──► deterministic enrichment ──► JSONL
                                  │                         │
                     Philea normalization/dedup ───────────┘
                                                            │
                                                            ▼
                                         coherent SQLite migration source
                                                            │
                                   deterministic versioned migration/reconcile
                                                            │
                                                            ▼
React/Vite UI ◄── cookie-authenticated JSON ── FastAPI BFF
                                                    │
                                                    ├── async PostgreSQL repositories
                                                    ├── versioned analytics/materialisations
                                                    ├── durable job/outbox workers
                                                    ├── experimental score engine/config
                                                    └── optional approval-gated news service
```

The major components are:

- `src/scrapers/`: source-specific collectors for the Charity Commission, 360Giving, Philea, and the legacy Hinchilla source. Live scraping is optional; the presentation rebuild uses checked-in caches.
- `src/preprocessing/consolidate.py`: maps Charity Commission and 360Giving records into common organization and grant records.
- `src/preprocessing/enrichment.py`: the one active, versioned source of programme and geography taxonomy/rules. It keeps source values and inferred values separate.
- `src/preprocessing/philea_adapter.py`: normalizes all cached Philea records, assigns stable negative local IDs, maps organization types, records provenance, and performs conservative cross-source deduplication.
- `src/data/db_loader.py`: maintains the coherent SQLite migration source for local ingestion compatibility; it is not an operational production datastore.
- `alembic/`, `src/migration/`, and `src/bff/postgres/`: own the PostgreSQL schema, lossless versioned migration/reconciliation, rollback controls and operational repositories.
- `src/pipelines/backfill_ecb_exchange_rates.py`: fetches/caches official ECB daily EXR rates and atomically backfills reproducible EUR values without altering source amounts.
- `src/pipelines/run_pipeline.py`: orchestrates collection, consolidation, enrichment, reports, and database publication.
- `src/bff/`: FastAPI entry point, demo cookie authentication, repositories, organization/grant endpoints, pipeline controls, proxy, and optional news summary.
- `src/scoring/engine.py` plus `config/scoring.example.json`: deterministic, configurable, explainable target-profile relevance scoring.
- `frontend/src/App.tsx`: React presentation UI, filters, details, grant table, map, Sankey, provenance labels, enrichment evidence, and score explanation.
- `src/tests/`: unit, regression, database-stability, API, transaction, enrichment, Philea, and scoring tests.

## Data sources and provenance

The checked-in source caches currently contain 62 Charity Commission records, a baseline 360Giving cache, an observed 360Giving publisher-pilot import, and 299 Philea member records. Consolidation creates 65 UK-side organization rows alongside the Philea records.

The current preserved SQLite migration source and reconciled PostgreSQL active dataset contain:

- 373 organizations across Charity Commission/360Giving and Philea identities.
- 302,546 observed grants in GBP, USD, EUR, ILS, and CHF. Original amount and currency remain source facts; this is a reproducible sampled source, not complete 360Giving coverage.
- 397,469 current registry rows and 345 accepted registry/profile links.
- Auto EUR display uses the official daily ECB EXR reference rate for the award date; weekends and ECB holidays use the previous published business-day rate. Each converted grant stores the conversion status, rate, rate date, and source. 34 pre-1999 GBP grants remain explicitly unconverted because no ECB EUR reference rate exists for their award dates.
- 299 Philea organizations marked `organization_level_only`; no grant is attached to a synthetic Philea ID.

Raw source records remain traceable through source name, source record ID, source URL where supplied, ingestion timestamp, and retained raw payload fields. Organization records also retain source-record arrays and deduplication status/candidates. Derived data is stored separately:

- `programme_areas_source`: normalized classifications present in source data.
- `programme_areas_inferred`: deterministic regex-derived classifications.
- `geographic_focus_source`: normalized source-described operating/funding geographies.
- `geographic_focus_inferred`: deterministic text-derived focus geographies.
- `headquarters_country` / `headquarters_region`: where the organization is based; never treated as beneficiary geography.
- `beneficiary_geography_normalized`: grant-recipient/project geography used by the map.

`src/data/charities.db`, generated JSONL, and generated coverage reports are ignored build artifacts. The checked-in raw caches are the reproducible presentation inputs.

### Scalable two-layer organization directory

The platform intentionally separates two kinds of organization data:

1. **Registry layer** — all available Charity Commission registration records, stored in `charity_registry_organizations`. These are lightweight official register rows: registration identity and status, reported income/expenditure, registered-office fields, activity text, source date, and import freshness.
2. **Enriched layer** — the existing `charities` table. These smaller profiles can contain Philea metadata, deterministic classifications, observed 360Giving relationships, scores, and platform-derived fields.

`organization_registry_links` records the relationship between the layers. The current automated importer creates only `accepted` `exact_identifier` links where a Charity Commission number equals the enriched profile ID. It deliberately does not auto-accept name-only fuzzy matches. Registry rows with no accepted link remain registry-only and display “No observed grant data” rather than “No funding”.

The registry table has a stable `cc:{organisation_number}` key, retains the original Charity Commission number as a searchable field, and has no grants, scores, or large raw JSON payload. SQLite indexes cover charity number, normalized name, status, income, expenditure, country/region, postcode, and link lookups. When FTS5 is available, `charity_registry_fts` provides Unicode-aware name search; the fallback is an indexed normalized-name prefix search, never an unbounded `%query%` scan.

The register directory is intentionally paginated. `GET /api/charities/directory/organizations` returns at most 100 rows (50 by default), a deterministic cursor based on the active sort plus `registry_id`, and no grant history. The browser uses a 300 ms debounced server-side query and only requests details after a user opens a result.

Registry addresses mean registered office only. They are never beneficiary geography, project geography, a grant recipient assertion, or a grant-funder assertion. The `Global Grant Distribution` map remains derived solely from stored 360Giving grants and explicit grant beneficiary geography. A future registered-office map, if needed, must be a separately labelled UK-focused aggregated visualization rather than a funding map.

### Organization types

Charity Commission types such as `CIO`, `Charitable company`, `Trust`, `Charity`, `Other`, and `Funder` are retained. Philea values are normalized as:

- `foundation` → `foundation`
- `affiliate` → `philanthropy infrastructure organization`
- `member` → `membership organization`
- anything else → `unknown`

Philea IDs are negative deterministic local IDs so they cannot collide with positive UK charity registration numbers. Exact normalized names/domains can merge across sources. Fuzzy matches at or above 0.92 can auto-merge only across sources; scores from 0.82 through 0.92 are review candidates. In the current cache, all 299 records were added, none auto-merged or rejected, and 19 ambiguous candidates were retained for review.

## Deterministic enrichment

The active rule version is `deterministic-enrichment-v1`. Rules compile and validate at import time, use token boundaries, retain matched excerpts and rule IDs, and inspect a 48-character context window for negation such as `not`, `without`, and `exclude`. Ambiguous country names such as Jordan and Georgia receive lower confidence and a review flag.

The programme taxonomy has 15 categories: Arts & Culture; Citizenship, Social Justice & Public Affairs; Civil society, Voluntarism & Non-Profit Sector; Diversity & Inclusion; Education; Environment/Climate; Food, Agriculture & Nutrition; Health; Human/Civil Rights; Humanitarian & Disaster Relief; Peace & Conflict Resolution; Sciences & Research; Socio-economic Development, Poverty; Youth/Children Development; and tech-enablement.

The latest generated coverage report processed 2,958 organization-plus-grant records. It found 2,035 with source programme areas, 1,907 with inferred programme areas, 300 without a programme area, 2,145 with source geography, 1,035 with inferred geography, 636 without geography, 213 requiring review, 0 detected classification conflicts, and 77 with insufficient source text. These are coverage counts, not precision/recall or proof of correctness.

Geographic concepts are intentionally distinct:

- Headquarters: the organization's registered or directory location; used by foundation-region filters.
- Geographic focus: where an organization says it works or funds; source and inferred values remain separate.
- Beneficiary/project geography: a transaction's destination; used by recipient-region filters and the map.

The `Global Grant Distribution` map currently resolves beneficiary-country geography for 104,191 of the 302,546 ingested grants (34.44%). These produce 104,309 country associations because explicitly multi-country grants retain every supported association. It uses `beneficiary_geography_normalized` first and falls back only to explicit ISO country codes or explicit country names in the original `beneficiary_geography` source field. It never consults funder headquarters, recipient registered offices, or inferred operating regions. England, Scotland, Wales, and Northern Ireland roll up to the United Kingdom shape while their original labels are retained in country detail. The remaining 198,355 records are reported as unmapped rather than assigned a fabricated country.

The Overview `Filters` button opens a non-layout-shifting, scrollable global grant-filter drawer for award date, currency, beneficiary geography, programme area, donor, recipient, and time aggregation. `Auto · EUR converted` includes every eligible source currency using stored historical ECB rates; selecting `GBP`, `USD`, `EUR`, `CHF`, or `ILS` instead shows only grants originally recorded in that currency. One applied grant scope drives the Overview KPIs, map, trends, and programme allocation; coverage counters explicitly change from ingested to filtered grants. The map header retains only display controls and the illustrative-connections toggle. Organization-directory search, income/expenditure, and headquarters filters remain independent because they describe organization records rather than the filtered grant population.

Selecting a country opens the primary **Donor Directory** rather than applying the country as an organization-profile location filter. It uses the map's canonical beneficiary-country association (including the UK roll-up) and preserves currency, date, beneficiary geography, programme area, donor, recipient, and selected sources through a shared typed URL contract. This means a country with observed funding leads to the source-funder evidence that produced the map instead of an often-empty registered-address lookup.

### Observed Donor Directory

`GET /api/charities/grants/funders` is a derived, paginated aggregation over the stored grant population; it does not create a new organization profile or use external data. A source funder has a deterministic identity made from its source namespace plus `funding_org_source_id`; only where that ID is unavailable does it fall back to the normalized `funding_name`. The identity deliberately does not change when an optional enriched-profile link is later added. A narrow reproducible `grant_source_funder_facts` table keeps list filtering, SQL aggregation, sorting, and pagination away from the wide raw-JSON grant column.

The endpoint requires `beneficiary_country` as a canonical ISO alpha-2 code and accepts the same grant-scope fields as the Overview (`currency`, `date_from`, `date_to`, `beneficiary_geographies`, `programme_areas`, `donor`, `recipient`, and `sources`) plus backend `search`, `profile_status`, `sort`, `page`, and `page_size`. It returns typed source identity, observed activity, amount policy, evidence sources, and explicit zero/one/many profile-link status. `GET /api/charities/grants/funders/{source_funder_key}` supports `detail_level=summary|full`; the primary UI loads full grant/recipient/evidence sections only when opened.

For a selected country, multi-country grants count once for activity and recency, but their full amount is excluded from the country-attributable funding total. In `currency=auto`, only stored EUR values with `native_eur`, `ecb_award_date`, or `ecb_previous_business_day` conversion status are monetary eligible. In an explicit currency mode, only original grants in that selected currency are considered. A verified Directory profile is linked only where it already exists; source-only funders remain source-only and must never be converted into invented profiles.

The current active 302,546-grant dataset is reproducible and checksum-bound, but remains sampled rather than complete 360Giving coverage. Its exact source/target counts and controls are recorded in `docs/remediation/evidence/phase4-migration-manifest.json`. The optimized source-funder list uses normalized beneficiary/programme indexes and a versioned narrow fact table; supported loaders invalidate it after data changes. See the Phase-6 performance evidence for current PostgreSQL timings; older donor-directory reports remain historical evidence.

The map CTA writes `funder_country` plus canonical `grant_*` filters to the URL. Search, status, sorting, page, sources, and selected donor also survive refresh/back/forward and copied URLs. Desktop uses a compact list plus right-side detail panel; tablet/mobile use a full-width/full-screen sheet. Real-browser acceptance is still outstanding and the legacy donor view remains available; see `docs/donor_directory_responsive_validation.md`.

Directory profiles without a cached raw Charity Commission detail object still expose a schema-valid partial detail view assembled from normalized organization fields. Their registration status is reported as `UNKNOWN`, unavailable contact/financial sections remain empty, and the API does not invent missing source values or fail the entire profile request.

An optional connection layer draws up to the 36 strongest registered-funder-location-to-beneficiary-country associations. The origin uses an explicit 360Giving funding-organization address country where present and otherwise the matched organization directory's registered headquarters country. These arrows are labelled in both settings and the map as illustrative associations, not verified financial routes; headquarters never substitutes for beneficiary geography or proves where a payment originated.

Grant-count mode counts a grant once in each explicitly associated country; the UI therefore labels the metric as grant-country associations when multi-country records are present. The current cache contains 58 such grants. Funding mode includes only non-negative, single-country amounts in one selected currency. It never repeats or invents allocations for multi-country awards. The map's country totals must not be added to excluded multi-country amounts or interpreted as complete 360Giving/global-market coverage.

## Grant overview aggregations

The responsive Overview uses one authenticated `GET /api/charities/grants/overview` aggregation for its grant KPIs, beneficiary map, time series, and programme allocation. It accepts `currency`, `date_from`, `date_to`, `beneficiary_geographies`, `programme_areas`, `donor`, `recipient`, and `granularity` (`auto`, `monthly`, or `yearly`). The UI keeps these filters in the URL so a filtered view survives refresh and can be shared. Organization-directory income and expenditure are intentionally excluded: grant filters do not silently change organization-level metrics.

`Grant Awards Over Time` groups `grants.amount` by the calendar month or year of `grants.date`, explicitly interpreted as the award date. Auto uses monthly aggregation for a selected period of up to 24 months and yearly aggregation for longer periods. Empty periods are returned as unknown coverage with null values, not as confirmed zero activity. The date presets are calculated from the actual cached-source range, so the chart does not extend into arbitrary future months.

`Grant Allocation by Programme Area` first normalizes `programme_area_source`; only a valid taxonomy match takes precedence. Otherwise it accepts `programme_area_inferred` categories whose stored score meets the existing 0.55 enrichment review threshold. Everything else remains visible as `Unclassified`. A multi-category grant is split in minor currency units across its categories, with deterministic remainder assignment, so allocated amounts reconcile exactly to qualifying source amounts. Negative source values are treated as possible corrections/reversals, excluded from these presentation sums, and reported in exclusion metadata. Numeric zero values remain included. No implicit currency conversion or upper-value rejection is applied.

The programme chart defaults to the largest substantive categories, groups the remainder as `Other`, and keeps `Unclassified` visible in a neutral treatment unless the user explicitly selects classified-only mode. Philea organization records are not included because the cache contains no Philea grant-level transactions.

## Explainable relevance score

No client-approved score definition, notes, target variable, or weights were found. The included `example-relevance-v2` configuration is therefore explicitly `experimental` and measures only relevance to a selected target profile. It is not a probability, recommendation, financial forecast, or prediction of donation behavior.

Default example weights are thematic fit 0.35, geographic fit 0.25, funding-capacity fit 0.15, historical grant-size fit 0.15, and organization-type fit 0.10. Component calculations expose their inputs, method, confidence, and missing reason. Overall confidence and data completeness are returned separately from the score. Missing components are excluded by renormalizing the available weights; they are never silently scored as zero. Financial/grant comparisons occur only when currencies match.

To use a reviewed configuration, copy the example, preserve the validated schema, set `configuration_status` appropriately, and point `SCORE_CONFIG_PATH` at it. Approval should include the business target, weights, thresholds, missing-data policy, and evaluation criteria.

## Local setup

Prerequisites: Python 3.12, Node.js 22/npm, Docker Desktop and Compose.

```bash
git checkout 91-clean-up-code-for-aws-integration
python3.12 -m venv venv
./venv/bin/python -m pip install --require-hashes -r requirements-locking.txt
./venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env

cd frontend
npm ci --ignore-scripts --no-audit --no-fund
cp .env.example .env
cd ..
```

The root `.env` is ignored by Git and loaded by the BFF configuration. `frontend/.env` is also ignored, but every `VITE_*` value is embedded in browser code and must not contain a real secret. The demo username/password are visible client configuration and are not production authentication.

Operationally relevant variables include `DATA_RUNTIME_MODE` (PostgreSQL by default), `DATABASE_*`, `AUTH_MODE`, `OIDC_*`, `CORS_ORIGINS`, request/rate limits, proxy allowlists and `SCORE_CONFIG_PATH`. See `docs/remediation/environment-variable-reference.md`. Optional news credentials remain separately approval-gated. Every frontend `VITE_*` value is public build-time configuration and must not contain a secret.

### Build the presentation database

Use the cached-source consolidation path for a deterministic local presentation build. It does not call the source APIs and skips optional website contact crawling:

```bash
PYTHONPATH=src ./venv/bin/python src/pipelines/run_pipeline.py \
  --source consolidate \
  --skip-contact-crawler
```

This writes ignored JSONL/reports under `src/data/preprocessed/` and atomically replaces `src/data/charities.db` only after schema and minimum-data validation. A failed staging load leaves the active database untouched. `full_run`, `refresh_charities`, and `refresh_grants` invoke external sources and should be used only intentionally, with conservative limits.

### Curate an EU/EEA/Switzerland tech-enablement grant profile

The existing 360Giving cache can be screened without changing the active database.
The curation step uses only an explicit `beneficiaryLocation` country, excludes the
UK, accepts EU-27 plus Iceland, Liechtenstein, Norway, and Switzerland, and applies
the existing deterministic `tech-enablement` rule at confidence 0.8 or greater. It
prioritises DACH (Germany, Austria, Switzerland) up to a best-effort 60% share, but
does not infer a country or pad the requested target when evidence is missing.

```bash
PYTHONPATH=src ./venv/bin/python -m pipelines.curate_europe_tech_grants \
  --input src/data/raw/threesixtygiving_results.json \
  --target 10000
```

The generated `src/data/processed/eu_tech_dach_grants.jsonl` retains each source
grant together with the country and programme-selection evidence. Its matching
`eu_tech_dach_report.json` records coverage, the achieved DACH share, and any target
shortfall. These generated artifacts are ignored by Git and the command does not
replace `src/data/charities.db`.

### Append a bounded 360Giving publisher sample to the map

The random-publisher pilot and its importer are separate from the normal source
cache. The importer is append-only and runs against a cloned staging database:
grants already present by `grant_id` are preserved, while new observed 360Giving
grants are added atomically. A publisher-record limit freezes a reproducible
prefix of the resumable pilot file.

```bash
PYTHONPATH=src ./venv/bin/python -m pipelines.import_observed_360giving_grants \
  --input src/data/processed/360giving_registry_publisher_pilot.json \
  --publisher-record-limit 60 \
  --database src/data/charities.db \
  --report src/data/processed/360giving_registry_publisher_pilot_import.json
```

When multiple source currencies are present, the default `Auto · EUR converted`
mode displays all eligible grants with historical ECB-derived EUR totals. Selecting
a concrete currency remains a strict original-currency filter, so no raw source
amounts are mixed.

### Backfill historic ECB EUR values

After adding grants from a new source import, refresh the local ECB cache and run
the atomic backfill before treating them as part of Auto EUR totals:

```bash
PYTHONPATH=src ./venv/bin/python -m pipelines.backfill_ecb_exchange_rates \
  --database src/data/charities.db \
  --report src/data/processed/ecb_exchange_rate_backfill_report.json
```

The pipeline downloads the daily official ECB EXR series once per source
currency/date range, stores the used rate rows in `exchange_rates`, and only then
publishes a cloned database. It preserves `amount` and `currency`; `amount_eur`
is a derived field accompanied by rate date, series, and conversion status.

### Import the full Charity Commission directory

The local bulk extract can be imported without loading its full JSON array into memory. The command applies the additive registry migration, streams the official `publicextract.charity.json` source in batches, upserts by stable organisation number, refreshes only accepted exact-ID links, and records source freshness. Existing enriched profiles and grants are not replaced.

To apply or reconcile just the additive schema on an already initialized application database:

```bash
PYTHONPATH=src ./venv/bin/python -m data.registry \
  --db src/data/charities.db --migrate-only
```

```bash
PYTHONPATH=src ./venv/bin/python -m data.registry \
  --db src/data/charities.db \
  --source src/data/raw/charity_commission_bulk/extracted/publicextract.charity.json \
  --batch-size 1000
```

Re-running the command is idempotent: existing registry records are updated rather than duplicated. A record missing from a successfully completed later source import is retained for auditability and marked non-current rather than silently deleted. Recovery is safe: restore the prior SQLite file if needed, then rerun the importer. The normal enriched-data rebuild preserves the registry tables while replacing only the generated `charities` and `grants` layer.

Useful directory requests:

```text
GET /api/charities/directory/organizations?query=alpha&limit=50
GET /api/charities/directory/organizations?charity_number=200027
GET /api/charities/directory/organizations?status=Registered&income_min=100000&sort=income_desc
GET /api/charities/directory/organizations/{registry_id}
```

For a local performance and query-plan report after import:

```bash
PYTHONPATH=src ./venv/bin/python -m data.benchmark_registry \
  --db src/data/charities.db --query foundation --charity-number 200027
```

SQLite is intentional for the current read-heavy internal/demo deployment. PostgreSQL becomes appropriate for sustained public multi-user use, frequent concurrent writes, more advanced spatial querying, or operational controls that exceed SQLite’s single-writer model; approximately 400,000 read-mostly directory rows alone do not require a database migration.

### Start each runtime component

Terminal 1 — rebuild first if needed, then start the BFF:

```bash
./start_backend.sh
```

The BFF listens at `http://127.0.0.1:8000`, redirects `/` to Swagger, and exposes health at `/health`. `start_backend.sh` expects the repository-local `venv`.

Terminal 2 — start the UI:

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. The UI checks BFF health, performs demo login, then loads statistics, directory rows, and beneficiary-map data in parallel. Selecting an organization loads detail, observed grants, Sankey data, and the experimental score. The admin view polls pipeline status/logs and can launch explicit pipeline modes.

Optional containerized BFF:

```bash
docker compose up --build bff
```

The frontend is not containerized by the current compose file.

## API surface

All `/api/*` routes require the session cookie returned by `POST /api/auth/login`; `/health` and Swagger availability do not establish an authenticated session.

| Method and path | Purpose |
|---|---|
| `POST /api/auth/login` | Validate demo credentials and issue an HTTP-only cookie |
| `POST /api/auth/logout` | Clear the session |
| `GET /api/charities` | Search/filter/paginate organizations |
| `GET /api/charities/directory/organizations` | Cursor-paginated lightweight Charity Commission registry directory; `query`, `charity_number`, status, financial, registry geography, accepted-link beneficiary geography, enriched/grant flags, `cursor`, `limit`, and `sort` are supported |
| `GET /api/charities/directory/organizations/{registry_id}` | Lazy official registry detail plus an accepted enriched-profile link, where present |
| `GET /api/charities/stats` | Dataset KPIs, source counts, and organization-type counts |
| `GET /api/charities/{id}` | Organization detail, provenance, and enrichment evidence |
| `GET /api/charities/{id}/grants?role=all|funder|recipient` | Observed transactions and coverage status |
| `GET /api/charities/grants/summary` | Currency-separated network totals and rankings |
| `GET /api/charities/grants/map?currency=GBP` | Filterable beneficiary-country associations, currency-safe funding totals, disclosed HQ-to-beneficiary connection groups, country explorer rankings, and coverage/exclusion metadata |
| `GET /api/charities/grants/funders?beneficiary_country=US` | SQL-filtered/paginated observed donor ranking with backend `search`, `profile_status`, sort, and canonical grant-scope filters |
| `GET /api/charities/grants/funders/{source_funder_key}?beneficiary_country=US&detail_level=summary` | Selected source-funder detail; summary-first with lazy full recipients, grants, and typed evidence, never a synthetic organization profile |
| `GET /api/charities/grants/trends?currency=GBP&months=24` | Award-date monthly grant totals with unknown-coverage months and exclusions |
| `GET /api/charities/grants/themes?currency=GBP` | Minor-unit-preserving programme allocations and classification coverage |
| `GET /api/charities/{id}/sankey` | Auto EUR-converted observed donor-to-recipient flow; a concrete currency parameter retains source-currency-only behaviour |
| `POST /api/charities/{id}/score` | Experimental target-profile relevance score and explanation |
| `GET /api/news/{name}/summary` | Optional sourced news summary |
| `GET /api/admin/pipeline/status` | Pipeline state |
| `GET /api/admin/pipeline/logs` | Last 100 pipeline log lines |
| `POST /api/admin/pipeline/trigger` | Start an allowed background pipeline mode |
| `/api/core/{path}` | Authenticated proxy to the configured downstream core API |

List filters include `search`, `reg_status`, `tags`, `foundation_regions`, `funding_regions`, `min_annual_giving`, `min_avg_grant_size`, `skip`, and `limit`. Transaction endpoints expose statuses such as `available`, `organization_level_only`, `transaction_data_unavailable`, and mixed-currency requirements so absent data is not represented as zero activity.

## Tests, build, and CI

```bash
# Fast local backend suite
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ./venv/bin/pytest -q -p no:cacheprovider src/tests

# Same backend coverage gate used by CI
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ./venv/bin/pytest src/tests --cov=bff --cov-fail-under=70

# Syntax/undefined-name gate
./venv/bin/flake8 src/ --count --select=E9,F63,F7,F82 \
  --show-source --statistics

# Frontend reproducible install and production build
cd frontend
npm ci
npm run test
npm run lint
npm run build
```

GitHub Actions runs separate Python 3.12 backend and Node.js 22 frontend jobs. The backend job installs requirements, runs the blocking flake8 checks, compiles Python, and enforces 70% BFF coverage. The frontend job performs `npm ci` and the TypeScript/Vite production build.

## Presentation flow

1. Start on Overview and show the full-width Global Grant Distribution map. Switch between grant-country associations and GBP funding, select a country to open its slim explorer, then show Grant Awards Over Time and Grant Allocation by Programme Area in the balanced row below it.
2. Select a map country and open Donor Directory. Demonstrate observed/linked status, backend search/sort/page, the right-side donor detail, ranked recipients, and explicit source evidence. Open Organization Research or Advanced Charity Commission Search only as secondary research tools.
3. Open Charity Projects (`326568`, whose source grant records use the funder name Comic Relief) to show Charity Commission identity/provenance, source versus inferred classifications, evidence/review state, observed 360Giving grants, and the donor-to-recipient Sankey.
4. Show the map's 62.86% known-country disclosure and 39 multi-country exclusions; explain why headquarters is not substituted for missing transaction geography and why multi-country amounts are not divided or duplicated.
5. Open Women Win (`-24788`) to show Philea organization type/source and the explicit `organization_level_only` transaction status.
6. Show the experimental score components, confidence, completeness, missing inputs, version, assumptions, and “not a prediction” label.
7. Open Admin last. Prefer `quick_consolidate` for the cached rebuild; do not launch an uncontrolled external scrape during the presentation.

## Known limitations and remaining prototype data

- The data is a bounded proof-of-concept snapshot, not a comprehensive UK, DACH, or European foundation database.
- Philea contributes organization metadata only. No transaction activity is inferred from membership.
- Enrichment coverage is measured, but accuracy is not validated against labelled ground truth; evidence and review flags must remain visible.
- The relevance score is an unapproved example and must not be framed as a donation likelihood.
- When the BFF is offline, KPI/cards/detail/news/admin simulation use clearly labelled local mock content; grant charts, flows, map values, and scores remain unavailable rather than fabricated.
- The news route depends on current external pages, Google News decoding, and Claude-compatible credentials, so it is not deterministic.
- Demo authentication defaults and frontend-visible credentials are suitable only for local presentation use.
- The production frontend build still emits a bundle-size warning above Vite's 500 kB advisory threshold. Registry and legacy donor views are now split, while remaining legacy profile/chart code still requires further extraction.
- Live pipeline modes depend on upstream availability and can take time. Use cached consolidation for reproducibility.
- Hinchilla code and historical preprocessed artifacts remain in the repository for compatibility/reference, but Hinchilla is not part of the active presentation rebuild.
