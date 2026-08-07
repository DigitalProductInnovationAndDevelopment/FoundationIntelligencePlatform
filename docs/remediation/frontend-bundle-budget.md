# Frontend bundle budget

Phase 7 keeps the map as the first major dashboard section while preventing its
geometry and charting dependencies from blocking the initial application shell.
The production build enforces these compressed limits locally:

| Asset scope | gzip budget |
| --- | ---: |
| Initial JavaScript, including module preloads | 120 KiB |
| Initial CSS | 25 KiB |
| Any deferred JavaScript chunk | 425 KiB |

`npm run build` runs `scripts/check-bundle-budget.mjs` after Vite. The checker
reads the generated `dist/index.html`, measures the referenced initial assets,
measures every lazy JavaScript chunk, and exits non-zero when a budget is
exceeded. The larger map geometry is intentionally deferred behind React
Suspense; charts and directory workspaces are separate lazy chunks.

No CDN fonts, scripts, or stylesheets are allowed. All production assets must be
resolved from the pinned npm lockfile and emitted into `frontend/dist/`.
