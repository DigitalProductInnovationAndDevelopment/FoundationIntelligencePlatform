# Phase 7 Frontend Evidence

## Result

Gate 7 **passes**. The unit, lint, production build, bundle, no-dependency
runtime and named Playwright/axe gates all complete locally.

## Verified locally

- 13 unit/contract tests pass; Oxlint has zero warnings.
- TypeScript and the Vite production build pass.
- Initial JavaScript is 87.81 KiB gzip (120 KiB budget).
- Initial CSS is 18.59 KiB gzip (25 KiB budget).
- The largest deferred JavaScript chunk is 392.36 KiB gzip (425 KiB budget).
- Headless Chrome passes at 320, 390, 768, 1024, 1440 and 1920 pixels.
- No page overflow, clipped visible control, cropped KPI, unnamed visible
  control, browser console warning/error or runtime exception was observed.
- The map precedes analytics, map controls remain inside the viewport, the
  Overview request runs once, and map connections run once only after the
  interaction that enables them.
- Overview and Registry drawers trap Tab, close with Escape and restore focus.
- Donor and Registry navigation and the Registry empty state are covered at
  320 and 1024 pixels.
- Playwright passes eight journeys and intentionally skips four redundant
  secondary-journey viewport combinations.
- axe reports zero violations across the six Overview widths and the two
  representative Donor/Registry journeys.

The runtime gate uses the already installed Chrome through the DevTools
Protocol, a local Vite Preview process and deterministic in-browser API mocks.
It disables background networking and performs no external request.

## Named-tool supply chain

After explicit user approval, `@playwright/test` and its Playwright runtime were
locked at `1.62.0`; `@axe-core/playwright` and `axe-core` were locked at
`4.12.1`. Resolution and tarball access were restricted to
`registry.npmjs.org`. The lock delta contains no pre-existing dependency
upgrade. `npm ci --ignore-scripts` ran with browser download disabled, and the
suite uses the already installed Chrome 150.

The first axe run found and drove four corrections: active-state contrast,
mobile disclosure naming, valid SVG ARIA and Overview heading order. The
repeated full run has zero axe violations. No AWS, paid/live API, browser
download, upload or push occurred.
