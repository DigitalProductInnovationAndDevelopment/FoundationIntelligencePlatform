# Foundation Intelligence Platform — frontend

React 19 + TypeScript + Vite single-page application for the Foundation Intelligence
Platform. Roughly 8,500 lines across `src/`.

This file is the frontend reference. For how the frontend fits into the wider system, see
[`docs/02-architecture.md`](../docs/02-architecture.md); for the filter and aggregation
rules it renders, see [`docs/04-domain-rules.md`](../docs/04-domain-rules.md).

## Setup

```bash
npm ci --ignore-scripts --no-audit --no-fund
cp .env.example .env
npm run dev -- --host 127.0.0.1     # http://127.0.0.1:5173
```

The backend must be running on `http://127.0.0.1:8000` — see the
[root README](../README.md).

**Use `127.0.0.1` consistently.** Mixing it with `localhost` gives the browser two
different origins and the session cookie will not be sent.

## Authentication

This application **does not log in**. Every request sends `credentials: "include"` and
assumes a session cookie already exists. Establish one against the backend first:

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<DEV_AUTH_USERNAME>","password":"<DEV_AUTH_PASSWORD>"}'
```

In staging and production, identity comes from the deployment OIDC flow.

**Never put a credential in a `VITE_*` variable.** They are compiled into the JavaScript
bundle and are public.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Vite dev server with HMR |
| `npm run build` | `tsc -b`, Vite production build, then the bundle budget gate |
| `npm run lint` | oxlint |
| `npm test` | Type-check and run the Node test runner suites |
| `npm run test:e2e` | Build, then Playwright |
| `npm run test:runtime` | Build, then the rendered layout check |
| `npm run preview` | Serve the production build locally |

## Layout

```
src/
  main.tsx              mount point
  App.tsx               application state, view routing, most data fetching
  components/           six lazy-loaded views (see below)
  lib/
    grantScope.ts       canonical grant filter scope — change filter semantics here
    numericRange.ts     numeric range validation
    http.ts             mutation headers (Idempotency-Key, X-Action-Reason)
tests/                  unit suites
e2e/                    Playwright specs
scripts/                bundle budget and runtime layout gates
```

| Component | Purpose |
|---|---|
| `OverviewDashboard` | Overview, trends, programme allocation, drill-down |
| `DonorDirectoryPage` | Donor list with lazy right-side detail |
| `GrantWorldMap` | Beneficiary-country map and country explorer |
| `RegistryDirectory` | Cursor-paginated Charity Commission registry search |
| `DataCharts` | `GrantAwardsChart`, `ProgrammeAllocationChart`, `FinancialHistoryChart` |
| `AppHeader` | Navigation, source selection, favourites |

All six are lazy-loaded from `App.tsx`. Keep it that way — the bundle budget depends on it.

## Bundle budgets

Enforced by `npm run build` via `scripts/check-bundle-budget.mjs`:

| Budget | Limit (gzip) |
|---|---|
| Initial JavaScript | 120 KiB |
| Initial CSS | 25 KiB |
| Any deferred chunk | 425 KiB |

## Conventions

- Fetch with `credentials: "include"` and an `AbortController` signal for anything that
  can be superseded.
- Mutations use `mutationHeaders(reason)` from `lib/http.ts` — the API requires an
  `Idempotency-Key`.
- Take grant filters from `lib/grantScope.ts` rather than inventing a shape.
- Model loading state **per section** (`idle | loading | ready | empty | partial |
  error`), not globally. Distinguish *empty* from *error* from *unknown* — never render
  absent data as zero.

## Environment

| Variable | Default | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | current hostname on port 8000 | Override only when the API runs elsewhere |
| `VITE_LEGACY_OVERVIEW` | unset | Renders the legacy overview layout when set to exactly `true` |
