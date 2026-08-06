# Frontend reference

React 19 + TypeScript + Vite. Roughly 8,470 lines across `frontend/src/`. Linted with
oxlint, tested with the Node test runner plus Playwright.

## Layout

```
frontend/
  src/
    main.tsx                  10 lines   — mount point
    App.tsx                4,102 lines   — state, routing, most data fetching
    components/
      DonorDirectoryPage.tsx 1,398        — donor directory and detail
      OverviewDashboard.tsx  1,061        — overview, drill-down, filters
      GrantWorldMap.tsx        718        — beneficiary map
      RegistryDirectory.tsx    652        — Charity Commission registry search
      DataCharts.tsx           142        — three chart components
      AppHeader.tsx             92        — header, navigation, favourites
    lib/
      grantScope.ts            251        — canonical grant filter scope
      numericRange.ts           37        — numeric range validation
      http.ts                   10        — mutation headers
  tests/                                  — unit tests (Node test runner)
  e2e/                                    — Playwright specs
  scripts/                                — bundle budget and runtime layout gates
```

## `App.tsx` — how to navigate 4,100 lines

It is long, but it is ordered. Reading top to bottom:

| Lines (approx.) | Section |
|---|---|
| 52–58 | Lazy component imports — all heavy views are code-split |
| 61–64 | `API_BASE`, `SHOW_LEGACY_OVERVIEW`, `DEFAULT_DATA_SOURCES` |
| 66–165 | Core domain types (`Charity`, favourites, news) and storage keys |
| 172–345 | Favourites and news persistence, PDF briefing generation |
| 346–520 | API response interfaces (`KPIStats`, `GrantTrendsResponse`, `SankeyData`, `ScoreResponse`, …) |
| 486–520 | Per-section loading state machine (`ProfileSectionStatus`, `ProfileLoadingState`) |
| 555–620 | Pipeline status types and offline mock constants |
| 620–710 | Markdown rendering and filter step constants |
| 710+ | The `App` component: state, effects, fetchers, view rendering |

`MOCK_STATS` and `MOCK_CHARITIES` back the labelled offline fallback. Grant, map and score
data are never mocked — when the API is unreachable those sections report an error state
instead of showing invented numbers.

The loading model is per-section rather than global: `ProfileLoadingState` tracks
`detail`, `grants`, `relationships`, `score` and `source_record` independently, each with
`idle | loading | ready | empty | partial | error`. That is what lets the UI show a
partially loaded profile honestly instead of blocking on the slowest call.

## Components

| Component | Export | Notes |
|---|---|---|
| `OverviewDashboard` | default | Overview, trends, programme allocation, drill-down. Owns `OverviewFilters` and grant-explorer favourites |
| `DonorDirectoryPage` | default | Server-side searched, sorted, paginated donor list with lazy right-side detail. Exports `FavoriteDonorPayload`, `HeaderContextState` |
| `GrantWorldMap` | default | Beneficiary-country map with country explorer. Exports the `GrantMap*` response types used by `App.tsx` |
| `RegistryDirectory` | default | Cursor-paginated registry search with 300 ms debounce |
| `DataCharts` | named ×3 | `GrantAwardsChart`, `ProgrammeAllocationChart`, `FinancialHistoryChart` |
| `AppHeader` | default | Navigation, source selection, favourites entry |

All six are lazy-loaded from `App.tsx`, which is what keeps the initial bundle inside its
budget while the map and charting dependencies stay heavy.

## `lib/`

- **`grantScope.ts`** — the canonical grant filter scope shared by the overview, map and
  donor directory. `normalizeGrantScope`, `grantScopeFromUrl`, `applyGrantScopeToParams`
  and `grantScopeToApiParams` keep URL state, component state and API parameters in one
  shape. Change filter semantics here, not in individual components.
- **`numericRange.ts`** — `validateOptionalNumericRange` for min/max filter inputs.
- **`http.ts`** — `mutationHeaders(reason, json)` generates the `Idempotency-Key` and
  `X-Action-Reason` headers every mutation requires.

## Data fetching conventions

- Every request sends `credentials: "include"`. **The application never logs in** — it
  assumes a session cookie already exists. See
  [08-running-and-operating.md](08-running-and-operating.md) for how to establish one
  locally.
- `API_BASE` defaults to the current browser hostname on port 8000. Override with
  `VITE_API_BASE_URL` only when the API genuinely runs elsewhere. Mixing `localhost` and
  `127.0.0.1` breaks cookies, because they are different origins to the browser.
- Requests that can be superseded use `AbortController` and pass `signal`.
- Mutations attach `mutationHeaders()`.
- `VITE_*` values are compiled into the bundle and are public. Never put a credential in
  one.

## Build gates

`npm run build` runs `tsc -b`, then `vite build`, then the bundle budget check. The
budgets in `frontend/scripts/check-bundle-budget.mjs` are enforced, not advisory:

| Budget | Limit (gzip) |
|---|---|
| Initial JavaScript | 120 KiB |
| Initial CSS | 25 KiB |
| Any deferred JavaScript chunk | 425 KiB |

`npm run test:runtime` additionally builds and checks rendered layout via
`check-runtime-layout.mjs`.

## Commands

```bash
cd frontend
npm ci --ignore-scripts --no-audit --no-fund   # reproducible install
npm run dev -- --host 127.0.0.1                # dev server on :5173
npm run lint                                   # oxlint
npm test                                       # unit tests
npm run test:e2e                               # build + Playwright
npm run build                                  # typecheck + build + budget gate
```

## Known state

`frontend/README.md` was the unmodified Vite starter template and has been replaced. The
components carry file-header comments describing their responsibility and props; there is
no generated API documentation for the frontend.
