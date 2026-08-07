# Phase 6 Performance and Concurrency Evidence

Date: 2026-07-29
Environment: local PostgreSQL 16.14 on loopback, Alembic `0004_versioned_analytics`, dataset `sqlite-v7-8fc0cce61c81-r2`

## Result

Gate 6 passes. The structured companion is `phase6-performance-report.json`.

| Journey | Target p95 | Repository p95 | Production-mode API p95 |
|---|---:|---:|---:|
| Health | <100 ms | 3.23 ms | 3.70 ms |
| Organization list | <500 ms | 233.03 ms | 245.73 ms |
| Default map | <2,000 ms | 3.34 ms | 4.95 ms |
| Lazy map relationships | <5,000 ms | 6.05 ms | 6.04 ms |
| Primary dashboard | <3,000 ms cold | 255.31 ms cold | 454.20 ms single cold run |
| Registry exact | <300 ms | 12.43 ms | 18.01 ms |
| Registry text | <1,000 ms | 77.03 ms | 83.93 ms |

The repository cold-dashboard distribution uses 20 independent application-cache clears: p50 157.56 ms, p95 255.31 ms and p99 539.80 ms. Five authenticated production-mode dashboards complete in 472.80 ms at 10.575 dashboards/s with zero errors. API cache hit ratio is 0.8333; the five-connection pool finishes with zero checked-out connections and zero overflow.

## Query and isolation evidence

`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` proves that the default map reads `analytics_country_aggregates`, exact current-name registry lookup reads `ix_registry_current_normalized_name`, and ranked text search reads a stored-vector or normalized-name GIN/trigram index. Timeout/cancellation aborts `pg_sleep(2)` within the application deadline, rolls back the session and then successfully executes `SELECT 1`. A simultaneous one-second heavy query does not push five health checks beyond the 100 ms gate.

The active materialization control row reports 204,220 rows across nine aggregate tables. Monthly and yearly periods are both present. Map connections are independent and capped at 250; funder-recipient relationships are pre-ranked to 50 and detail returns at most 25.

## Scope and safety

The initial one-sample baseline was intentionally diagnostic, not statistically comparable: map 2.00 s, network summary 4.35 s, dashboard 8.94 s, exact registry 15.04 s and text registry 16.84 s. Final claims use the bounded repeatable harnesses in `scripts/benchmark_postgres.py` and `scripts/load_test_api.py`.

No dependency/image download, AWS access, paid/live API, scraper/model call, upload or push occurred. The protected SQLite and aggregate audit checksums remain at their approved baselines.

The normal regression suite passes 311 tests, skips nine explicit live-environment tests and passes eight route subtests. The final backend rebuild succeeded with `--pull=false`; every pinned base/dependency layer was cached and no download occurred. The local arm64 image is 354,456,439 bytes, runs as `10001:10001` and has ID `sha256:cf71388a8fc83cdc32632ea2cf8ea9b7b27d4d68b164f848cd6e97b49905af8a`.
