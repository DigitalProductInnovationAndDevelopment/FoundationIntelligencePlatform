# Donor Directory redesign implementation

Date: 2026-07-25  
Branch: `9-fr-09-display-ranked-list-of-main-donors`  
Starting commit: `9c46f275dcf5140d97bc45d56099d036bf602d00`

## Outcome

The application now distinguishes three record populations instead of presenting them as one organization directory:

1. **Funding Landscape** — observed grant activity grouped by beneficiary geography.
2. **Donor Directory** — source-namespaced funder identities observed in the current grant scope.
3. **Organization Research / Advanced Charity Commission Search** — secondary enriched-profile and official-registry research.

The default Donor Directory never inserts an enriched profile or registry row merely because its name or registered location resembles a source funder. A profile appears only as an explicit zero/one/many linkage attached to the same observed source identity.

## Shared application shell

`frontend/src/components/AppHeader.tsx` is the single global header implementation. It always renders:

`Foundation Intelligence Platform` → `Filters` → `Reset` → `Data sources`

The active page supplies the filter count and contextual actions:

- Funding Landscape opens and resets grant-scope filters.
- Donor Directory opens and resets donor and grant-scope filters.
- Organization Research targets its existing profile filters.
- Advanced Charity Commission Search targets and resets registry filters.
- Pipeline Monitor keeps contextual actions visible but disabled.

Data-source selection is global, synchronized, and persisted in `grant_sources`. An explicit empty value means no sources selected; a missing parameter means the page may use its supplied defaults.

## Canonical GrantScope

`frontend/src/lib/grantScope.ts` owns:

- URL parsing and validation;
- normalization and serialization;
- API query construction;
- active chips and filter counts;
- contextual value removal;
- scope equality;
- Donor Directory search/status/sort/page/detail route state.

The existing query names remain compatible:

| Meaning | URL parameter | API parameter |
|---|---|---|
| beneficiary country | `funder_country` | `beneficiary_country` |
| beneficiary terms | `grant_geo` | `beneficiary_geographies` |
| programme areas | `grant_programme` | `programme_areas` |
| date range | `grant_from`, `grant_to` | `date_from`, `date_to` |
| currency | `grant_currency` | `currency` |
| donor / recipient text | `grant_donor`, `grant_recipient` | `donor`, `recipient` |
| source selection | `grant_sources` | `sources` |
| directory search | `donor_search` | `search` |
| profile-link status | `donor_status` | `profile_status` |
| sort / page | `funder_sort`, `funder_page` | `sort`, `page` |
| selected detail | `donor` | path parameter |

The small `view` parameter identifies primary/secondary application state (`donors`, `research`, `registry`, `legacy-donors`, or `pipeline`); Funding Landscape remains the default when it is absent.

Browser Back, Forward, refresh, direct URLs, copied URLs, detail opening/closing, search, sorting, pagination, and source selection use this contract. The pure contract is tested with the Node test runner.

## Backend design

The broad list no longer selects and parses the wide `grants.raw_grant_data` column for an entire country. The idempotent schema migration creates `grant_source_funder_facts`, a reproducible narrow table with one row per grant-country association and these semantics:

- source namespace;
- stable source-funder key;
- source organization ID where present;
- normalized-name fallback where required;
- identity method and display name;
- recipient identity and label;
- award date and original/EUR monetary status;
- multi-country count;
- supported linked profile ID;
- publisher record reference;
- data revision.

The list query filters before aggregation, groups in SQL, sorts aggregated results, and pages in SQL. Programme classifications use `grant_programme_categories`; geography uses the normalized beneficiary indexes. Raw JSON is not read by list requests.

Supported writers delete `grant_overview_index_revision` after changing grant or exchange-rate facts. The next request rebuilds all derived tables transactionally. `load_jsonl_to_db` now also invalidates preserved staging revisions so an atomic reload cannot publish stale or empty derived facts.

The BFF startup no longer awaits a full Overview aggregation. Repository initialization is immediate; persisted caches are reused and expensive aggregation is request-driven. This prevents the health endpoint from appearing crashed during a cold 1.3 GB scan.

## Typed result and linkage status

List responses preserve legacy fields while adding:

- `kind: source_funder`;
- typed `identity`;
- `evidence_sources`;
- `observed_activity`;
- `amount_policy`;
- explicit `profile_link` with `none`, `single`, or `multiple` status;
- accepted registry link information only through a single supported profile.

Status filters are backend operations:

- `all` — every observed source identity in scope;
- `linked` — exactly one supported profile link;
- `observed_only` — zero or multiple supported profile links.

Multiple candidates are never auto-selected and expose no profile fields in the donor row.

## Shared donor detail

The primary Donor Directory opens a right-side detail panel on desktop, a full-width sheet on tablet, and a full-screen sheet on mobile. The list and selected row remain present on desktop. Detail state is stored in the URL and Escape/Back close it.

Initial requests use `detail_level=summary` and return only summary facts plus a relationship availability summary. Opening grant activity or source evidence makes the explicit `detail_level=full` request. The full query is restricted to the selected source-funder key and country; it does not rebuild the country directory.

The default relationship representation is a ranked, keyboard-readable recipient list with amount, grant count, and latest date. The previously clipped source-funder Sankey in the legacy overlay was also replaced by a ranked textual equivalent. A Sankey is optional, not loaded by the primary donor workflow, and may be reintroduced later only as a lazy readable enhancement with the list retained.

Observed-only details state that no linked profile exists. Linked details show the profile as a separate section and route to Organization Research. Observed, enriched-profile, and registry facts are not merged.

## Evidence links

The BFF parses already stored values into these typed kinds:

- `360giving_funder_record`;
- `360giving_recipient_record`;
- `observed_funder_website`;
- `observed_recipient_website`;
- `publisher_grant_data`;
- `profile_website`;
- `profile_source`.

Only syntactically valid HTTP(S) URLs without embedded credentials are returned. The BFF does not fetch, proxy, preflight, follow, or validate external destinations. The frontend opens a link only after an explicit click with `target="_blank"` and `rel="noopener noreferrer"`. Source organization identifiers are visible and copyable in methodology.

## Navigation and legacy access

Primary navigation is now:

1. Funding Landscape
2. Donor Directory
3. Pipeline Monitor

Organization Research and Advanced Charity Commission Search are secondary actions inside the Donor Directory. Their data and filters remain intact. The former Main Donors component is lazy-loaded behind **Legacy donor view** because real-browser responsive acceptance could not be completed in this environment.

## Phase checkpoints

### Phase 1 — contract and performance foundation

- Shared header, GrantScope, derived source-funder facts, optimized list/detail, source invalidation, tests, and measurements implemented.
- Backend focused suite: 20 passed.
- Frontend contract tests: 5 passed.
- Safe to continue: yes.

### Phase 2 — directory and map hand-off

- Compact observed donor rows, backend search/status/sort/page, active chips, filter drawer, reset, and History API state implemented.
- Source selection round trip covered by tests.
- Legacy data remains accessible.
- Safe to continue: yes.

### Phase 3 — donor detail and evidence

- Responsive detail shell, summary-first fetch, lazy full activity, ranked recipients, conditional linkage, and typed evidence implemented.
- No fabricated profile data for observed-only/multiple-link results is covered by backend tests.
- Safe to continue: yes.

### Phase 4 — secondary research and validation

- Navigation hierarchy, shared header, lazy registry/legacy routes, and secondary page introductions implemented.
- Real-browser validation and socket-level HTTP measurement remain incomplete because local process binding/escalation was unavailable.
- Legacy view retained as required by the removal gate.
- Final removal gate: **not passed**.

## Rollback

The source `grants`, `charities`, registry, and linkage tables are unchanged. To roll back the derived structure:

1. deploy the prior application revision;
2. drop only `grant_source_funder_facts` and its indexes if desired;
3. remove `grant_overview_schema_version` and `grant_overview_index_revision` metadata keys;
4. allow the prior code to rebuild its existing beneficiary/programme indexes.

Dropping the derived table loses no source record. Do not delete or rewrite unresolved grant IDs to perform a rollback.
