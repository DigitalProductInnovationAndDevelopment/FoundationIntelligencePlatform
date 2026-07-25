# Donor Directory performance report

Date: 2026-07-25

## Environment

- Hardware: MacBook Air (Apple M1, 8 cores, 8 GB RAM, arm64)
- OS/kernel: Darwin 25.4.0 (local macOS environment)
- Python: 3.12.3
- Node: 22.20.0
- npm: 10.9.3
- SQLite: 3.51.0
- Database: `src/data/charities.db`
- Starting database size: 1,331,175,424 bytes
- Final database size: 1,389,834,240 bytes
- Grants: 200,000
- Enriched profiles: 364
- Current registry rows: 397,469
- Derived facts: 71,403 rows, 71,286 distinct grants, 133 source-funder identities, 113 countries
- Data revision: `[200000,"v360G-virginmoneyfdn-3050","2026-07-24T15:29:23.308211Z","2026-07-10",199579,15652485145.3]`
- Code baseline: `9c46f275dcf5140d97bc45d56099d036bf602d00`

## Timing definitions

- **Repository** means a direct in-process call to `SQLiteCharityRepository`; JSON payload byte counts use local serialization where recorded.
- **HTTP** means a completed request through a listening BFF socket.
- **Browser** means interaction/route start to usable rendered content in a real browser.

Repository measurements do not prove HTTP or browser targets.

## Before and after

The pre-change baseline used a fresh copied database with the persisted Overview payload cache cleared. The old list path loaded broad grant rows and performed source identity grouping in Python. Final timings use the reproducible derived facts and current local database.

| Operation | Before repository | Final repository | State / notes |
|---|---:|---:|---|
| GB donor list, 25 rows | 15.754 s | 0.772 s first call; 0.296 s repeat | 131 funders / 68,430 grants; final list is SQL filtered/grouped/sorted/paged |
| US donor list | 0.088 s | 0.234 s | 22 funders / 585 grants; richer typed response adds fixed joins; still bounded |
| NG donor list | 0.096 s | 0.022 s | Post-index test-copy measurement; 11 funders / 308 grants |
| GB linked large-donor detail | 16.293 s | 0.324 s summary | 1,965 grants; summary returns no grant sample/evidence |
| Same full lazy detail | 16.293 s | 0.071 s warm; 0.620 s isolated first full call | 50 grant samples, 50 ranked recipients, 40 evidence links capped |
| GB search + observed-only status | not available | 0.292 s | Backend search/status, 36 matching funders for `trust` in the measured scope |

The GB list is approximately 20× faster on the first measured final repository call and approximately 53× faster on the repeated call. The large detail no longer recomputes all GB funders and is approximately 26× faster even using the isolated 0.620-second full measurement.

## Query plan

SQLite confirms the two critical accesses use the new indexes:

```text
SEARCH grant_source_funder_facts USING COVERING INDEX
  idx_source_funder_facts_country_key (country_code=? AND source_namespace=?)

SEARCH fact USING INDEX idx_source_funder_facts_key_country
  (source_funder_key=? AND country_code=?)
```

The list response reads no `raw_grant_data`. Only a selected full detail reads up to 50 raw stored grant records for typed evidence extraction.

## Build comparison

| Asset | Baseline | Final |
|---|---:|---:|
| Main JS | 1,952.10 kB / 607.87 kB gzip | 1,942.73 kB / 604.89 kB gzip |
| CSS | 63.34 kB / 11.19 kB gzip | 79.29 kB / 13.43 kB gzip |
| Lazy registry JS | in main bundle | 13.64 kB / 3.72 kB gzip |
| Lazy legacy donor JS | in main bundle | 15.25 kB / 4.25 kB gzip |

The primary bundle remains above Vite’s 500 kB warning threshold, but is 9.37 kB smaller (2.98 kB smaller gzip) than baseline despite including the new primary directory/detail. The registry and legacy donor page are split out, and the clipped legacy source-funder Sankey was replaced by an accessible ranked list. Profile detail charts and other legacy Recharts code still dominate the main bundle and are a documented remaining optimization.

## Index-build and cache cost

The derived facts are rebuilt only when the schema or grant data revision is invalidated. A test run that combined first-time schema/fact creation and a cold full Overview aggregation took 60.420 seconds. That is a maintenance/cold-aggregation cost, not a normal Donor Directory request. It increased this local database by 58,658,816 bytes (about 4.4%).

The BFF no longer blocks application readiness on the full Overview warmup. Supported loaders and ECB conversion backfills invalidate the revision explicitly. The next request performs one reproducible rebuild rather than using an indefinitely stale materialization.

## HTTP and browser status

Socket-level HTTP measurement was attempted with an isolated BFF on `127.0.0.1:8011`. The sandbox rejected local port binding. The escalation needed to bind the test process was unavailable because of the current tool-approval limit. Therefore:

- no socket-level HTTP timing is claimed;
- no cold/warm HTTP target is claimed as passed;
- TestClient results are treated as tests, not production HTTP performance;
- no browser transition target is claimed as passed.

## Remaining risks

- First rebuild after a new data snapshot remains expensive and should be run as a deployment/load step for larger datasets.
- The wide legacy Overview aggregation is still slow on an uncached new query.
- The main JS bundle remains large.
- HTTP and browser timings require a locally permitted server and real browser run.
