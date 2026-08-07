# Data Retention and Deletion Candidates

Audit date: 2026-07-28

## Safety statement

No domain data, database row, raw source artifact or user-owned change was deleted during this audit. Candidates are classifications for review, not deletion instructions. Every deletion requires an approved retention policy, an identified owner, a recoverability check and explicit authorization.

## Data that must be preserved

| Data | Why preserve it | Risk if removed | Recommended target |
|---|---|---|---|
| `src/data/charities.db` active database | Current integrated application state and audit baseline. | Irrecoverable service/data loss. | Keep read-only baseline until PostgreSQL reconciliation and rollback window close; then archive encrypted under policy. |
| Raw 360Giving, Charity Commission, Philea and other source artifacts | Source evidence, reproducibility and reprocessing. | Cannot explain or rebuild derived results. | Versioned immutable S3 raw zone with checksum/manifests and lifecycle. |
| Source IDs, URLs, timestamps and evidence fields | Identity, provenance and auditability. | False deduplication/linking and unverifiable claims. | Preserve in operational schema and curated history. |
| Enrichment method, confidence, review flags and rule versions | Explains geo/programme classifications. | Silent model/rule drift. | Preserve with each derived record/version. |
| Link overrides and profile-cache revision history | Operator decisions and correction trail. | Repeated bad matching or unexplained profile changes. | Transactional table with actor/time/reason audit record. |
| Negative and zero grants | Can represent corrections, reversals or valid source semantics. | Incorrect totals and loss of source fidelity. | Retain; explicitly classify and exclude/include according to documented metric rules. |
| Duplicate business-key groups | Unique source IDs indicate possible legitimate tranches/awards. | Accidental loss of distinct grants. | Keep until source-aware adjudication. |
| Registry rows sharing a charity number | Often constituent funds or linked registrations. | Directory records and legal identities lost. | Keep; model charity-number relationship rather than deduplicate destructively. |
| Removed registry records | Historical/legal status evidence. | Incorrect longitudinal and due-diligence results. | Archive/retain according to source and legal policy; do not treat `Removed` as deletable. |

## Archive candidates

| Candidate | Current observation | Archive approach | Risk / prerequisite | Approval |
|---|---|---|---|---|
| Superseded coherent SQLite snapshots | Each is about 2.10 GB. | Compressed encrypted S3 archive with checksum, schema/fact versions and expiry. | Must retain at least the accepted rollback/PITR horizon. | Data owner + platform owner. |
| `src/data/processed/charity_commission_register.sqlite3` | About 1.70 GB, reproducible registry cache. | S3 versioned processing artifact; rebuild manifest required. | Removing local copy breaks current workflows until S3 access is implemented. | Data pipeline owner. |
| Large processed/pilot JSON and JSONL batches | Hundreds of MB; reproducible but useful for provenance/rebuild. | Convert curated analytical history to Parquet; retain raw separately. | Validate record counts/checksums and loader before local cleanup. | Data owner. |
| Pipeline logs/status history | Local files provide limited operational evidence and may contain contact data. | Short-lived redacted CloudWatch logs; immutable run summary separately. | Privacy/incident requirements determine retention. | Security/privacy + operations. |
| Generated exports | Potentially contain filtered organization/contact data. | Encrypted per-job S3 prefix with short TTL and access log. | Must not be indefinitely public/cacheable. | Product/data owner. |
| Audit screenshots and Markdown reports | Small, useful evidence, no secret observed. | Keep in Git if approved; screenshots are loading/performance evidence. | Recheck screenshots before publishing externally. | Repository owner. |

## Quarantine and review candidates

These records should remain stored but be excluded or flagged in affected metrics until reviewed.

| Candidate | Count / evidence | Reason | Suggested action |
|---|---:|---|---|
| Future-dated grant | 1: `360G-TheSeafarersCharity-1756`, 2026-10-30, GBP 50,000 | Could be a planned award or source-date error. | Compare with source; expose a future-date flag. |
| Negative grant amounts | 2; minimum −10,000 | Likely corrections/reversals; overview already excludes them from the non-negative total. | Add transaction type/correction semantics and regression totals. |
| Zero grant amounts | 2,101 | May be missing/cancelled/non-monetary. | Classify by source fields; do not delete by amount alone. |
| Missing EUR conversions | 432 | Cannot participate in EUR totals. | Backfill only from approved rate policy; preserve original currency/amount. |
| Blank/unknown curated source provenance | 8 organizations | Weak auditability. | Repair from source records or mark explicitly unknown. |
| Negative registry income | 2 rows | May be corrections/source quality. | Confirm against Charity Commission source; flag rather than clamp. |
| Exact business-key duplicate grant groups | 4,271 groups, 14,529 rows beyond one/group | May be repeated source exports or legitimate distinct awards. | Compare source award identifiers and transaction semantics. |
| Low-confidence/review programme or geo records | 72 low-confidence programme; hundreds of review flags | Analysis may overstate certainty. | Human review queue and coverage thresholds. |
| Invalid programme source labels | 234,774 | Indicates normalization mismatch or broadly applied flag. | Investigate rule/label vocabulary before PostgreSQL baseline is signed. |

## Potential technical deletion candidates

These are not domain-data deletion candidates. They can be regenerated, but none was automatically removed except the ephemeral test container `fip-audit-prodlike`, which was created solely for this audit and then stopped/removed after verification.

| Candidate | Location / size | Recoverability | Risk | Required approval / safe precondition |
|---|---|---|---|---|
| Audit staging DB | `/private/tmp/fip-audit-staging-20260728.db`, 2.10 GB | Recreate using SQLite `.backup` from the active DB. | Removes the tested migration copy and its evidence. | Repository/data owner after reports are accepted. |
| Audit restore DB | `/private/tmp/fip-audit-restore-20260728.db`, 2.10 GB | Recreate from staging/source snapshot. | Removes restore evidence. | Same as above. |
| Incomplete isolated virtualenv | `/private/tmp/fip-audit-venv` | Fully reproducible; install failed before dependencies downloaded. | Minimal. | Audit owner after acceptance. |
| Headless Chrome profiles | `/private/tmp/fip-chrome-audit-*` | Reproducible. | Could contain only local session/cache state; inspect ownership first. | Audit owner. |
| Temporary API JSON responses | `/private/tmp/fip-api-*.json`, `/private/tmp/fip-error-*.json` | Reproducible from local read-only calls. | May contain public organization detail/contact fields. | Audit owner; remove after report acceptance. |
| Temporary Docker config | `/private/tmp/fip-audit-docker-config/config.json` containing `{}` | Reproducible. | None; no credentials stored. | Audit owner. |
| Local Docker image | `foundationintelligenceplatform-bff`, 9,368,380,422 B | Rebuildable, though build is slow and unpinned. | Frees substantial disk; deletion removes reproducibility evidence. | Repository owner after Docker finding accepted. |
| Frontend `dist/` | About 2 MB, ignored | Rebuild with `npm run build`. | None if dependencies remain available. | Developer/operator. |
| `.coverage`, `.pytest_cache`, `__pycache__` | Local test artifacts, ignored | Recreate with tests. | None. | Developer/operator. |
| `node_modules` | Installed dependency tree, ignored | Recreate with `npm ci`; network required. | Offline rebuild unavailable. | Developer/operator when network/lockfile available. |

## Not deletion candidates without a separate investigation

- The 4,271 business-key duplicate groups and 9,073 duplicate charity-number groups.
- Removed registry organizations.
- Organizations without grant data, including the 299 Philea records.
- Negative/zero financial rows and grants.
- Raw source snapshots or source evidence.
- Active link overrides, cached profiles or audit run metadata.

## Required governance before automated lifecycle

1. Assign data owner and system owner per dataset.
2. Record legal basis, source licence/terms and permitted redistribution.
3. Classify contact/address/article data and define data-subject handling.
4. Define active, archive, quarantine and deletion periods by dataset.
5. Define litigation/incident holds and backup/PITR interaction.
6. Test restore before enabling expiration.
7. Require two-person approval for production bulk deletion and retain an immutable deletion manifest.
