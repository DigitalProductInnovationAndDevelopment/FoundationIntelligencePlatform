# Data Governance Register

Status: Phase-9 local governance baseline. Policy status is `proposed`.
No legal, licence, privacy or production approval is inferred by this file.

## Owner register

| Role | Owner | Status |
|---|---|---|
| Business owner | unassigned | unresolved |
| Data owner | unassigned | unresolved |
| Privacy owner | unassigned | unresolved |
| Legal owner | unassigned | unresolved |
| Security owner | unassigned | unresolved |
| Technical owner | data-platform | assigned role, named person unresolved |

Unassigned owners block production approval. They are not replaced with the
developer, repository author or cloud account owner by assumption.

## Source register

`config/source-pipelines.json` is the executable source register. The database
retains its checksummed projection in `source_configurations`.

| Source | Classification | Legal | Licence | Schedule |
|---|---|---|---|---|
| 360Giving | public organisation and grant data | unresolved | unresolved | disabled / blocked |
| Charity Commission | public organisation, contact and financial data | unresolved | unresolved | disabled / blocked |
| Philea | public organisation data | unresolved | unresolved | disabled / blocked |
| Hinchilla | public organisation data | unresolved | unresolved | disabled / blocked |
| ECB | public reference data | unresolved | unresolved | disabled / blocked |
| Google News RSS | article metadata | unresolved | unresolved | disabled / blocked |
| Article content | article content | unresolved | unresolved | disabled / blocked |
| Anthropic news summary | derived classification | unresolved | unresolved | disabled / blocked; paid use separately prohibited |

Terms URLs intentionally remain unset until reviewed. Database and application
constraints prohibit enabling a source while legal/licence status is
unresolved or a governance block is present.

## Classification and exposure register

| Classification | Exposure | Proposed archive dry-run | Destructive window |
|---|---|---:|---|
| Public organisation data | authenticated | 365 days | unset |
| Contact data | restricted | 90 days | unset |
| Personal email addresses | restricted | 30 days | unset |
| Postal addresses | restricted | 90 days | unset |
| Raw source evidence | internal | 30 days | unset |
| Article metadata | authenticated | 30 days | unset |
| Article content | internal | 1 day | unset |
| Pipeline logs | restricted | 30 days | unset |
| Exports | restricted | 7 days; expiry report at 7 days | unset |
| Audit events | restricted | 365 days | unset |
| Derived classifications | authenticated | 365 days | unset |
| Enriched profiles | authenticated | 365 days | unset |
| Credentials | never | unset | unset |
| User identities | restricted | 90 days | unset |

Archive windows create reports only; they are proposed operational review
points, not deletion approval. Holds override every window.

## Field-level exposure

Response models already name fields for typed domain endpoints. Generic
administrative dictionaries now additionally use policy allowlists:

- health output: `status`, `service`, `checks`;
- organization summary: named public/analytical identity fields only;
- job history: lifecycle metadata only, excluding input and actor identity;
- job events: bounded event metadata plus recursively redacted details;
- source configuration: operational schedule fields, excluding credential
  references and user-agent detail.

There is no fallback policy that serializes every database column. An unknown
exposure policy raises an error.

## Privacy checklist

| Control | Status |
|---|---|
| Lawful-basis review | unresolved |
| Data protection impact assessment | not started |
| Data-subject contact | unassigned |
| Source terms review | unresolved |
| International-transfer review | unresolved |
| Processor register | not started |
| Breach-response owner | unassigned |

These unresolved items keep the overall production decision at `NO-GO`.
