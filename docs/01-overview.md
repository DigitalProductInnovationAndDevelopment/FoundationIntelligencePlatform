# 1. Overview

## Purpose

The platform supports exploration of philanthropic foundations, charities and the grant
relationships observed between them. It addresses questions such as which organizations
funded work in a given country, what a funder has given and to whom, and how an
organization compares against a user-defined target profile.

The platform does not perform prediction. It does not estimate whether an organization
will donate, and it does not infer activity that is absent from source data.

A Python/FastAPI backend serves an async PostgreSQL datastore to a React/Vite single-page
application. An offline pipeline consolidates cached source data, enriches it
deterministically, and loads it into PostgreSQL as versioned datasets.

## Separation of value categories

Four categories of value are stored and displayed separately and are not merged. This
distinction is applied throughout the schema, the API and the user interface.

| Category | Example | Definition |
|---|---|---|
| Source fact | `programme_areas_source` | Present in the source record; normalized but not inferred |
| Deterministic inference | `programme_areas_inferred` | Derived by versioned rules, with evidence and confidence |
| Platform-derived | Relevance score, analytics aggregates | Computed from the above; versioned and explainable |
| Absent | `transaction_data_unavailable` | Explicitly unknown; not rendered as zero |

The same separation applies to geography. The location where an organization is based is
not substituted for the location where its grant funding was directed. See
[4. Domain rules](04-domain-rules.md).

## Data sources

| Source | Volume in the shipped cache | Description |
|---|---|---|
| Charity Commission (England & Wales) | 397,469 registry rows; 65 normalized UK-side organizations | Official UK register |
| 360Giving | 302,546 observed grant transactions | Published UK grant data, sampled; not complete coverage |
| Philea | 299 member organizations | European membership directory; organization metadata only, no grants |

The reconciled active dataset contains 373 enriched organizations, 302,546 grants across
GBP, USD, EUR, ILS and CHF, and 345 accepted registry-to-profile links.

Each raw record retains its source name, source record ID, source URL where supplied,
ingestion timestamp and raw payload, so that any displayed value can be traced to its
origin.

## Capabilities

| Capability | Status | Limitation |
|---|---|---|
| Organization directory and detail | Complete | PostgreSQL-backed |
| Charity Commission registry search | Complete | Cursor-paginated, capped at 100 rows per request |
| Grant list, network summary, Sankey | Complete | Observed 360Giving transactions only |
| Beneficiary world map | Complete | Displayed above a coverage threshold; coverage reported per response |
| Programme and geography enrichment | Complete | Deterministic and versioned; accuracy not externally validated |
| Monthly awards and programme allocation | Complete | Auto mode uses historical ECB-converted EUR |
| PostgreSQL migration and cutover | Complete locally | Exact reconciliation; the controlled cutover procedure has not been executed against AWS |
| DACH foundation intelligence | Partial | Organization-level discoverability only |
| News summary | Partial | Requires credentials and network access; approval-gated |
| Offline dashboard fallback | Mocked | Labelled prototype values; grant, map and score data are not fabricated |
| Relevance score | Experimental | Example weights; no client approval; not a prediction |
| Complete DACH grant transactions | Missing | No source currently supplies this |
| Enrichment predictive accuracy | Not verified | Coverage is measured; labelled ground truth does not exist |
| Client-approved score definition | Blocked | No approved target, weights or decision policy exists in the repository |

## Limitations

- The dataset is a bounded proof-of-concept snapshot. It is not a comprehensive UK, DACH
  or European foundation database, and coverage claims should not extend beyond the table
  above.
- 360Giving ingestion is a sample. The absence of a grant record does not indicate the
  absence of funding.
- Philea contributes organization metadata only. No activity is inferred from membership.
- Enrichment coverage is measured; accuracy is not. No labelled ground-truth set exists.
  Evidence and review flags should remain visible in any interface built on this data.
- The relevance score is an illustrative example. Its weights carry no client approval and
  it should not be presented as an indication of donation likelihood.
- The news route depends on live external pages and credentials, and is therefore neither
  deterministic nor reproducible.
- The system has been deployed to AWS once, as a manually provisioned environment that is
  not reproducible from the infrastructure code and has no monitoring. It has not been run
  under production load. See [6. Deployment and status](06-deployment-and-status.md).
