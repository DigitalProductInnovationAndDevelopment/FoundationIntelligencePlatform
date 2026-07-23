# Foundation Intelligence Platform

An explainable proof-of-concept for exploring foundations, charities, and observed grant relationships. The current presentation dataset combines cached Charity Commission for England and Wales records, cached 360Giving grant transactions, and cached Philea member-directory records. UK organization and grant coverage is the strongest part of the prototype; DACH/European coverage is partial and primarily organization-level.

The platform deliberately separates source facts, normalized source values, deterministic inferences, and illustrative fallback content. It does not predict whether an organization will donate.

## Current status

| Capability | Status | Current evidence / limitation |
|---|---|---|
| Cached UK organization ingestion | Complete | 65 normalized UK-side organizations in the current rebuilt database |
| Cached 360Giving grant ingestion | Complete | 3,096 GBP transactions with source provenance |
| Cached Philea organization ingestion | Complete | 299 records; organization-level only, with no grants assigned |
| DACH foundation intelligence | Partial | Philea and deterministic geography normalization provide some European/DACH discoverability; this is not a complete DACH registry or grant dataset |
| Organization directory and detail | Complete | SQLite-backed API and UI, with source/type/coverage labels |
| Programme-area enrichment | Complete | Versioned deterministic taxonomy/rules and evidence; accuracy has not been externally validated |
| Geographic-focus enrichment | Complete | Versioned deterministic rules and evidence; distinct from headquarters and beneficiary geography |
| Grant list and network summary | Complete | Observed 360Giving transactions only |
| Sankey | Complete | Donor-to-recipient flows from stored transactions, never operating-cost estimates |
| Beneficiary map | Complete | Displays only when normalized beneficiary geography meets the coverage threshold |
| Relevance score | Experimental | Explainable example configuration; not client-approved and not a prediction |
| News summary | Partial | Live Google News/Claude path requires credentials and network access |
| Offline dashboard fallback | Mocked | Clearly labelled local prototype values; grant/map/score data are not fabricated offline |
| Monthly grant awards and programme allocation | Complete | Currency-isolated aggregations from cached 360Giving grants with exclusions and coverage metadata |
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
                                               atomic SQLite rebuild
                                                            │
                                                            ▼
React/Vite UI ◄── cookie-authenticated JSON ── FastAPI BFF
                                                    │
                                                    ├── organization/grant repository
                                                    ├── experimental score engine/config
                                                    ├── pipeline monitor/trigger
                                                    └── optional news service
```

The major components are:

- `src/scrapers/`: source-specific collectors for the Charity Commission, 360Giving, Philea, and the legacy Hinchilla source. Live scraping is optional; the presentation rebuild uses checked-in caches.
- `src/preprocessing/consolidate.py`: maps Charity Commission and 360Giving records into common organization and grant records.
- `src/preprocessing/enrichment.py`: the one active, versioned source of programme and geography taxonomy/rules. It keeps source values and inferred values separate.
- `src/preprocessing/philea_adapter.py`: normalizes all cached Philea records, assigns stable negative local IDs, maps organization types, records provenance, and performs conservative cross-source deduplication.
- `src/data/db_loader.py`: owns schema version 4, validates tables/version, loads JSONL strictly into a staging database, and publishes it atomically only after validation.
- `src/pipelines/run_pipeline.py`: orchestrates collection, consolidation, enrichment, reports, and database publication.
- `src/bff/`: FastAPI entry point, demo cookie authentication, repositories, organization/grant endpoints, pipeline controls, proxy, and optional news summary.
- `src/scoring/engine.py` plus `config/scoring.example.json`: deterministic, configurable, explainable target-profile relevance scoring.
- `frontend/src/App.tsx`: React presentation UI, filters, details, grant table, map, Sankey, provenance labels, enrichment evidence, and score explanation.
- `src/tests/`: unit, regression, database-stability, API, transaction, enrichment, Philea, and scoring tests.

## Data sources and provenance

The checked-in source caches currently contain 62 Charity Commission records, 57 360Giving publisher/organization records containing 3,096 grants, and 299 Philea member records. Consolidation creates 65 UK-side organization rows alongside the Philea records.

The current regenerated database contains:

- 364 organizations: 65 primarily from the Charity Commission/360Giving and 299 from Philea.
- 3,096 grants, all explicitly stored as GBP and sourced from 360Giving.
- GBP 961,181,726.30 in stored grant values. `amount_eur` is empty because there is no approved exchange-rate/date policy; currencies are not silently converted or combined.
- 299 Philea organizations marked `organization_level_only`; no grant is attached to a synthetic Philea ID.

Raw source records remain traceable through source name, source record ID, source URL where supplied, ingestion timestamp, and retained raw payload fields. Organization records also retain source-record arrays and deduplication status/candidates. Derived data is stored separately:

- `programme_areas_source`: normalized classifications present in source data.
- `programme_areas_inferred`: deterministic regex-derived classifications.
- `geographic_focus_source`: normalized source-described operating/funding geographies.
- `geographic_focus_inferred`: deterministic text-derived focus geographies.
- `headquarters_country` / `headquarters_region`: where the organization is based; never treated as beneficiary geography.
- `beneficiary_geography_normalized`: grant-recipient/project geography used by the map.

`src/data/charities.db`, generated JSONL, and generated coverage reports are ignored build artifacts. The checked-in raw caches are the reproducible presentation inputs.

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

The current map has normalized beneficiary geography for 1,699 of 3,096 grants (54.88%), above the default 30% display threshold. The remaining 1,397 are reported as unknown rather than assigned to donor headquarters.

## Grant overview aggregations

The Overview charts use cached 360Giving grant rows only. `Monthly Grant Awards` groups `grants.amount` by the calendar month of `grants.date`, explicitly interpreted as the award date. The default 24-month period is anchored to the latest available award month rather than the current month. Months without an observed source record are returned as unknown coverage with null values, not as confirmed zero activity.

`Grant Allocation by Programme Area` first normalizes `programme_area_source`; only a valid taxonomy match takes precedence. Otherwise it accepts `programme_area_inferred` categories whose stored score meets the existing 0.55 enrichment review threshold. Everything else remains visible as `Unclassified`. A multi-category grant is split in minor currency units across its categories, with deterministic remainder assignment, so allocated amounts reconcile exactly to qualifying source amounts. Negative source values are treated as possible corrections/reversals, excluded from these presentation sums, and reported in exclusion metadata. Numeric zero values remain included. No implicit currency conversion or upper-value rejection is applied.

The current GBP cache yields 66.82% accepted programme classification coverage (2,068 of 3,095 qualifying non-negative grants); 1,027 remain Unclassified. Philea organization records are not included because the cache contains no Philea grant-level transactions.

## Explainable relevance score

No client-approved score definition, notes, target variable, or weights were found. The included `example-relevance-v1` configuration is therefore explicitly `experimental` and measures only relevance to a selected target profile. It is not a probability, recommendation, financial forecast, or prediction of donation behavior.

Default example weights are thematic fit 0.35, geographic fit 0.25, funding-capacity fit 0.15, historical grant-size fit 0.15, and organization-type fit 0.10. Component calculations expose their inputs, method, confidence, and missing reason. Overall confidence and data completeness are returned separately from the score. Missing components are excluded by renormalizing the available weights; they are never silently scored as zero. Financial/grant comparisons occur only when currencies match.

To use a reviewed configuration, copy the example, preserve the validated schema, set `configuration_status` appropriately, and point `SCORE_CONFIG_PATH` at it. Approval should include the business target, weights, thresholds, missing-data policy, and evaluation criteria.

## Local setup

Prerequisites: Python 3.12, Node.js 22, and npm.

```bash
git checkout 86-implement-ui-for-final-presentation
python3.12 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
cp .env.example .env

cd frontend
npm ci
cp .env.example .env
cd ..
```

The root `.env` is ignored by Git and loaded by the BFF configuration. `frontend/.env` is also ignored, but every `VITE_*` value is embedded in browser code and must not contain a real secret. The demo username/password are visible client configuration and are not production authentication.

Relevant backend variables are `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `BFF_ADMIN_USER`, `BFF_ADMIN_PASSWORD`, `CORE_API_URL`, `DB_PATH`, `DATA_PATH`, `SCORE_CONFIG_PATH`, and the optional `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `CLAUDE_MODEL`. Frontend variables are `VITE_API_BASE_URL`, `VITE_BFF_USERNAME`, and `VITE_BFF_PASSWORD`; the two demo credentials must match the BFF values.

### Build the presentation database

Use the cached-source consolidation path for a deterministic local presentation build. It does not call the source APIs and skips optional website contact crawling:

```bash
PYTHONPATH=src ./venv/bin/python src/pipelines/run_pipeline.py \
  --source consolidate \
  --skip-contact-crawler
```

This writes ignored JSONL/reports under `src/data/preprocessed/` and atomically replaces `src/data/charities.db` only after schema and minimum-data validation. A failed staging load leaves the active database untouched. `full_run`, `refresh_charities`, and `refresh_grants` invoke external sources and should be used only intentionally, with conservative limits.

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
| `GET /api/charities/stats` | Dataset KPIs, source counts, and organization-type counts |
| `GET /api/charities/{id}` | Organization detail, provenance, and enrichment evidence |
| `GET /api/charities/{id}/grants?role=all|funder|recipient` | Observed transactions and coverage status |
| `GET /api/charities/grants/summary` | Currency-separated network totals and rankings |
| `GET /api/charities/grants/map` | Beneficiary-geography aggregation and coverage metadata |
| `GET /api/charities/grants/trends?currency=GBP&months=24` | Award-date monthly grant totals with unknown-coverage months and exclusions |
| `GET /api/charities/grants/themes?currency=GBP` | Minor-unit-preserving programme allocations and classification coverage |
| `GET /api/charities/{id}/sankey` | Currency-safe observed donor-to-recipient flow |
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
npm run build
```

GitHub Actions runs separate Python 3.12 backend and Node.js 22 frontend jobs. The backend job installs requirements, runs the blocking flake8 checks, compiles Python, and enforces 70% BFF coverage. The frontend job performs `npm ci` and the TypeScript/Vite production build.

## Presentation flow

1. Start on Overview and show the beneficiary map, Monthly Grant Awards, and Grant Allocation by Programme Area; point out the cached-source, currency, temporal-coverage, and classification-coverage labels.
2. Open Directory and demonstrate search, programme, headquarters, funding-region, annual-giving, and average-grant filters.
3. Open Charity Projects (`326568`, whose source grant records use the funder name Comic Relief) to show Charity Commission identity/provenance, source versus inferred classifications, evidence/review state, observed 360Giving grants, and the donor-to-recipient Sankey.
4. Show the beneficiary map and its 54.88% known-geography disclosure; explain why headquarters is not substituted for missing transaction geography.
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
- The production frontend build currently emits a bundle-size warning above Vite's 500 kB advisory threshold; code splitting is future work.
- Live pipeline modes depend on upstream availability and can take time. Use cached consolidation for reproducibility.
- Hinchilla code and historical preprocessed artifacts remain in the repository for compatibility/reference, but Hinchilla is not part of the active presentation rebuild.
