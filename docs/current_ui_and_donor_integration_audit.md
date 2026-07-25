# Current UI and donor-integration audit

**Audit date:** 24 July 2026  
**Branch / commit audited:** \`9-fr-09-display-ranked-list-of-main-donors\` / \`9c46f275dcf5140d97bc45d56099d036bf602d00\`  
**Scope:** current-state audit only. No application route, component, API, schema, or dataset was changed for this report.

## 1. Executive Summary

The application currently contains three different entity directories under one visual area:

1. **Main Donors** is an observed-grant view. A row is a run-time grouping of 360Giving grant records by source-funder identity, not necessarily an organisation profile.
2. **Organization Profiles** is an enriched-directory view. A row is one of 364 \`charities\` profile records, mostly Philea records; it may have no observed grant relationship.
3. **Full Charity Commission Register** is an official-registry search. A row is one of 397,469 current Charity Commission records, not automatically a donor.

This distinction is technically real and must survive a UI simplification. The desired two-screen journey is feasible if the new Donor Directory is based on observed source funders, and profile/registry data is attached only as explicitly evidenced enrichment. It is *not* safe to merge all three row types into one undifferentiated “organization” object.

The map hand-off to Main Donors is already substantially implemented: selecting a beneficiary country can open a filtered observed-funder list and carries most grant scope in the query string. Its biggest state gap is that the global data-source selection is not reconstructed by the Overview when returning to the map. The current directory pages are separate modes inside a single React SPA rather than stable, independently routed pages.

The primary user-facing slow path is Main Donors for a large country such as GB: it currently takes about 13.7 seconds in an in-process repository measurement because the backend fetches and parses many grant rows, then groups them in Python. The world-map overview is cache-backed and fast after warming, but its first computation is approximately 3.3 seconds and a cold yearly trend request approximately 6.7 seconds. The profile list and registry page are comparatively fast in the measured local database.

The existing “Observed Funding Flows” renderer has real data for linked profiles such as Oxfam, but its Sankey presentation is visually unreliable for many-to-one relationships: labels clip at the left edge and links converge into an unreadable point. This is a visualisation problem, not proof that the data is empty.

## 2. Audit Scope and Limitations

### What was verified

- Branch, commit, dirty worktree, local runtime health, local Vite frontend availability, database-backed API implementation, data counts, API/query paths, production build, lint, tests, and selected repository timing measurements.
- BFF health endpoint returned HTTP 200 at \`http://127.0.0.1:8000/health\`.
- A Vite application instance returned HTTP 200 at \`http://127.0.0.1:5175/\`. The documented default is normally port 5173; ports 5173 and 5174 were already occupied.
- \`PYTHONPATH=src ./venv/bin/python -m pytest -q\` passed: **242 passed** in 12.70s, with 39 existing deprecation warnings.
- \`frontend/npm run build\` and \`frontend/npm run lint\` completed successfully. Lint reports five existing exhaustive-dependency warnings in \`frontend/src/App.tsx\`.

### Important limits

- No browser automation, installed Chromium/Chrome, Playwright package, or screenshot-capable browser was available in this environment. The target-viewport section is therefore **code-inferred**, not rendered-device verified. Existing user-provided screenshots were considered only as corroborating evidence, not newly captured test artifacts.
- The performance timings are local, in-process repository timings serialised to JSON. They exclude HTTP, authentication, browser layout/paint, and network latency.
- The repository was already dirty. The listed changes belong to the active worktree and were not altered. This audit adds only this Markdown report.
- News fetching and any external AI/news service behaviour were inspected from code paths but not exercised against external services.

### Runtime assumptions and setup

The browser application uses the BFF at \`VITE_API_BASE_URL\` when supplied, otherwise \`<current protocol>//<current hostname>:8000\`; see \`frontend/src/App.tsx:72-80\`. The BFF uses the local SQLite database \`src/data/charities.db\` through \`src/bff/repositories.py:4721-4738\`. Authentication is a local demo-cookie/JWT flow; all charity endpoints are protected in \`src/bff/charity.py:24-28\`.

Documented local commands are:

    ./start_backend.sh
    cd frontend && npm run dev
    cd frontend && npm run build
    PYTHONPATH=src ./venv/bin/python -m pytest -q

\`start_backend.sh\` launches Uvicorn on 127.0.0.1:8000 with reload; Docker Compose supplies the BFF-only alternative. No migration or missing-environment-variable issue blocked the locally running BFF during this audit.

## 3. Verified Runtime and Architecture

| Layer | Current implementation | Evidence |
|---|---|---|
| Frontend | React 19.2.7, TypeScript, Vite | \`frontend/package.json\` |
| Navigation | Manual SPA state plus \`window.location.pathname\`, \`history.pushState\`, and \`popstate\`; no React Router | \`frontend/src/App.tsx:410-484\` |
| Styling | Project CSS in \`frontend/src/index.css\`, utility-like class strings and substantial inline component styles | \`frontend/src/index.css\`, \`frontend/src/App.tsx\` |
| Client state | Local React state/effects; URL query parameters for portions of grant state; no Redux/Zustand/query cache | \`frontend/src/App.tsx:485-894\`, \`frontend/src/components/OverviewDashboard.tsx:145-257\` |
| Maps/charts | \`@svg-maps/world\`, Recharts, Leaflet / react-leaflet, Lucide icons | \`frontend/package.json\`, \`GrantWorldMap.tsx\` |
| Backend/BFF | FastAPI, repository layer | \`src/bff/main.py:51-91\`, \`src/bff/repositories.py\` |
| Database | SQLite \`src/data/charities.db\` | \`src/bff/repositories.py:4721-4738\` |
| Aggregate cache | \`grant_overview_cache\` table, startup warming for three selected sources | \`src/data/db_loader.py:229-249\`, \`src/bff/main.py:51-68\` |

The BFF startup event is deprecated FastAPI syntax, which accounts for part of the test warning set; it is not an immediate user-facing failure. The frontend auto-logins against \`/api/auth/login\` before protected data calls; see \`frontend/src/App.tsx:87-108\`.

The production build emitted one significant bundle signal: the main JavaScript output is **1,952.10 kB / 607.87 kB gzip**, above Vite’s 500 kB warning threshold. The map and chart libraries are currently included in the initial application bundle rather than clearly lazy-loaded by view.

## 4. Current Navigation and Route Inventory

There is one practical browser path, normally \`/\`, rather than three independently routed directory pages. \`App\` selects \`activeTab\` (\`overview\`, \`directory\`, \`admin\`) and \`directoryMode\` (\`profiles\`, \`registry\`, \`funders\`). The only special path detection is the presence of a \`funder_country\` query parameter; it opens the funder mode on initial load/back navigation. See \`frontend/src/App.tsx:410-484\`.

| User-facing area | Current path / state | Primary component(s) | Fetch/API | Loading / empty / error | Reachability and duplication |
|---|---|---|---|---|---|
| Funding Landscape / Overview | \`/\`, \`activeTab=overview\` in memory; grant query params | \`App\`, \`OverviewDashboard\`, \`GrantWorldMap\` | \`GET /api/charities/grants/overview\`, optionally \`/overview/trends\` | Map/dashboard loading and fallback states; BFF unavailable message | Primary sidebar destination. A legacy overview exists only when \`SHOW_LEGACY_OVERVIEW\` is enabled; \`App.tsx:1589+\`. |
| World map country action | Same view; selected ISO country in query and component state | \`GrantWorldMap\` | Aggregate overview payload | Map supplies selection/loading/help affordance | “Explore active funders” invokes hand-off; map also has optional illustrative connections. |
| Main Donors | \`/?funder_country=GB&...\`; \`activeTab=directory,directoryMode=funders\` | \`SourceFunderResults\` | \`GET /api/charities/grants/funders\`; detail \`/funders/{key}\` | Explicit loading, empty and error states | Reachable from map and Directory tab. New, currently untracked worktree component: \`frontend/src/components/SourceFunderResults.tsx\`. |
| Organization Profiles | \`/\`; \`activeTab=directory,directoryMode=profiles\` | \`App\` profile list and inline detail modal | \`GET /api/charities\`, \`/{id}\`, \`/{id}/grants\`, \`/{id}/sankey\`, \`POST /{id}/score\` | List loading/empty/error; profile modal data states | Reachable tab. Overlaps Main Donors visually but uses a different row entity. |
| Full Charity Commission Register | \`/\`; \`activeTab=directory,directoryMode=registry\` | \`RegistryDirectory\` | \`GET /api/charities/directory/organizations\`, item detail endpoint | Loading, error, no-result, cursor load-more | Reachable tab; an advanced official-register utility, not a donor list. |
| Profile details | In-memory \`selectedCharity\`; no shareable detail URL | Inline modal in \`App\` | Four concurrent calls after selection; news on demand | Modal conditions and API errors | Selecting a profile card opens it; close returns to mounted list/filter state. |
| Source-funder details | In-memory selected item; no shareable detail URL | \`App\` source-funder modal / Recharts Sankey | \`GET /api/charities/grants/funders/{source_funder_key}\` | Source list/modal error and loading conditions | From Main Donors row/detail control. Current modal is visually distinct from profile detail. |
| Registry record details | In-memory \`selectedOrganization\` | \`RegistryDirectory\` dialog | Detail request by registry identifier | Modal loading/error | A linked enriched profile can be opened from this dialog. |
| Data sources | Header toggle / global selection | \`App\` header control | Passed to overview/profile calls; source strings in URL only on donor handoff | No separate request | It is visually global but does not consistently affect every directory mode. |
| News | Profile detail only, user initiated | \`App\` | Profile news endpoint | On-demand state | Not a directory-list concern. |

The sidebar and header are rendered at \`frontend/src/App.tsx:1442-1564\`. Directory tabs are mounted at \`App.tsx:1790-1861\`; the profile list runs through \`App.tsx:1863-2147\`; profile detail begins around \`App.tsx:2365\`.

## 5. Current Main Donors View

### Purpose and entity

The title and controls communicate “source funders by beneficiary geography.” This is the appropriate answer to “which organisations have been observed awarding grants associated with this country?” It is **not** an organization-profile directory. The endpoint is \`GET /api/charities/grants/funders\` in \`src/bff/charity.py:303-345\`, backed by \`SQLiteCharityRepository._source_funder_scope\` at \`src/bff/repositories.py:1716-1979\`.

A source-funder identity is computed from:

- grant source + raw funding-organisation source identifier when available; otherwise
- grant source + a normalised donor name.

This produces 292 distinct observed source-funder identities in the present 200,000-grant 360Giving dataset. It is a query-time group; **there is no \`source_funders\` database table**. Name fallback may split one real organisation across spelling variants or merge ambiguously similar names. It must therefore retain identity method and source provenance.

### Inclusion, exclusion, aggregation and labels

The UI reads the following URL parameters in \`SourceFunderResults.tsx:141-210\`:

\`funder_country\`, \`grant_currency\`, \`grant_from\`, \`grant_to\`, \`grant_geo\`, \`grant_programme\`, \`grant_donor\`, \`grant_recipient\`, \`grant_sources\`, \`funder_sort\`, and \`funder_page\`.

The request sends beneficiary country, currency, sort, page/page_size=25, date range, beneficiary geographies, programme areas, donor, recipient, and sources. It groups source grant rows after those scopes are applied. Single-country grants contribute to country funding totals; multi-country grants contribute to observed activity but their full value is excluded from country-attributable money. The UI identifies this exception. In automatic currency mode it uses historical EUR conversion; a selected source currency restricts rather than converts the scope. Conversion is based on original grant date and stored ECB rates where available.

Displayed information:

| Default visible field | Meaning / source | Classification | Recommendation |
|---|---|---|---|
| Rank and funder name | Query-time source identity | Primary decision information | Keep |
| Identity method | Source ID versus normalised-name fallback | Data-quality/provenance | Move behind a compact “Observed source” status or details |
| Grant count, distinct recipients, latest activity | Aggregated observed source grants | Primary/supporting | Keep compactly |
| Country-attributable observed funding | Single-country amount, automatic EUR or selected original currency | Primary decision information | Keep; retain multi-country caveat in details |
| Leading programme areas | Derived grant programme categories | Supporting | Keep at most two; all in details |
| Data source and link status | 360Giving provenance / linked profile status | Provenance | Keep short text/badge; never colour-only |
| “Source-only” | No direct linked profile is available | Data-quality/provenance | Keep, with clear explanation |
| “Open verified profile” action | Only when exactly one direct linked profile is returned | Primary action where available | Keep, but call it “Open linked profile” unless a stronger reviewed-verification contract is established |
| Six summary tiles | Scope-level identity/grant/recipient/funding/link counts | Supporting | Keep only the 2–3 decision-critical totals by default; remainder under scope details |
| Eight permanent grant controls | Scope controls | Supporting / administrative | Put advanced controls in a drawer; preserve state and API support |

### Linkage and interaction

The source-funder scope records a linked directory profile only when a grant \`funding_charity_id\` joins an actual \`charities.charity_id\` and has a compatible linked funder name; see \`repositories.py:1886-1889\`. It reports a link only if precisely one directory ID is associated with the source identity; it intentionally does not choose arbitrarily when several IDs exist (\`repositories.py:1982-2020\`). This is safer than auto-enrichment but should be described as “direct linked profile” rather than silently equivalent to identity verification.

Clicking a source funder fetches a source-funder detail and opens an in-memory modal. That state has no URL, so it cannot be deep-linked and browser Back cannot reproduce it. Pagination/sort/filter state are URL-backed and preserved. The detail presents one donor-to-many-recipient flow, capped to 15 links; see \`repositories.py:2023-2151\` and \`App.tsx:2739-2842\`.

### Source evidence, 360Giving organisation links and website links

This is feasible with the data already stored, but it is a **multi-hop evidence chain**, not one generic \`source_url\`.

#### Verified current chain

The raw 360Giving payload stored in \`grants.raw_grant_data\` contains organisation API links and, when the publisher supplied it, the organisation website. Both are different from the publisher URL already exposed by the API:

    Grant raw JSON
      ├─ recipients[].self                    → 360Giving recipient organisation JSON
      │    └─ grants_received                 → grant-record JSON, including recipientOrganization[].url when supplied
      ├─ funders[].self                       → 360Giving funder organisation JSON
      │    └─ grants_made                     → grant-record JSON, including fundingOrganization[].url when supplied
      ├─ data.recipientOrganization[].url     → recipient website supplied in the observed grant record
      ├─ data.fundingOrganization[].url       → funder website supplied in the observed grant record
      └─ data.dataSource                      → publisher's own website / open-grants data

For example, the stored Oxfam grant \`360G-BMT-2022-2-OXF\` contains:

    recipients[0].self = https://api.threesixtygiving.org/api/v1/org/GB-CHC-202918/
    funders[0].self    = https://api.threesixtygiving.org/api/v1/org/GB-CHC-1076925/
    data.dataSource    = https://www.brianmercertrust.org/

The recipient/funder \`self\` URL resolves to the public 360Giving organisation JSON. The live Oxfam recipient response verified during this audit gives its canonical ID, name, grant aggregates, linked organisation IDs, and a \`grants_received\` URL. Opening that second JSON response returns individual grant records. Some Oxfam records contain \`data.recipientOrganization[0].url = http://www.oxfam.org.uk\`. This is the exact navigation chain described in the request.

The organisation endpoint itself does **not** contain a website field. The individual grant record’s recipient/funder \`.url\` is supplied by the grant publisher. Separately, the linked enriched/Charity Commission profile \`charities.charity_id=202918\` has \`website=www.oxfam.org.uk\` and an official Charity Commission \`source_url\`. These can agree, but remain distinct provenance sources.

That means the honest chain is:

    observed grant
      → 360Giving recipient/funder organisation JSON
      → grants_received / grants_made JSON
      → optional recipient/funder website embedded in an observed grant
      → optional direct/accepted local profile or registry resolution
      → separately sourced organisation website from profile/official-record metadata

Each final website must carry its own origin label: either “website supplied in this 360Giving grant record” or “website from linked Charity Commission/profile data.”

#### Current coverage and implementation state

- Every current grant has a non-empty, valid HTTP(S) publisher \`source_url\` and a \`source_record_id\`. They are stored in \`grants\` by \`src/data/db_loader.py:195-200, 484-522\`, selected at \`src/bff/repositories.py:1037-1055, 4387-4438\`, and exposed as \`GrantDetail.source_url\` in \`src/bff/schemas.py:297-322\`.
- **183,947** raw grants expose \`recipients[0].self\`; all **200,000** expose \`funders[0].self\`. Neither is currently normalised into a dedicated database column or emitted by the grant/funder endpoints; they remain inside \`raw_grant_data\`.
- **72,906** grants contain \`data.recipientOrganization[0].url\`, representing 35,841 distinct recipient website values. **3,594** grants contain \`data.fundingOrganization[0].url\`, representing five distinct funder website values. These are the publisher-provided organisation website fields found by following the 360Giving organisation-record path; they are also retained locally in \`raw_grant_data\`.
- The API already returns a representative publisher \`source_url\` for a grouped source-funder row (\`SourceFunderItem.representative_source_url\`, \`schemas.py:370-385\`; calculated at \`repositories.py:1914-1916, 2019\`). Source-funder detail returns up to 50 individual grant samples with that publisher URL (\`repositories.py:2266-2305\`).
- The 200,000 publisher URLs have only **309 distinct values**, so they are often publisher/API/open-data URLs shared by many awards. They must not be labelled “recipient website” or “individual grant page.”
- 361 of 364 enriched profile rows have a profile \`source_url\`; those rows also carry an optional \`website\` field in \`charities\`. Profile evidence is separate from observed grant evidence.
- The current header **Data sources** disclosure exposes source-selection buttons only (\`frontend/src/App.tsx:1539-1561\`). Main Donors does not render any publisher/evidence link, and the profile grant table does not render an outbound organisation-record link (\`frontend/src/components/SourceFunderResults.tsx:12-45, 430-452\`; \`App.tsx:2666-2726\`).

The future detail should add a final, collapsible **Source evidence** section, not a generic external-link icon scattered across every list row:

| Evidence item | Recommended user-facing label | When to show | Required safety / provenance rule |
|---|---|---|---|
| \`recipients[].self\` / \`funders[].self\` | “View 360Giving recipient record” / “View 360Giving funder record” | Individual observed grant relationship | Extract and expose the raw value explicitly; open in a new tab with \`noopener noreferrer\`; it is an organisation-data JSON link |
| \`recipientOrganization[].url\` / \`fundingOrganization[].url\` | “Visit recipient website” / “Visit funder website” | Individual observed grant relationship, when this raw field exists | State “Website supplied in this grant record”; it is publisher-provided and should not be silently merged with a profile website |
| \`data.dataSource\` / current \`source_url\` | “View publisher’s grant data” | Individual grant or grouped funder detail | State that it can cover multiple awards and may be a publisher/API page |
| Resolved profile \`website\` | “Visit organisation website” | Only if a direct/accepted linked profile supplies it | Label its provenance, for example “Website from Charity Commission profile”; never derive it from name alone |
| \`source_record_id\` | “Source record ID” | Copyable detail/methodology field | Preserve source namespace and ID; no name-only reconstruction |
| Profile \`source_url\` / \`source_records\` | “Profile data source” | Linked-profile evidence section | Keep visibly separate from observed grant evidence |
| Data-source selector | “Included sources” | Header / active-filter chips | Continue to filter scope; it is not a proof link |

No new crawler or live 360Giving fetch is required. An endpoint extension is required to expose the recipient/funder \`self\` URLs and publisher-provided recipient/funder website values without making the browser parse arbitrary raw JSON. A later ingestion enhancement can normalise these into \`funder_source_record_url\`, \`recipient_source_record_url\`, \`funder_website_observed\` and \`recipient_website_observed\`, with a validated \`link_kind\` (360Giving organisation JSON, observed organisation website, publisher data source, profile website, unknown).

## 6. Current Organization Profiles View

### Purpose and population

The profile directory answers “which enriched organisations are in our directory?” It uses \`GET /api/charities\` at \`src/bff/charity.py:30-72\`, whose repository implementation is \`get_all\` at \`src/bff/repositories.py:3220-3419\`. Rows are \`charities\` records, not source-funder identities.

Current population:

- 364 profile records;
- 299 sourced from Philea and 65 from the Charity Commission;
- 299 are currently organisation-level only and 65 are classified as unknown coverage;
- only 17 profiles have any direct matched grant on either donor/recipient side.

Therefore a profile may be valuable background research but must not be presented as an observed donor merely because it has a name, headquarters, or financial data.

### Filters and their technical meaning

The profile list uses a 250 ms debounced search and 50-row pagination (\`App.tsx:585-606\`, \`801-894\`). It sends \`search\`, \`reg_status\`, \`tag\`, \`region\`, \`size\`, \`tags\`, \`foundation_regions\`, \`funding_regions\`, \`sources\`, \`min_annual_giving\`, \`min_avg_grant_size\`, \`skip\`, and \`limit\`.

The left sidebar fields in \`App.tsx:1863-2071\` are always visible: name search/suggestions, thematic sector, foundation location, beneficiary geography, annual giving, average grant and reset. “Beneficiary Geography” is technically ambiguous here: its backend criterion is grant rows for which the linked directory organisation is the **funder** (\`funding_charity_id\`), followed by parsed beneficiary locations; it is not generic geographic focus nor recipient activity. This causes empty results even when a similarly named source funder appears on the map.

### Visible fields and information density

| Default field | Meaning / source | Classification | Recommendation |
|---|---|---|---|
| Profile ID | Philea source record / charity identifier | Technical | Move to details, except official charity number in registry mode |
| Organisation name | \`charities\` profile name | Primary decision information | Keep |
| Source badge | Profile source | Provenance | Keep one compact label |
| Organisation type | Profile metadata | Supporting | Keep only where reliably populated; otherwise details |
| HQ location | Profile headquarters | Supporting; not beneficiary geography | Keep only if relevant to comparison |
| Up to two programme tags | Source or inferred classification | Supporting / provenance | Keep one primary tag, show source/inferred basis in details |
| “Review suggested” / organisation-level-only | Coverage status | Data quality | Keep as an accessible text status, not as a competing primary badge |
| Latest income | Profile financial field | Supporting | Keep where present; label source/year and move other financial values to detail |
| “Select Details” | Card action | Primary action | Keep but use a semantic button/card pattern |

The rows can duplicate the same real organisation across Philea, Charity Commission and source-funder identity representations. That is acceptable only when the interface makes the record type and relationship explicit.

### Interaction issues

Typing suggestions are fetched with \`?search=<value>&limit=6&sources=\`; see \`App.tsx:632-673\`. The suggestion UI has listbox/options but lacks full combobox state/keyboard behaviour. Profile cards are clickable \`div\` elements without keyboard semantics (\`App.tsx:2082-2117\`). Selecting one launches four requests in parallel: profile, grants, Sankey data and scoring (\`App.tsx:675-692\`). This produces detail work even before a user requests the deeper sections.

## 7. Current Charity Commission Register View

### Purpose and entity

The registry is a large, official-record lookup. It uses \`RegistryDirectory\` and \`GET /api/charities/directory/organizations\` in \`src/bff/charity.py:87-146\`. Its table is \`charity_registry_organizations\`, not \`charities\` and not observed grant funders. The current registry contains 397,469 source-current records and matching FTS records.

The query implementation at \`src/bff/repositories.py:2918-3137\` uses FTS5/prefix search where applicable, \`is_current_source_record\`, finance/status fields and stable cursor pagination. It is the correct advanced utility for questions such as “does the Commission register contain this charity?” rather than “who funds this beneficiary country?”

### UI, filters and linkage

\`frontend/src/components/RegistryDirectory.tsx:81-168\` maintains its own states and fetches 50 records at a time with a 300 ms search debounce. Its sidebar has eleven persistent controls (\`RegistryDirectory.tsx:217-270\`): organisation name, exact number, status, income/expenditure ranges, country/region, observed beneficiary geography, profile layer, observed grants, and sort. It has cursor “Load 50” pagination.

Cards show charity number, registered name, status, enriched/observed/Philea badges, city/region, and GBP income (\`RegistryDirectory.tsx:273-297\`). Its modal displays registry attributes, activities, observed-data message, and an accepted enriched-profile link when present (\`RegistryDirectory.tsx:302-321\`).

The accepted registry link table is \`organization_registry_links\` (\`src/data/registry.py:115-128\`). Current data has 343 accepted links, all exact-identifier method and confidence 1.0; 64 distinct enriched profiles are linked, while 9 profiles link to multiple registry records. Only 121 accepted registry links have any direct observed grant existence. A registry record must never inherit observed-donor status merely because it has a profile link.

| Default field / feature | Classification | Recommendation |
|---|---|---|
| Registered name and charity number | Primary for registry search | Keep in advanced registry mode |
| Registration status and registered office | Supporting official information | Keep compactly |
| Income/expenditure and activities | Supporting / research | Keep in record detail; summary only when a user sorts by it |
| Enriched/observed/profile badges | Linkage / provenance | Keep as text-led evidence, do not imply equivalence |
| Registry-specific range/status filters | Advanced search | Keep in a drawer/advanced mode |
| Beneficiary geography filter | Data relationship feature | Keep only with explicit label: “observed grants linked to an accepted profile” |

## 8. Current Donor/Organization Detail Behaviour

There are three substantially different detail experiences:

1. **Profile modal** — \`App.tsx:2365-2731\`: contact/address, website, program/geography metadata, profile classification and coverage, experimental relevance score, ranked funding relationships, financial totals/trends, lazy AI news, and observed grant table.
2. **Observed source-funder modal** — \`App.tsx:2739-2842\`: selected source identity, aggregate grant details, donor-to-recipient Sankey, currency/multi-country notes and source data.
3. **Registry dialog** — \`RegistryDirectory.tsx:302-321\`: official registry record and optional accepted enriched-profile link.

Oxfam is a useful concrete test case. Its directory profile has 40 direct recipient-linked grant records. The repository returns Sankey/relationship data as available with 9 nodes and 8 links. A prior apparent “empty” or bad funding-flow view is explained by the visualisation/label layout, rather than absence of the 40 stored relationships.

The profile flow data uses only direct \`funding_charity_id = ? OR recipient_charity_id = ?\` matching in \`get_grants_for_charity\` and \`get_sankey_data\` (\`repositories.py:4372-4478\`, \`4480+\`). This is deliberately narrower than donor-name matching and should remain so until stronger linkage evidence exists.

Common information hierarchy for a future shared detail is feasible:

| Section | Observed-only source funder | Direct linked profile | Registry record |
|---|---|---|---|
| Identity / observed status | Always | Always, plus link evidence | Only when opened in advanced registry mode |
| Grant activity / recipients / amounts | Always within the selected grant scope | When direct grant links exist | Only explicitly linked observed evidence |
| Profile contact/HQ/website/programmes | Never fabricate | Show with source and coverage | Registry fields, not profile fields |
| Registry registration data | Never fabricate | Show only through accepted registry link | Native content |
| Methodology and match evidence | Available last | Available last | Available last |

The existing profile and source modals should not be forced into one renderer without a discriminated result type. A common layout can share shell, heading, amount, tabs/sections and loading behaviour, but fields must remain conditional on entity/evidence type.

## 9. Data Model and Entity Relationships

### Entity diagram

    grants (grant_id)
      ├─ funding_name + funding_org_source_id ──> source-funder identity (query-time, 1 grant : 1 identity)
      ├─ funding_charity_id ──> charities.charity_id (optional direct join; not universally valid)
      ├─ recipient_name + recipient_org_source_id ──> source-recipient identity (query-time)
      ├─ recipient_charity_id ──> charities.charity_id (optional direct join; not universally valid)
      ├─ 0..n grant_beneficiary_terms / grant_beneficiary_countries
      ├─ 0..n grant_programme_categories
      └─ original amount/currency/date + optional amount_eur/exchange-rate basis

    charities (charity_id, enriched directory profile)
      └─ 0..n organization_registry_links ──> charity_registry_organizations

    charity_registry_organizations (registry_id, official current record)
      └─ 0..n organization_registry_links (match status/method/confidence/review metadata)

    profile enrichment / financial metadata / news
      └─ belongs to charities, and must not be assumed for a source-funder identity

### Relationships and evidence

| Relationship | Cardinality / join | Mandatory? | Evidence level / duplicate risk |
|---|---|---|---|
| Grant → source funder | 1:1 query-time identity from source ID or normalised name | Conceptually required from grant donor fields; ID may be absent | Observed source data; name fallback can split/merge |
| Grant → directory profile (funder/recipient) | 0..1 stored ID per side; join to \`charities\` | Optional | Direct join only when ID resolves; current database has unresolved values |
| Source funder → directory profile | 0..n derived through direct grant joins | Optional | Endpoint exposes an actionable profile only if exactly one ID; no persistent source-funder-link table |
| Directory profile → registry record | 0..n through \`organization_registry_links\` | Optional | Current importer accepts exact identifier links; match metadata retained |
| Grant → beneficiary country | 0..n normalised derived rows / JSON fallback | Optional | Beneficiary location, not donor HQ |
| Grant → programme area | 0..n derived category rows | Optional but currently populated for all grants | Source/inferred classification must remain traceable |
| Grant → EUR amount | 0..1 stored historical conversion | Optional | 199,579 / 200,000 currently have valid conversion |

### Integrity findings

The present database has 2,294 foreign-key-check violations relating to source entity IDs stored in \`funding_charity_id\` / \`recipient_charity_id\`: 464 funding-side rows across 65 distinct unresolved IDs, and 1,830 recipient-side rows across 1,260 unresolved IDs. A non-null value in either field is therefore **not by itself proof of a valid directory link**. Current joined code is safer than naïvely using non-null IDs, but any future unification must use successful joins plus evidence metadata.

Current direct join coverage is:

- 2,440 grants have non-null funding IDs; 1,976 join current profile rows;
- 2,907 grants have non-null recipient IDs; 1,077 join current profile rows;
- only 17 profile rows participate in at least one direct observed grant link;
- three profiles occur as direct funders and fifteen as direct recipients (one or more can do both).

The conceptual chain requested by the product is supported only as:

    grant → source funder [observed]
          → optional directory profile [derived direct ID join]
          → optional registry record [accepted exact-identifier link]

It is not a universal mandatory chain and cannot be made one by normalized-name matching without misleading users.

## 10. End-to-End Data Flow

### Funding Landscape

1. \`OverviewDashboard\` reads grant filters from URL in \`frontend/src/components/OverviewDashboard.tsx:145-160\`.
2. It requests the aggregate overview in \`OverviewDashboard.tsx:223-257\`.
3. The repository uses cache-backed overview data; country/programme scoped source rows are then processed to build map/KPIs/trends.
4. \`GrantWorldMap\` renders beneficiary-country choropleth geometry. Map selection is ISO country selection, and is not a donor-headquarters selector. Keyboard/click selection can scroll to the selected-country content (\`GrantWorldMap.tsx:353-360\`).
5. “Explore active funders” calls \`openSourceFundersFromMap\` at \`App.tsx:1047-1069\`.

### Map-to-donor hand-off

The hand-off writes \`funder_country\` and \`funder_sort\`, removes \`funder_page\`, then carries \`grant_currency\`, \`grant_from\`, \`grant_to\`, \`grant_geo\`, \`grant_programme\`, \`grant_donor\`, \`grant_recipient\`, \`grant_granularity\`, and \`grant_sources\`. This is a strong basis for preserving country/grant scope. On return, \`openSourceFundersFromMap\` only removes funder country/sort/page (\`App.tsx:1072-1080\`) so most grant URL state remains.

**Gap:** \`OverviewDashboard\` serialises its map query keys in \`OverviewDashboard.tsx:187-200\`, but it does not reconstruct the global source selection from \`grant_sources\` on reload/back. The donor page receives source scope initially; the overview can later show an unselected global source control despite the query string. This is a state-contract inconsistency.

### Directory profile flow

The profile list calls \`/api/charities\`; open profile fires the four detail calls described in section 6. Profile grant details and source-funder detail intentionally take different back-end paths. Do not silently substitute one for the other.

### Registry flow

Registry search uses its own cursor/query state and calls the registry endpoint. It can open a linked enriched profile, which preserves registry mode because the component remains mounted. The registry does not inherit map grant filters, which is semantically safer than applying them as if registry rows were donor results.

## 11. Filter and URL-State Audit

| Filter / state | UI and frontend state | URL / API | Backend effect and meaning | Navigation survival / issues |
|---|---|---|---|---|
| Beneficiary country | Map selection / Main Donors country control | \`funder_country\`; \`beneficiary_country\` API | Normalised beneficiary-country scope | Transfers map → Main Donors; profile label means something narrower |
| Beneficiary geography | Map multi-scope / donor advanced control / profile and registry controls | \`grant_geo\`; \`beneficiary_geographies\` | Grant beneficiary geography; profile version only linked profile-as-funder grants; registry version only accepted-link observed grants | Same label masks three different semantics |
| Programme area | Overview / donor control | \`grant_programme\`; \`programme_areas\` | Derived programme categories | Transfers map → donor |
| Time range | Overview / donor control | \`grant_from\`, \`grant_to\`; date params | Grant award-date scope | Transfers map → donor |
| Currency | Overview / donor control | \`grant_currency\`; \`currency\` | Auto EUR conversion or original-currency filter | Transfers map → donor |
| Donor text | Overview / donor control | \`grant_donor\`; \`donor\` | Grant donor text scope | Transfers map → donor |
| Recipient text | Overview / donor control | \`grant_recipient\`; \`recipient\` | Grant recipient text scope | Transfers map → donor |
| Source | Header global selector / donor advanced control | \`grant_sources\`; \`sources\` | Source scope | Donor retains it; Overview fails to rehydrate global selection |
| Granularity | Overview | \`grant_granularity\` | Trend/map aggregation UI state | Not relevant to donor identity list |
| Main-donor sort/page | Main Donors | \`funder_sort\`, \`funder_page\` | Backend sort/cursor-like page | Preserved in URL |
| Profile name/status/tags/HQ/amount filters | Profile sidebar | API params listed in section 6; no equivalent canonical URL contract | Profile table filters | State is component-local; not shareable/back-restorable |
| Registry number/status/finance/country/region/links/sort/cursor | Registry sidebar | Registry request params in section 7; component-local | Official record lookup | Separate correctly; not map scope |

The current world-map-to-donor contract is the safest existing foundation. A future route design should first introduce a **canonical serialisable grant-scope object** (including sources) and test round trips, rather than immediately renaming every path. Once a conventional router is introduced, equivalent paths such as \`/funding-landscape?... \` and \`/donors?... \` can use that same contract. Current code does not yet establish those paths as a stable public convention.

Filters most in need of restructuring are the permanently visible profile/registry sidebars, which expose many advanced controls before the user has selected a research task. Move them into an explicit drawer while retaining every current server parameter and clear active-filter chips.

## 12. Visual Hierarchy and Information-Density Audit

### General

The UI frequently gives decision data, provenance, technical metadata, and administrative filters the same visual weight. This is clearest in the profile-card tag stack and permanent filter rail. It makes the answer (“who should I investigate?”) compete with source badges, types, HQ, inferred classifications, review states, and multiple monetary fields.

### Main Donors

The row hierarchy is closest to a practical donor list because observed funding and recipients are visible. The six KPI tiles plus eight control fields consume significant vertical space before results. The best default comparison set is: source donor name, observed/linked state, grant count, recipient count, country-attributable amount, and one leading programme. Identity method, all programme tags, multi-country calculation detail, and raw source metadata belong behind details/methodology.

### Profiles

The profile grid uses visually large cards with multiple small tags and often unavailable or weakly comparable fields. The default card shows source, type, HQ and topical tags at similar emphasis to name/income. Technical IDs and coverage/review labels should not outrank a clear answer about record type and evidence.

### Registry

The register needs more factual fields than donor discovery, but those are appropriate in a separate advanced research mode. Its eleven visible filters form a heavy permanent administrative panel. The default should lead with name/number, current registration status and location; financial ranges and data-link controls can be advanced.

### Funding-flow visual

The observed funding-flow canvas uses a pale beige field, wide empty canvas and multiple left labels whose available width is too small. Curves converge at one destination, producing an imprecise funnel rather than a readable comparison. This is especially poor in the supplied Oxfam screenshot. It needs a ranked relationship list/table as the default, with a compact Sankey only when there are few balanced links and labels can be placed without clipping.

## 13. Responsive Behaviour

This section is code-inferred because a rendered browser was not available. Validate it manually at the following exact sizes before shipping a redesign.

| Target viewport | Inferred current behaviour | Risk / required validation |
|---|---|---|
| 1440 × 900 | Full 260 px sidebar; desktop map uses \`clamp(320px, 100svh - 410px, 580px)\`, about 490 px map height; source donor summary can use six columns | Strong desktop information density, but map/chart and large cards consume initial viewport |
| 1280 × 800 | At exactly 1280, desktop sidebar rule remains rather than the \`<=1279px\` compact treatment; map tends around 390 px | Abrupt breakpoint discontinuity one pixel below; check title/header and four KPI cards |
| 1024 × 768 | Sidebar has compact behaviour at max 1279; KPI transition begins at \`<=1023px\`, so exact 1024 still attempts four KPI columns; map minimum about 380 px | Likely cramped KPI row and a brittle 1023/1024 transition |
| 834 × 1194 | Portrait/tablet drawer rules are present; KPI grid is two columns; map clamp about 400 px | Validate drawer focus, scroll locking and map touch targets |
| 390 × 844 | Mobile drawer; two KPI columns; map clamp about 304 px; donor table switches to cards only at \`<=680px\` | Validate tab wrapping, source-funder modal and full-screen detail treatment |

Relevant responsive rules are concentrated in \`frontend/src/index.css:868-888\` and later override rules at approximately \`3007-3069\`. The CSS contains overlapping historical and new media queries, which makes changes difficult to reason about. The source-funder result table has a 1040 px desktop minimum and becomes cards only at 680 px; between 681 and 1050 px it may horizontally scroll. Its KPI grid changes from six to two to one columns at 1050/680. Registry has more deliberate one-column/reduced-detail rules at 900/520, while profile and source-funder modal inline styles have less intentional mobile restructuring.

The future detail model is feasible as:

- **Desktop:** list plus an accessible right-side detail panel, with wide table/chart only in the panel’s dedicated section.
- **Tablet:** list plus drawer/full-width sheet; do not retain the desktop three-column information density.
- **Mobile:** list then full-screen detail route/sheet; do not attempt desktop modal-within-list geometry.

The overview filter drawer already has useful focus trapping and Escape behaviour. The future donor filter drawer can reuse that interaction pattern; the existing profile/sidebar implementation cannot simply be shrunk.

## 14. Performance Findings

### Measured repository timings

Measurements ran against the current local SQLite database on 24 July 2026 and include JSON serialisation; they do not include HTTP or browser rendering.

| Operation | Result | Interpretation |
|---|---:|---|
| Overview, cold, all sources | 3.281 s, 88,746 bytes | Acceptable only with loading feedback/cache; too slow for repeated interactive re-scoping |
| Overview, hot cache | 0.004 s, 88,746 bytes | Cache works very well |
| Yearly trends, cold | 6.737 s, 5,929 bytes | Small result but expensive computation; it should be cached or pre-aggregated |
| Profile page (51 rows) | 0.002 s, 52,006 bytes | List paging itself is fast |
| Registry page (50 rows) | 0.013 s, 18,078 bytes | Cursor/FTS approach is effective |
| Registry search “oxfam” | 0.006 s, 1,449 bytes | Effective |
| Main Donors, US | 3.369 s, 22,669 bytes, 22 results | Work remains high despite a smaller country scope |
| Main Donors, GB | 13.734 s, 26,579 bytes, 25 results | Current primary donor journey is too slow |

The database is approximately 1.33 GB. It contains 200,000 360Giving grants, 71,286 mapped beneficiary-country grant rows (35.64%), all 200,000 categorised by programme, 199,579 valid EUR converted amounts, 421 grants without a valid conversion, and 37 overview-cache entries.

### Main bottlenecks

| Priority | Layer | Finding | Evidence / impact |
|---|---|---|---|
| P0 | Backend/query/CPU | \`_source_funder_scope\` obtains scoped grant rows then parses JSON and groups all candidate rows in Python | \`repositories.py:1716-1979\`; GB scans a large mapped scope and costs 13.7 s |
| P0 | Backend/API | Source-funder detail recomputes full source scope instead of retaining/caching grouped work | \`repositories.py:2023-2151\`; detail adds repeat latency |
| P1 | Backend/query | Cold overview/trend aggregation is expensive despite a small returned payload | Cache helps overview; trends need a compatible cache/materialised aggregation |
| P1 | Frontend/API | Opening one profile starts four requests, including score and grant/relationship data before requested | \`App.tsx:675-692\`; lazy-load secondary tabs |
| P1 | Frontend bundle | One 607.87 kB gzip initial JS chunk includes map/chart dependencies | Build output; defer directory/registry/Sankey/chart code |
| P2 | Backend/query | Profile \`funding_regions\` filtering can fetch and JSON-parse every non-null funder-linked grant; broad \`LIKE %query%\` search is also expensive at scale | \`repositories.py:3220-3419\` |
| P2 | Frontend rendering | Sankey attempts many labels/paths in a wide canvas and has poor readable density | Current source detail renderer \`App.tsx:2739-2842\` |

No evidence of an N+1 pattern was found in the paged registry or initial profile-list request. The greater risk is broad Python post-processing and repeated aggregate queries. Use staged SQL aggregation/derived table/cache before attempting client-side optimisation.

## 15. Accessibility Findings

### Strengths

- Overview filter drawer has dialog semantics, focus trapping, Escape support and focus restoration: \`OverviewDashboard.tsx:310-368\`.
- Interactive map countries expose button role, keyboard Enter/Space operation, tab stops and country-specific accessible labels: \`GrantWorldMap.tsx:448-513\`.
- Map loading has live/progress communication; map legend/help includes textual explanation in addition to colours.
- Main Donors desktop table has table headers, and mobile cards expose data labels.
- Registry record dialog uses \`role="dialog"\` and \`aria-modal\`; form labels are generally present.

### Gaps

| Priority | Issue | Evidence / remedy |
|---|---|---|
| P0 | Profile cards are clickable non-semantic divs | \`App.tsx:2082-2117\`; use a button/link or complete keyboard handling |
| P0 | Profile and observed-source modals lack dialog role, focus trap, Escape/return-focus lifecycle | \`App.tsx:2365+\`, \`2739+\`; use the overview drawer pattern |
| P1 | Registry dialog has role but not demonstrated focus trap, Escape or focus restoration | \`RegistryDirectory.tsx:302-321\` |
| P1 | Tabs lack complete \`aria-controls\` / \`tabpanel\` relationship and arrow-key tab behaviour | Directory tab implementation around \`App.tsx:1790-1830\` |
| P1 | Search suggestions lack complete combobox semantics and keyboard active-descendant selection | \`App.tsx:632-673\` |
| P1 | Sankey labels clip, which is both visual and assistive-information loss | Use an accessible ranked table and make visual chart optional |
| P2 | \`.world-map-country:focus\` removes standard outline and relies on SVG stroke visual | \`frontend/src/index.css\`; validate strong visible focus at high contrast |

Verified/linked/source-only states must always include text, not just purple/grey/badge colour. Automated contrast, screen-reader, keyboard, touch-target and responsive tests remain required because no browser/AT tooling was available here.

## 16. Data-Preservation Matrix

“Move” means relocate in the interface; it does **not** authorise deletion from API or database.

| Current page | Current field / feature | Current data source | Component / API | Future location | Default? | Details? | Advanced? | Retained? | Required change | Risk |
|---|---|---|---|---|---|---|---|---|---|---|
| Main Donors | Source funder name / ID method | Grant donor fields; query identity | \`SourceFunderResults\` / \`/grants/funders\` | Donor Directory row | Yes | Yes | Yes | Yes | Shared discriminated donor result | High |
| Main Donors | Source provenance | \`grants.source\` | Same | Row status + methodology | Compact | Yes | Yes | Yes | Preserve source / identity method | High |
| Main Donors | 360Giving recipient/funder JSON link, observed recipient/funder website, publisher URL, source record ID | \`raw_grant_data.recipients[].self\`, \`funders[].self\`, \`recipientOrganization[].url\`, \`fundingOrganization[].url\`, \`grants.source_url\`, \`source_record_id\` | Grant/funder APIs expose publisher URL/ID; 360Giving JSON/observed websites require a typed response extension | Collapsible Source evidence; individual relationship/transaction actions | No | Yes | Yes | Yes | Extract typed values server-side; distinguish observed organisation website, publisher data, 360Giving JSON and profile website links | High |
| Main Donors | Grant count / recipients / latest date | Aggregated grants | Same | Donor row/detail | Yes | Yes | Yes | Yes | Reuse endpoint or aggregate layer | Low |
| Main Donors | Country-attributable funding | EUR/original grant amounts, single-country rule | Same | Donor row/detail | Yes | Yes | Yes | Yes | Preserve multi-country policy and label | High |
| Main Donors | Original currency / EUR conversion | \`grants\`, \`exchange_rates\` | Same | Details/methodology; selected display | No | Yes | Yes | Yes | Canonical currency/scope contract | High |
| Main Donors | Leading programmes | Derived programme categories | Same | One row tag / all details | One | Yes | Yes | Yes | Keep classification provenance | Medium |
| Main Donors | Source-only / linked profile state | Direct joined profile IDs | Same | Row status | Yes | Yes | Yes | Yes | Do not equate null/non-null ID with link | High |
| Main Donors | Recipient relationship / Sankey | Stored grant transactions | Source detail API | Details, ranked default | No | Yes | No | Yes | Replace fragile chart default, retain data | Medium |
| Main Donors | Country/time/program/source/donor/recipient filters | URL + API scope | \`SourceFunderResults\` | Compact chips + drawer | Chips | N/A | Yes | Yes | Canonical URL object | High |
| Profiles | Profile name | \`charities\` | \`App\` / \`/charities\` | Search result / linked donor enrichment | Conditional | Yes | Yes | Yes | Separate from source-funder row | High |
| Profiles | Profile source/type/HQ/tags/coverage | Enriched profile metadata | \`App\` | Linked-profile section | Minimal | Yes | Yes | Yes | Move secondary tags from row | Medium |
| Profiles | Website/email/phone/address | Enriched profile metadata | Profile detail endpoint | Linked-profile detail | No | Yes | No | Yes | Conditional evidence-only rendering | High |
| Profiles | Latest income/expenditure / trends | Profile financial fields | Profile detail | Financial detail | Optional | Yes | Sort/filter | Yes | Label currency/year/source | Medium |
| Profiles | Relevance score | Score endpoint | \`POST /{id}/score\` | Secondary detail | No | Yes | No | Yes | Lazy load; clarify experimental status | Medium |
| Profiles | AI news | News enrichment | Profile news endpoint | Secondary detail | No | Yes | No | Yes | On-demand loading/progress/date/source | Low |
| Profiles | Profile grant table / relationships | Direct profile-linked grants | \`/{id}/grants\`, \`/{id}/sankey\` | Details | No | Yes | No | Yes | Preserve direct-link scope distinction | High |
| Profiles | Profile search and all filters | Profile query params | \`/api/charities\` | Separate profile/advanced directory filter set | No | N/A | Yes | Yes | Keep out of observed donor default unless status is explicit | High |
| Registry | Registered name / charity number | \`charity_registry_organizations\` | \`RegistryDirectory\` / registry API | Advanced Registry Search | Yes in registry | Yes | Yes | Yes | Retain route/mode initially | High |
| Registry | Registration status / address / activities | Official record | Registry detail | Registry detail | Compact | Yes | Yes | Yes | Registry-specific layout | Low |
| Registry | Income/expenditure / sorting | Official registry finance | Registry list/API | Advanced filters/sort | No | Yes | Yes | Yes | Drawer/filter preservation | Low |
| Registry | Accepted enriched link / method / confidence | \`organization_registry_links\` | Registry API/detail | Evidence section | Status | Yes | Yes | Yes | Never convert registry row into donor automatically | High |
| Registry | Observed grant indicator/geography | Direct accepted link then grants | Registry filter | Advanced registry evidence | No | Yes | Yes | Yes | Explicit semantics in label | High |

## 17. Feasibility of the Two-Screen Structure

The proposed structure is technically and semantically feasible **with a staged integration layer**, not a merge of current tables.

### Screen 1 — Funding Landscape

Retain \`OverviewDashboard\` and \`GrantWorldMap\`. The map already uses beneficiary geography and can hand a canonical grant scope to observed funders. Keep country, programme, time and compact aggregate summary. The primary action should remain “Explore observed funders” or a similarly precise term; calling them merely “organisations” obscures the entity distinction.

### Screen 2 — Donor Directory

Make the current Main Donors source-funder aggregation the default list. It should contain a discriminated item such as:

    kind: source_funder
    identity: source + source-id-or-normalised-name
    observed_metrics: counts, recipient count, currency-aware totals, programmes
    profile_link: zero / one / many-without-auto-selection, with evidence
    registry_link: absent or separately traceable accepted link

Suggested statuses are:

- **All observed funders** — all source-funder identities in map scope;
- **Linked profiles** — only rows with a successful, explicitly described profile linkage;
- **Observed only** — source-funder identities without that linkage.

Avoid “Verified profile” until product/data owners confirm whether today’s direct Funder-ID join meets their definition of verification. The current record has good evidence of a direct database relationship but does not store a separately reviewed source-funder-profile match record.

### Details

Use one detail shell but render discriminated sections. Observed transactions remain source-data facts. Enriched profile information appears only under a linked-profile evidence section; it must never fill an observed-only donor with inferred website/HQ/type. The registry remains a linked official-record section or separate advanced record dialog.

The shared shell should end with a collapsed **Source evidence** section. Reuse the existing grant-level publisher \`source_url\`, \`source_record_id\`, raw recipient/funder website values, profile \`source_url\`/website and group-level \`representative_source_url\`; expose raw 360Giving recipient/funder \`self\` links and observed website values through a typed server response rather than parsing raw JSON in the browser. Keep publisher data, 360Giving organisation JSON, website supplied in an observed grant, and profile website links visibly separate.

### Registry

Keep \`RegistryDirectory\` as an advanced registry-search mode. Do not apply map filters to it by default. A user can explicitly filter “registry records with observed grant evidence through accepted linked profiles,” but that label must remain precise.

### Routes and redirects

No stable individual routes currently exist beyond \`/\` and query state, so no current route should be removed or redirected in the first migration. Retain the existing modes while a conventional route contract is introduced and parity-tested. Only after the new Donor Directory reaches data/interaction parity should legacy tabs redirect to it. The future exact paths remain a product decision; the existing query names provide a compatible starting point.

## 18. Reusable Components

| Component / capability | Reuse assessment | Conditions |
|---|---|---|
| \`OverviewDashboard\` | Reuse | Extract/standardise URL grant-scope parsing, including sources |
| \`GrantWorldMap\` | Reuse | Preserve beneficiary-country semantics; leave connection arrows explicitly illustrative |
| \`SourceFunderResults\` | Reuse as donor-result data/row foundation | Reduce KPI/control density, improve state and detail renderer |
| Source-funder API and identity grouping | Reuse conceptually | Optimise query/cache before making it default for heavy countries |
| Registry cursor/FTS API | Reuse | Keep as advanced registry utility |
| \`RegistryDirectory\` record detail | Reuse | Add focus lifecycle and simplify default filter exposure |
| Overview filter drawer | Reuse interaction pattern | Adopt semantics/focus handling for donor filters |
| Profile financial/news/grant sections | Reuse conditionally | Lazy load and render only for actual linked profile |
| Existing relationship data | Reuse | Prefer ranked table/list first; only show Sankey under readable thresholds |

## 19. Components Requiring Refactoring

| Area | Required refactor |
|---|---|
| \`App.tsx\` | Split page composition, directory mode coordination and all three modals into focused components; retain behaviour during transition |
| Routing/state | Introduce one typed parse/serialise grant-scope contract; route/back tests before path changes |
| Profile cards | Semantic controls, less tag density, explicit profile/coverage state |
| Source funder detail | URL-addressable selection or controlled sheet state, accessible dialog lifecycle, ranked relationships |
| Directory filters | Convert permanent desktop sidebars to active chips plus advanced drawer; do not remove any API filter |
| Modal system | Shared accessible sheet/modal primitive with role, focus trap, Escape, return focus and responsive mode |
| CSS | Consolidate overlapping responsive media rules into component-owned breakpoints/tokens after visual parity is protected |
| Charts | Code-split Recharts/Sankey and use a data-table fallback |

## 20. API and Query Changes Required

### APIs that can remain initially

- \`GET /api/charities/grants/overview\`
- \`GET /api/charities/grants/overview/trends\`
- \`GET /api/charities/grants/funders\`
- \`GET /api/charities/grants/funders/{source_funder_key}\`
- \`GET /api/charities\` and profile-detail endpoints
- Registry page/detail endpoints

### Changes required for a fast, safe unified experience

1. Define a typed donor-result response or versioned extension that explicitly includes \`record_type\`, identity method, source provenance, observed metrics, and **zero/one/many** profile-link evidence. Do not flatten profile fields onto source-only rows.
2. Push source-funder aggregation down to indexed SQL/derived aggregation where feasible, or precompute/cache by canonical scope. Avoid parsing all GB-country JSON rows on each request.
3. Cache or materialise cold trend aggregates by the same filter dimensions that the overview supports. Invalidate based on data-load/version, not a vague timeout alone.
4. Let source-funder detail query one source identity within the already-scoped aggregate rather than recomputing every result row. Add a small detail cache keyed by scope and source-funder key if measurements justify it.
5. Return explicit amount-policy metadata: automatic EUR conversion coverage, selected original-currency scope, and excluded multi-country amount. This avoids a UI having to infer totals.
6. Bound/lazy-load profile grant tables, relationships, scores and news. The four initial profile requests should become summary first, then user-requested sections.
7. Preserve registry endpoint independence. If an endpoint offers registry observed evidence, return the link method/status/confidence so it cannot be mistaken for a direct donor record.
8. Existing funder list/detail and profile-grant responses already expose publisher \`source_url\` values and source record IDs. Add a small typed response extension for raw 360Giving \`recipients[].self\` / \`funders[].self\` links and \`recipientOrganization[].url\` / \`fundingOrganization[].url\` values; the browser must not parse arbitrary raw JSON. A later structured \`link_kind\`/validation field would prevent publisher data, 360Giving JSON, an observed organisation website and a profile website being confused.

No database migration is required merely to introduce UI changes, but a durable source-funder identity/link table or materialised aggregation may be justified for performance and auditable linkage. It must include source, source identifier/name fallback, data-load version and evidence—not only a normalised display name.

## 21. Risks and Possible Regressions

### Data integrity / meaning

1. **False donor profiles:** merging source-funder rows with enrichment by name or non-null foreign key can show profile data for an entity that is not proven to be the observed funder.
2. **Registry misrepresentation:** treating a Charity Commission row or an accepted profile link as observed-donor evidence changes the meaning of the official register.
3. **Geography confusion:** donor HQ, registered office, stated geographic focus and grant beneficiary country are different attributes. The current profile “Beneficiary Geography” label is already ambiguous.
4. **Currency-total drift:** changing automatic-EUR, selected-original-currency or multi-country treatment without a shared policy breaks comparability.
5. **Identity collapse/duplication:** normalised-name fallback can split or merge source funders; multiple directory/registry profiles cannot be silently resolved for UI convenience.

### Product and functional regressions

- A new donor list that drops source-only rows would hide the majority of observed grant evidence.
- A new profile-only list would lose recipient counts, country-attributable grant totals and map continuity.
- Moving filters into drawers can lose advanced research capability unless every currently supported server parameter is preserved and tested.
- Replacing modals with a panel/page can break list/filter back state unless the selection and filters are URL-backed.
- Retiring registry navigation before parity would remove the only complete official-register search.

### Performance regressions

- Making slow GB source-funder aggregation the default landing query without backend optimisation will produce a visibly slow primary product flow.
- Querying all profile detail sections on every selection compounds API/render work.
- Loading map/chart libraries into the initial bundle slows first interaction, especially mobile.

## 22. Open Product Decisions

These decisions should be made before implementation, because each changes wording, states and test assertions:

1. Does “verified profile” mean a successful direct \`charities\` join, a manually reviewed funder-profile linkage, or only a registry exact-identifier record? Recommended current label: **Linked profile**.
2. Is “Donor Directory” intended to mean observed source funders only (recommended), or should it also support an explicitly separate “directory profiles” research mode?
3. What is the canonical user-facing amount policy: automatic historical EUR by default, selected original-currency-only scope, and explicit multi-country exclusion (the current implementation), or another policy?
4. Which of the 364 enriched profiles should be eligible for a donor-directory link if there is no direct observed grant link? Recommended: none until evidence is recorded.
5. Should a donor-detail selection be a shareable URL state? Recommended: yes after the base grant-scope contract is stable.
6. Should the map’s illustrative headquarters-to-beneficiary arrows remain? Recommended: only as an explicitly labelled optional view; never as a confirmed money route.
7. Is the AI news feature a core discovery surface or a secondary profile-research capability? The answer determines whether it belongs in details or a separate module.
8. Should the product surface every available evidence link or only show it in donor details? Recommended: details only, with “View 360Giving recipient/funder record” for the stored raw organisation JSON URL, “Visit recipient/funder website” when supplied in the observed grant, “View publisher’s grant data” for shared dataset/API links, and a separately labelled profile website when one is linked.

## 23. Recommended Implementation Sequence

This sequence fits the actual repository and preserves the existing modes until parity is demonstrated.

### Phase 1 — Terminology, evidence and state contract

- Define source funder, linked profile, source-only and registry-record terminology.
- Define the typed canonical grant-scope parse/serialise contract, including data sources.
- Add count and semantic regression tests for current dataset/linkage behaviour.
- Fix the current source-selection round-trip gap without redesigning the UI.

### Phase 2 — Unified observed Donor Directory alongside current modes

- Build on \`SourceFunderResults\` data/API semantics.
- Introduce status filtering based on explicit link evidence.
- Use compact default rows and an advanced filter drawer.
- Keep Organization Profiles and Registry modes unchanged/reachable while testing parity.

### Phase 3 — Shared evidence-aware donor details

- Introduce accessible desktop panel/tablet sheet/mobile full-screen treatment.
- Lazy-load relationships, profile facts, score, financial history and news.
- Use ranked relationship list first; retain Sankey as optional progressive enhancement when readable.

### Phase 4 — Map connection and navigation

- Make map-to-donor scope/back navigation fully URL-round-trippable.
- Add country/programme/time/source transfer tests and browser history tests.

### Phase 5 — Position the registry correctly

- Reframe Full Charity Commission Register as Advanced Registry Search.
- Retain its complete record and filters; add explicit relationship labels.

### Phase 6 — Consolidate only after parity and optimise

- Redirect/retire duplicated navigation only after acceptance criteria pass.
- Optimise donor aggregation/trend cache and code-split heavy visualisations.
- Consolidate CSS breakpoints and complete manual responsive/accessibility validation.

## 24. Acceptance Criteria for the Future Redesign

The redesign should not be accepted until all are true:

1. A donor-list row declares whether it is an observed source funder, a linked profile, or a source-only record.
2. Source-only donors never display fabricated profile contact/HQ/type/financial data.
3. Profile and registry linkage display record type, source, match method/status/confidence where applicable.
4. Every outbound data-evidence link has an accurate label: 360Giving recipient/funder record, observed recipient/funder website, publisher grant data, or linked-profile website/data source. No publisher/API URL is presented as a recipient website or unique grant page without evidence.
5. Map country/programme/time/currency/donor/recipient/source state survives Explore donors, browser Back, refresh and shared URL.
6. Beneficiary geography is never substituted with HQ, registered office or stated focus.
7. Original amount, historical EUR conversion coverage and multi-country treatment are consistent between map, donor list and details.
8. All current Main Donors, profile and registry fields remain either visible by default, available in details, or available in advanced search as documented in the preservation matrix.
9. Registry search remains complete, cursor-paginated and visibly distinct from observed donors.
10. Donor aggregation meets an agreed latency budget for large countries before it becomes the primary navigation destination. Suggested target: a cached/typical result under 1 second and a cold result with useful progress under 2 seconds, measured end-to-end.
11. Detail loads initial identity/summary first and lazy-loads secondary data; no unbounded relationship/grant payload is fetched by default.
12. Keyboard users can operate tabs, map, search suggestions, rows, drawers and all detail views with correct focus restoration.
13. Desktop, 1280 laptop, 1024 landscape tablet, 834 portrait tablet and 390 mobile are manually tested for overflow, labels, touch targets, sheets/panels and loading state.
14. Automated tests cover canonical identity, unresolved foreign IDs, zero/one/many links, registry-link evidence, currency/multi-country policy, source-link labels, pagination, empty states and URL round trips.

## 25. Evidence Appendix

### Repository state

At audit time the worktree was not clean. Existing modified/untracked files included \`README.md\`, \`frontend/src/App.tsx\`, \`frontend/src/components/GrantWorldMap.tsx\`, \`frontend/src/components/OverviewDashboard.tsx\`, \`frontend/src/index.css\`, multiple BFF/data/test files, and the untracked \`frontend/src/components/SourceFunderResults.tsx\`. This report does not attribute those changes to a particular implementation decision beyond describing their current observable code.

### Key source locations

| Area | Source location |
|---|---|
| App navigation, map hand-off, modes, list/modals | \`frontend/src/App.tsx:410-484, 585-894, 1047-1114, 1442-1564, 1790-2147, 2365-2842\` |
| Overview URL/requests | \`frontend/src/components/OverviewDashboard.tsx:145-257\` |
| World-map behaviour/accessibility | \`frontend/src/components/GrantWorldMap.tsx:116-124, 250-295, 353-360, 448-513, 598-653\` |
| Observed donor list | \`frontend/src/components/SourceFunderResults.tsx:12-452\` |
| Registry view | \`frontend/src/components/RegistryDirectory.tsx:81-321\` |
| Responsive styling | \`frontend/src/index.css:868-888, 3007-3069\` |
| BFF routes | \`src/bff/charity.py:24-146, 303-470\` |
| Overview warm-up | \`src/bff/main.py:51-68\` |
| Source-funder aggregation/details | \`src/bff/repositories.py:1716-2151\` |
| Registry query | \`src/bff/repositories.py:2918-3137\` |
| Directory profile list query | \`src/bff/repositories.py:3220-3419\` |
| Direct profile grant / relationships | \`src/bff/repositories.py:4372+\` |
| Core/derived schemas | \`src/data/db_loader.py:67-113, 229-259\` |
| Registry/link schemas | \`src/data/registry.py:82-128\` |

### Current local data counts used in this audit

| Measure | Count / value |
|---|---:|
| Grants | 200,000 |
| Grant source | 360Giving only |
| Source-funder identities | 292 |
| Source-recipient identities | 75,760 |
| Enriched directory profiles | 364 |
| Current Charity Commission registry records | 397,469 |
| Accepted registry links | 343 |
| Distinct profiles with accepted registry link | 64 |
| Profiles with direct observed grant link | 17 |
| Grants with usable country geography | 71,286 (35.64%) |
| Grants with programme category | 200,000 |
| Grants with valid EUR conversion | 199,579 |
| Grants without valid conversion | 421 |
| Grants with valid stored HTTP(S) source URL | 200,000 |
| Distinct grant source URLs | 309 |
| Raw grants with 360Giving recipient JSON URL | 183,947 |
| Raw grants with 360Giving funder JSON URL | 200,000 |
| Raw grants with publisher-provided recipient website | 72,906 |
| Distinct publisher-provided recipient websites | 35,841 |
| Raw grants with publisher-provided funder website | 3,594 |
| Profile records with a stored source URL | 361 |
| Overview cache rows | 37 |

### Documentation drift

\`README.md\` still describes an earlier 100,000-grant / 39,018-mapped state in several locations (including lines approximately 12, 71-72, 127, 141 and 149). The current database is 200,000 grants and 71,286 mapped beneficiary-country grants. Any user-facing or operational documentation should be refreshed as a separate, deliberate change so dataset claims do not drift again.

### Test/build record

    PYTHONPATH=src ./venv/bin/python -m pytest -q
    242 passed, 39 warnings in 12.70s

    cd frontend && npm run build
    Build succeeded; Vite emitted a >500 kB bundle warning.

    cd frontend && npm run lint
    Exit 0; five existing react-hooks/exhaustive-deps warnings.

No new screenshots were captured because browser automation was not installed in this environment.
