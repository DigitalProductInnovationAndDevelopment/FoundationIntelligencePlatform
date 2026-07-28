# AWS/PostgreSQL Remediation Baseline

Created: 2026-07-28

Target branch: `91-clean-up-code-for-aws-integration`

Starting revision: `408eb879b05ec4d2caf92d9bbd782dda9b290e23`

## Safety contract

- `src/data/charities.db` is a read-only migration source.
- `docs/audits/` is an immutable audit baseline.
- AWS resources, DNS, certificates, ECR, S3 uploads, RDS migrations, paid APIs, `terraform apply` and `terraform destroy` are prohibited without a later explicit approval.
- Local scoped commits are authorised. A Git push is not authorised until a later explicit confirmation.
- No existing uncommitted change may be discarded.

## Initial Git state

- Working directory: `/Users/manuelgrabmayer/netlight - github/FoundationIntelligencePlatform`
- Branch: `91-clean-up-code-for-aws-integration`
- Upstream: `origin/91-clean-up-code-for-aws-integration`
- HEAD: `408eb879b05ec4d2caf92d9bbd782dda9b290e23`
- Initial tracked diff: none.
- Initial untracked content: `docs/audits/` only.

## Immutable audit baseline

Exactly 16 files were found. No SQLite/database file, environment file, Terraform state, raw dataset or browser profile directory is present. A high-confidence private-key/token-pattern scan returned no match.

| SHA-256 | Bytes | Path |
|---|---:|---|
| `21c75790c91fa7fcf401c66b1df38546645cc62ac9475b29ea8c0bae93a9b84d` | 17,644 | `docs/audits/audit-command-log.md` |
| `ab685f281528cf281d0bc5160e33a00057dd87f20f239ed546af63b043cb8199` | 14,636 | `docs/audits/aws-migration-plan.md` |
| `f7e02d8c65fc30a0a9d4929a678b98ee5d7d28e8480fb8381ea4dfadc466ed68` | 43,941 | `docs/audits/aws-readiness-audit-2026.md` |
| `0ff26c1acc11cb75ae9a6d86dd239ba2d22e512f722cdd2d62846ce02b6a4842` | 9,035 | `docs/audits/data-retention-and-deletion-candidates.md` |
| `d6e0c7c91e09a3c638601803de0933dd97f887420dd9347d8e733f4ca9bfd782` | 12,793 | `docs/audits/database-integrity-report.md` |
| `093abe5476c0c8687720121d7c0da28d2104a052c12c25d7fb3db3c30f1e05c6` | 26,725 | `docs/audits/feature-test-matrix.md` |
| `efd480cba706e60fbab8c15a51f5ce455f063cd6a517d0d3757d6ae19b5d9ad9` | 4,092 | `docs/audits/performance/runtime-measurements.md` |
| `4ba7216a7e84d0c8a3e00d47963d2d9178fcd32d346d940eeb62b96ef512a75b` | 15,646 | `docs/audits/requirements-traceability.md` |
| `a8438ed1e4e67a807182b96248cecd725c8e3438811f3ef2c551cd5d47f6d61a` | 75,230 | `docs/audits/screenshots/donor-directory-1440x900.png` |
| `f1fcaddd24733c232a832c48421754054cd3f550864bfcd19ee3f8d39c0e6dd3` | 112,667 | `docs/audits/screenshots/overview-1440x900.png` |
| `13c570b67679f5ecf94f65a0057d7c96f19ca84d1cf7992e8d543367001d4a8c` | 96,631 | `docs/audits/screenshots/overview-ipad-landscape-1024x768.png` |
| `ddb778f94c10562180a2acb791fbd8cfcc19ff62287884396abb195cda85d4bf` | 87,431 | `docs/audits/screenshots/overview-ipad-portrait-768x1024.png` |
| `c9d38dc7c2044680db9fdd423822e5ab8bdad20cd8f30747f1f8cdb57526476c` | 15,935 | `docs/audits/screenshots/overview-large-1920x1080.png` |
| `d068c1e99b1049cbf0e94984f84453c59f5b69cc4f81ca4bfaf183e2bd83610e` | 295,435 | `docs/audits/screenshots/overview-loaded-1440x900.png` |
| `70a8a3f784db8b99dd9f8bbe191d9fc835795578a0c0de8d8622211778bc6cb1` | 65,404 | `docs/audits/screenshots/overview-mobile-390x844.png` |
| `d7022a54c23ab08f68a37fbce2d1a6d6a89b5ac5e6e7e571e73ba02d05fb0067` | 114,303 | `docs/audits/screenshots/overview-warm-1440x900.png` |

The checksums above are the immutability reference for all later phase and final checks.

## Source database baseline

The source was inspected read-only. A coherent backup was created with SQLite's backup mechanism at `/private/tmp/fip-remediation-baseline-20260728.db`; it is not a Git artifact.

| Item | Verified result |
|---|---|
| Active path | `src/data/charities.db` |
| Active size / modified time | 2,100,543,488 bytes / 2026-07-28T17:23:28+0200 |
| Active file SHA-256 | `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651` |
| Active read-only quick check | `ok` |
| Coherent backup size | 2,100,543,488 bytes |
| Coherent backup SHA-256 | `609208373d9a832c6d54e5d0a6679bed801bc35c59dc11847011d3c98b4f895d` |
| Backup integrity check | `ok` |
| Backup foreign-key violations | 0 |
| Application schema version | 7 |
| Registry schema version | 1 |
| Overview fact version | `2026-07-overview-facts-v5` |
| Host free space before backup | 69 GiB |

The file and backup hashes differ because SQLite backup rebuilds page layout; logical integrity/counts are the migration acceptance evidence. The migration manifest records the coherent migration source checksum.

### Reverified control values

| Control | Value |
|---|---:|
| `charities` | 373 |
| `charity_registry_organizations` | 397,469 |
| `grants` | 302,546 |
| `grant_beneficiary_countries` | 104,309 |
| Distinct mapped grants | 104,191 |
| `grant_beneficiary_terms` | 556,719 |
| `grant_programme_categories` | 358,883 |
| `grant_overview_facts` | 302,546 |
| `grant_source_funder_facts` | 104,309 |
| `organization_registry_links` | 345 |
| Classified, conversion-eligible non-negative grants | 134,554 |
| Conversion-eligible non-negative grants | 302,112 |
| Overview total | EUR 22,435,986,707.70 |
| Duplicate source identity groups | 0 |
| Missing EUR conversions | 432 |
| Negative grants | 2 |
| Zero grants | 2,101 |
| Future-dated grants | 1 |
| Blank/unknown curated source provenance | 8 |
| Invalid programme source-label flags | 234,774 |
| Exact business-key duplicate groups / extra rows | 4,271 / 14,529 |
| Duplicate charity-number groups / extra rows | 9,073 / 40,226 |

Currency, programme-provenance and geography-method distributions also match the immutable audit baseline.

## Application baseline

| Check | Verified result |
|---|---|
| Python compile | PASS |
| Blocking Flake8 `E9,F63,F7,F82` | PASS, 0 findings |
| Backend tests | PASS, 286 tests |
| BFF coverage | PASS, 76.29% (threshold 70%) |
| Backend warnings | 55 deprecation warnings; recorded, not hidden |
| Frontend install | PASS, `npm ci`, 74 packages |
| Frontend unit tests | PASS, 8 tests |
| Frontend lint | PARTIAL, exit 0 with five React hook dependency warnings |
| Frontend build | PASS with >500 KB chunk warning |
| Main JS | 1,963.60 KB / 612.00 KB gzip |
| Clean target-branch startup | PASS: backend and frontend reachable |
| OpenAPI | 33 paths / 38 operations |
| Anonymous charity/admin/proxy access | 401 after clean target-branch restart |
| Overview measured after persistent cache | 21.3 ms first sampled call, 4.2 ms warm; not a true empty-cache result |
| Registry FTS `foundation` | 808.9 ms |
| Docker build | FAIL: 8.81 GB context; `COPY src/` ended with `no space left on device` |

An initial HTTP check accidentally reached the still-running process from the prior branch and showed 31/36 plus anonymous access. That process was stopped. The clean target-branch process produced the authoritative 33/38 and 401 baseline above.

The Docker daemon is available (`23.0.5`, aarch64), but it held a 9.37 GB prior image and 12.74 GB build cache. The failure is evidence for Phase 2, not a passed Docker gate.
