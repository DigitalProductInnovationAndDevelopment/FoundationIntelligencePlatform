# Phase 13 transition evidence

Status: `PASS` locally; AWS and production cutover `NOT TESTED`/unexecuted.

PostgreSQL is the default authoritative runtime. SQLite migration-source mode is
rejected in staging/production, and temporary shadow mode requires a separate
snapshot. Shadow reads run after the primary response, are queue/size/time
bounded and record only privacy-safe difference fingerprints.

The executed local source/target gate compared 18 semantic projections across
counts, totals, coverage, filters, map relationships, trends, donor/recipient
rankings, registry pagination/search, profiles, grant lists, Sankey, score
components and currency statuses. Result: `0` differences against active dataset
`sqlite-v7-8fc0cce61c81-r2` and SQLite SHA-256 `8fc0cce…7651`.

The local rollback switched to approved prior dataset
`sqlite-v7-8fc0cce61c81` and restored `sqlite-v7-8fc0cce61c81-r2`, with equal
counts and an active materialization. No bidirectional replay occurred.

The full logical restore created a 247,509,368-byte archive with SHA-256
`2c571954…ae87`, restored it into an isolated temporary database and matched
schema `0006_governance_retention`, active dataset, counts, EUR total and
materialization. The temporary database and archive were removed.

Three failed diagnostics remain explicit in the command log: strict date
binding, exact numeric representation and the stale migration-schema constant.
All were fixed and repeated successfully; none was hidden or described as a
successful first attempt.

No AWS resource, production traffic, external API, live news provider or Git
remote was touched.

Final regression evidence is 352 normal backend passes, 13 skips and eight
subtests at 72.73% coverage across all `src` modules, passing the exact 70% CI
gate; 55 combined PostgreSQL integration passes;
17 mypy files; 13 frontend unit passes; and 8 Playwright/axe passes with four
intentional skips. The first container E2E attempt used a stale local frontend
image and failed eight tests. After rebuilding the current pinned image, the
same command passed; both attempts are retained in the command log.

The Phase-13 backend image is `sha256:101071…338ee`, 354,658,326 bytes and
`10001:10001`. The frontend image is `sha256:8f6658…9ad1a`, 56,241,904 bytes
and `101:101`. Stack liveness/readiness and backend restart readiness pass.
The frontend build and final deterministic install used only the explicitly
approved `registry.npmjs.org` via `npm ci`; no arbitrary download occurred.

The final acceptance replay also exposed and fixed a workflow-only invocation
error: calling the installed `pytest` entry point with `PYTHONPATH=src` omitted
the repository root and prevented four `scripts.*` imports. All workflow test
steps now use `python -m pytest`; the offline workflow validator rejects a
regression to the bare entry point. The corrected exact CI coverage command
passes with the results above.
