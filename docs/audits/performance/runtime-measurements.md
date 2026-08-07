# Runtime Measurements

Measured locally on 2026-07-28 on Apple Silicon/macOS, using the active FastAPI development process and the full 2.10 GB SQLite database. These are diagnostic samples, not a production load test. Browser sessions were fresh headless Chrome profiles unless labelled warm.

## Warm sequential HTTP samples

| Endpoint | Samples | Status | Median | Approx. sample p95/max | Notes |
|---|---:|---:|---:|---:|---|
| `GET /health` | 10 | 200 | 1.18 ms | 1.74 ms | Process-only health. |
| `GET /api/charities/grants/overview` | 10 | 200 | 3.24 ms | 4.28 ms | Warm application cache/materialization path. |
| `GET /api/charities/directory/organizations?limit=10` | 10 | 200 | 2.14 ms | 9.10 ms | First sample was slowest. |
| `GET /api/charities?limit=20` | 5 | 200 | 4.06 ms | 17.38 ms | First sample was slowest. |
| `GET /api/charities/grants/funders?beneficiary_country=DE&limit=20` | 5 | 200 | 1.071 s | 2.009 s | Remains expensive after warm-up. |

The p95 column is the maximum for the small 5/10-sample set and must not be treated as a statistically robust production p95.

## Cold browser and heavy query evidence

| Scenario | Observed duration / state | Evidence |
|---|---|---|
| Fresh overview session | Shared `stats`, geography and overview calls about 27–35 s | `screenshots/overview-1440x900.png`, `overview-warm-1440x900.png`; runtime request logs. |
| Fully loaded overview | Complete at the 45 s screenshot budget | `screenshots/overview-loaded-1440x900.png`. |
| Fresh donor directory | Still displayed “Loading observed donors” and zero counts at 12 s | `screenshots/donor-directory-1440x900.png`. |
| Legacy map, GBP with connections | 67.5668 s | Uvicorn request-duration log. |
| Parallel requests during heavy map query | Trends, themes, charities, registry and funders each waited about 55.435 s | Concurrent curl/request log. This demonstrates event-loop blocking, not only DB contention. |
| Mobile overview, 390×844 | Still loading and horizontally cropped | `screenshots/overview-mobile-390x844.png`. |

## Repository/database benchmark

Executed against `/private/tmp/fip-audit-staging-20260728.db`, 397,469 registry rows:

| Query | Latency | Result / plan |
|---|---:|---|
| FTS5 name search `foundation`, limit 50 | 651.171 ms | 50 rows; strategy `fts5`. |
| Exact charity number `200027`, limit 50 | 1.997 ms | 3 rows; `idx_registry_charity_number`. |
| Registered with income >= 100,000, sorted income desc, limit 50 | 8.890 ms | 50 rows; covering `idx_registry_status_income`. |

SQLite `quick_check` took 15.15 s and full `integrity_check` 39.08 s on the 2.10 GB staging copy.

## Frontend artifact

- Vite build: 2,347 modules, about 755 ms.
- Main JavaScript: 1,963.84 KB, 612.12 KB gzip; Vite emitted its >500 KB chunk warning.
- CSS: 116.05 KB, 18.69 KB gzip.
- Lazy chunks observed: donor directory about 38.38 KB, registry about 19.42 KB.
- Lint completed with five `react-hooks/exhaustive-deps` warnings.

## Docker artifact

- Build context: 8.81 GB; transfer about 80.1 s.
- Image: 9,368,380,422 bytes.
- Largest layer: `COPY src/`, about 8.81 GB.
- Embedded DB files: 2.10 GB active DB and 1.70 GB processed registry SQLite, plus other source/processed artifacts.
- Tested architecture: Linux ARM64 only.
- Default-image startup returned health 200 after application initialization; an immediate probe during startup got an empty response.

## Interpretation and required budgets

Warm cache numbers conceal an unacceptable cold/concurrent path. FastAPI handlers are declared async but execute synchronous SQLite work, so a heavy aggregation blocks unrelated calls on the event loop. Before AWS staging exit:

1. define representative cold/warm datasets and concurrency;
2. move blocking DB work off the event loop or use an async PostgreSQL data layer;
3. precompute/version overview, map, ranking and trend facts;
4. set a primary-view target (recommended p95 <= 3 s, cached API p95 <= 500 ms where feasible);
5. test p50/p95/p99, saturation, timeouts, cache invalidation and one-heavy-query isolation in CI/staging.
