# Donor Directory responsive-validation report

Date: 2026-07-25

## Acceptance status

**Incomplete — not claimed as passed.**

The implementation includes responsive rules and accessibility behavior, but this environment did not permit the required real-browser run. Chrome, Edge, and Firefox applications are installed locally; however, the sandbox rejected binding the temporary local BFF port and the required escalation was unavailable under the current tool-approval limit. No screenshots were captured.

Per the removal gate, the legacy donor view remains lazy-loaded and reachable from Organization Research.

## Implemented responsive behavior

### Shared header

- One `AppHeader` is used for primary and secondary views.
- Desktop/laptop retain text labels and fixed action order.
- At narrow widths actions become icon controls while accessible names remain in the DOM.
- Reset remains present and uses the native disabled attribute.
- Data sources uses the same persistent disclosure across pages.
- Focus-visible styling is retained.

### Donor Directory

- Desktop uses compact semantic list rows and a sticky right detail panel.
- Tablet switches detail to a full-width sheet below the global header.
- Mobile uses the full screen below the header; donor rows become stacked rather than a desktop-width table.
- Search/status controls are keyboard-operable.
- Selected rows use text/status plus colour and `aria-pressed`.
- Detail selection, search, status, sort, page, and scope are URL state.
- Escape and browser Back close details.
- The filter drawer traps focus, supports Escape, and returns focus to the global filter action.
- Loading and error states use live/status semantics.

### Secondary research

- Organization Research and registry search retain their data and existing responsive behavior.
- Registry result cards remain semantic buttons.
- Registry and legacy donor modules are lazy-loaded.

## Required viewport matrix

The following real-browser checks remain outstanding:

| Viewport | Source inspection | Real browser | Screenshot | Acceptance |
|---|---|---|---|---|
| 1440 × 900 | completed | not run | none | incomplete |
| 1280 × 800 | completed | not run | none | incomplete |
| 1024 × 768 | completed | not run | none | incomplete |
| 834 × 1194 | completed | not run | none | incomplete |
| 390 × 844 | completed | not run | none | incomplete |

## Manual/browser checklist

At every viewport, verify:

- global title and Filters → Reset → Data sources order;
- no header or page horizontal overflow;
- disabled Reset does not shift layout;
- Data sources disclosure remains on screen and all choices are reachable;
- navigation drawer open/close and focus behavior;
- Funding Landscape map height and country keyboard behavior;
- Donor Directory search, status pills, row truncation, pagination, and empty/error/loading states;
- desktop list width while detail is open;
- tablet/mobile sheet dimensions and scrolling;
- selected row restoration and scroll position after closing detail;
- filter-drawer focus trap and trigger focus restoration;
- external evidence labels and explicit-click-only opening;
- Organization Research filters and profile detail;
- registry filters, cards, cursor loading, and detail close behavior;
- keyboard focus visibility throughout.

## Automated validation completed

- TypeScript production build: passed.
- Oxlint: passed with five pre-existing `App.tsx` hook-dependency warnings; no warnings remain in newly added Donor Directory, GrantScope, AppHeader, or modified Overview code.
- GrantScope Node tests: 5 passed.
- Focused backend donor/overview tests: 20 passed.

## Remaining corrective gate

Do not remove the legacy donor route or claim responsive acceptance until a real browser run at all five target sizes produces durable screenshots (or an equivalent recorded artifact) and verifies overflow, focus lifecycle, loading/error states, and Back behavior.
