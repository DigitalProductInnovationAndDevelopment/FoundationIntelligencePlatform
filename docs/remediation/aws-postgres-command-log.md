# AWS/PostgreSQL Remediation Command Log

Commands are recorded in execution order. Secret values are never recorded. Exit codes are included when known.

## Phase 0

### Initial branch and working tree

```zsh
pwd
git status --short --branch
git branch --show-current
git rev-parse HEAD
git log -5 --oneline
git remote -v
git diff --stat
git diff
git diff --cached
```

Result: exit 0. Correct branch `91-clean-up-code-for-aws-integration`, HEAD `408eb879b05ec4d2caf92d9bbd782dda9b290e23`, no tracked diff, `docs/audits/` untracked.

### Immutable audit verification

```zsh
find docs/audits -type f -print | LC_ALL=C sort
find docs/audits -type f -exec stat -f '%z %N' {} \; | LC_ALL=C sort -k2
find docs/audits -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256
git status --short -- docs/audits
find docs/audits -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.env' -o -name '*.tfstate*' \) -print
rg -n --hidden 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]+ PRIVATE KEY-----' docs/audits || true
find docs/audits -type f | wc -l
```

Result: exit 0. Exactly 16 untracked files; no prohibited data/state file; no high-confidence secret-pattern match. Paths, sizes and hashes are in `aws-postgres-baseline.md`.

### Repository modifications

The first repository modification created only the four required remediation ledger/contract documents under `docs/remediation/`. No application, infrastructure or immutable audit file was changed.

### Source database safety and coherent backup

```zsh
df -h . /private/tmp
stat -f 'path=%N size=%z modified=%Sm' -t '%Y-%m-%dT%H:%M:%S%z' src/data/charities.db
shasum -a 256 src/data/charities.db
sqlite3 -readonly src/data/charities.db "SELECT key, value FROM metadata WHERE key IN ('schema_version','registry_schema_version','grant_overview_schema_version') ORDER BY key;"
sqlite3 -readonly src/data/charities.db 'PRAGMA quick_check;'
sqlite3 -readonly src/data/charities.db ".backup '/private/tmp/fip-remediation-baseline-20260728.db'"
stat -f 'path=%N size=%z modified=%Sm' -t '%Y-%m-%dT%H:%M:%S%z' /private/tmp/fip-remediation-baseline-20260728.db
shasum -a 256 /private/tmp/fip-remediation-baseline-20260728.db
sqlite3 -readonly /private/tmp/fip-remediation-baseline-20260728.db 'PRAGMA integrity_check;'
sqlite3 -readonly /private/tmp/fip-remediation-baseline-20260728.db 'PRAGMA foreign_key_check;'
```

Result: exit 0. Source quick check and backup integrity are `ok`; zero FK violations. Source and coherent-backup SHA-256 values are recorded in the baseline.

### Database reconciliation baseline

Read-only SQL was executed on `/private/tmp/fip-remediation-baseline-20260728.db` for table counts, distinct mapped grants, source identity duplicates, conversion eligibility/total, anomaly cohorts, exact business-key duplicate groups, duplicate charity numbers, currency/conversion status, programme provenance and geography methods.

Result: exit 0. All mandatory controls match the immutable audit. The exact business key is `(funding_name, recipient_name, amount, currency, date, description)` and reproduces 4,271 groups / 14,529 extra rows. Classified count 134,554 is the non-unclassified cohort intersected with non-negative, EUR-convertible grants.

### Backend and frontend baseline

```zsh
venv/bin/python -m compileall -q src
venv/bin/python -m flake8 src --count --select=E9,F63,F7,F82 --show-source --statistics
PYTHONPATH=src venv/bin/python -m pytest src/tests --cov=bff --cov-report=term-missing --cov-fail-under=70
cd frontend && npm ci
cd frontend && npm run test
cd frontend && npm run lint
cd frontend && npm run build
```

Result: all commands exit 0. Backend 286 passed, 76.29% coverage, 55 deprecation warnings. Frontend 8 passed; lint has five hook warnings; build has a 1.96 MB main JavaScript chunk warning.

### Architecture/config inventory

Read-only `sed`, `find` and `rg` commands inspected FastAPI main/auth/config/proxy/admin/charity/news routes, Dockerfile, Compose, CI, dependency manifests, environment examples, source trees, SQLite-specific constructs and local background-process/file coordination.

Result: current branch has local shared-secret/basic admin authentication, static credentials in frontend example, broad router authentication, unrestricted authenticated proxy forwarding, synchronous SQLite and filesystem jobs. These are remediation inputs, not accepted target controls.

### Clean target-branch runtime baseline

The old no-reload backend process from the prior branch was first queried accidentally, then explicitly stopped. The frontend process was also stopped. Clean target-branch processes were started with:

```zsh
./start_backend.sh
cd frontend && npm run dev -- --host 127.0.0.1 --strictPort --port 5173
```

Local `curl` checks then recorded health, OpenAPI counts, frontend reachability and anonymous charity/admin/proxy responses. A local Python request used configuration internally without printing credentials to verify login and cached API timing.

Result: clean target branch has 33 paths/38 operations; health/frontend 200; anonymous protected routes 401; login 200; cached overview 21.3 ms then 4.2 ms; registry FTS 808.9 ms.

### Legacy Docker baseline

```zsh
DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker info --format 'server={{.ServerVersion}} arch={{.Architecture}}'
DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker-compose build bff
df -h . /private/tmp
DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker system df -v
```

Result: Docker server 23.0.5/aarch64. Build transferred an 8.81 GB context, installed unpinned dependencies, then failed at `COPY src/` with `no space left on device` (exit 1). Host free space fell from 69 GiB to 51 GiB; existing prior image is 9.37 GB and build cache reports 12.74 GB. No user-owned Docker artifact was deleted.

### Baseline staging review

The first `git diff --cached --check` exited 2 because the immutable audit Markdown contains intentional two-space CommonMark hard breaks. The audit files were not changed. The same check also identified removable whitespace in newly created remediation documents; those new documents were corrected and restaged. Future commits will pass `git diff --check` because the immutable audit is established in this dedicated baseline commit.
