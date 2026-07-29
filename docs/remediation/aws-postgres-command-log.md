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

### Immutable baseline commit and Phase-0 closeout

```zsh
git commit -m "Document immutable AWS readiness baseline"
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256
shasum -a 256 src/data/charities.db
jq -e . docs/remediation/schemas/migration-manifest.schema.json
git diff --check
```

Result: commit `19e84ba11dd3567fc871b3411166ae59a5b6eef0` created with only the 16 immutable audit artifacts and four initial remediation documents. The final audit checksum set retained aggregate SHA-256 `d40c8b0114f8c5ef728884dd0e8632ecc6f9f03912fdf8ba709556f9ba3c1f2a`; the active database retained SHA-256 `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`. The manifest Schema parses successfully and the post-baseline working diff passes whitespace validation.

## Phase 1 — Security hardening

### Route, configuration and client inventory

Read-only `rg`, `sed`, `find`, Git and OpenAPI inspection enumerated every route, authentication dependency, proxy behavior, configuration input, frontend login/fetch path and existing test assumption. The resulting classification is `aws-postgres-route-inventory.md`.

One overly broad local environment inventory command printed values from the ignored developer `.env` into transient tool output. No value was copied into source, documentation, Git or a remote service. The user was notified immediately and rotation of the affected third-party credentials was recommended. The `.env` file was not changed.

### Security implementation and test gate

```zsh
venv/bin/python -m compileall -q src
venv/bin/python -m flake8 src --count --select=E9,F63,F7,F82 --show-source --statistics
PYTHONPATH=src venv/bin/python -m pytest src/tests --cov=bff --cov-report=term-missing --cov-fail-under=70
cd frontend && npm run test
cd frontend && npm run lint
cd frontend && npm run build
git diff --check
git grep -nE '<high-confidence credential patterns>' -- ':!docs/audits/**'
shasum -a 256 src/data/charities.db
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

Result: compile and blocking Flake8 pass with zero findings. Backend has 297 passing tests plus 8 mutation-route subtests, 53 dependency/test-client warnings and 76.69% coverage. The 11 security test methods cover OIDC signature/claim validation, 401, 403, role success, audit fields, proxy/path/header allowlists, rate limiting, required idempotency/replay, payload limits, message/traceback redaction, public read-only behavior and production startup failure. Frontend has 8 passing tests; lint exits 0 with the five baseline hook warnings; production build passes with the baseline large-chunk warning. The tracked-source credential scan has no high-confidence match.

### Default-runtime HTTP gate

The first local start attempt inside the filesystem sandbox reached application startup but could not bind the loopback port (`operation not permitted`). It was repeated with the narrowly approved local start permission. No external network or service was contacted.

```text
GET  /health                    -> 200 + X-Request-ID
GET  /api/charities             -> 401 + audit event + X-Request-ID
GET  /api/admin/pipeline/status -> 401 + audit event + X-Request-ID
GET  /api/core/v1/data          -> 401 + audit event + X-Request-ID
POST /api/auth/login            -> 404 + audit event + X-Request-ID
```

Result: the application starts with authentication disabled, exposes only the public read surface, rejects protected/admin/proxy access and hides the local login until the development bypass is explicitly configured. The local process was stopped cleanly after the checks.

### Phase-1 immutability and external-action check

The active SQLite source retained SHA-256 `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`; the immutable audit checksum aggregate retained `d40c8b0114f8c5ef728884dd0e8632ecc6f9f03912fdf8ba709556f9ba3c1f2a`. No AWS mutation, paid API call, external upload, push or Docker artifact deletion occurred.

## Phase 2 — Docker and local PostgreSQL foundation

### Explicit registry authorization and dependency locks

On 2026-07-29 the user explicitly authorized downloads only from PyPI,
`files.pythonhosted.org`, `registry.npmjs.org` and the Docker Hub registry/auth
endpoints. AWS, live scrapers, paid APIs, uploads and pushes remained forbidden.

```zsh
venv/bin/python -m pip install --dry-run --ignore-installed --only-binary=:all: --report /private/tmp/fip-pip-tools-resolve.json pip-tools==7.5.2
venv/bin/python -m pip install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r requirements-locking.txt
venv/bin/pip-compile --index-url=https://pypi.org/simple --resolver=backtracking --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --output-file=requirements-runtime.txt requirements-runtime.in
venv/bin/pip-compile --index-url=https://pypi.org/simple --resolver=backtracking --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --output-file=requirements.txt requirements.in
```

The first compile attempts failed before modifying either output because
`pip-tools==7.5.2` is incompatible with `pip==26.1.2`
(`PackageFinder.allow_all_prereleases` is absent). The correction was resolved,
hashed and installed explicitly:

```zsh
venv/bin/python -m pip install --dry-run --ignore-installed --only-binary=:all: --report /private/tmp/fip-pip-tools-resolve-pip25.json pip==25.3 pip-tools==7.5.2
venv/bin/python -m pip install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r requirements-locking.txt
venv/bin/pip-compile --index-url=https://pypi.org/simple --resolver=backtracking --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --output-file=requirements-runtime.txt requirements-runtime.in
venv/bin/pip-compile --index-url=https://pypi.org/simple --resolver=backtracking --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --output-file=requirements.txt requirements.in
venv/bin/python -m pip install --index-url https://pypi.org/simple --require-hashes -r requirements.txt
venv/bin/python -m pip check
cd frontend && npm ci --ignore-scripts --registry=https://registry.npmjs.org
```

Result: both Python lockfiles contain exact transitive versions and accepted
SHA-256 artifact hashes. The npm lock contains exact tarball integrity values;
its only resolved host is `registry.npmjs.org`. `pip check` reports no broken
requirements. The authoritative version/digest inventory is
`aws-postgres-dependency-locks.md`.

### Local application checks

```zsh
venv/bin/python -m compileall -q src
venv/bin/python -m flake8 src --count --select=E9,F63,F7,F82 --show-source --statistics
PYTHONPATH=src venv/bin/python -m pytest src/tests --cov=bff --cov-report=term-missing --cov-fail-under=70
cd frontend && npm run test
cd frontend && npm run lint
cd frontend && npm run build
```

Result: compile and blocking Flake8 pass; 300 backend tests pass with 76.57%
coverage and 53 warnings; 8 frontend tests, lint and the Vite production build
pass. The five existing hook warnings and 1.96 MB main chunk remain assigned to
Phase 7.

### Base-image pulls and digest capture

```zsh
docker pull python:3.12.13-slim-bookworm
docker pull node:22.22.2-alpine
docker pull nginxinc/nginx-unprivileged:1.30.4-alpine3.24
docker pull postgres:16.14-alpine3.24
docker image inspect --format '<tag>|<repo-digest>|<architecture>|<os>|<id>|<size>' <four-images>
```

The parallel Python and PostgreSQL pull clients reached their command timeouts;
the exact two pulls were resumed and completed. All selected local variants are
`linux/arm64`; the pinned values are multi-platform manifest-list digests. The
Dockerfile frontend image resolved to
`sha256:e87caa74dcb7d46cd820352bfea12591f3dba3ddc4285e19c7dcd13359f7cefd`.
No non-approved image source was contacted.

### Image build and contract verification

```zsh
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder docker-compose config --quiet
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder docker-compose --profile operations config --quiet
docker-compose build backend frontend
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder docker-compose build backend frontend
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder docker-compose build frontend
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config scripts/verify_container_image.sh foundation-intelligence-backend:local
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config scripts/verify_container_image.sh foundation-intelligence-frontend:local
docker buildx bake --print
```

The first build invocation stopped before Dockerfile execution because the
required Compose secret variable was absent. The second used the variable but
stopped before the build because the configured `docker-credential-desktop`
binary was unavailable. An isolated `/private/tmp` Docker config containing no
credential helper fixed that local CLI issue. The first frontend stage then
proved that production npm omission removed `tsc`; `npm ci --include=dev` fixed
the build stage while the runtime remained static-only. The final backend
context was 1.04 MB and frontend context 661 kB, compared with the 8.81 GB
baseline context.

The final backend is `354092439` bytes and UID/GID `10001:10001`; the frontend
is `56230634` bytes and UID/GID `101:101`. Both have healthchecks and contain no
`.env`, SQLite file, application credential/key path, domain-data payload or
compiler. Both declare `linux/amd64` and `linux/arm64` in `docker-bake.hcl`.
The installed Docker CLI has no usable buildx plugin (`docker buildx bake
--print` exits 125 with `unknown flag: --print`), so local manifest assembly is
not claimed; the platform declaration is statically verified and CI will run
the real multi-platform build.

### Gate-2 Compose start, health, HTTP and stop

The first default-port start created containers but PostgreSQL did not start
because `127.0.0.1:5432` was already occupied. Host ports were parameterized;
containers and network from that attempt were removed without deleting the
volume.

```zsh
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder docker-compose up -d --no-build postgres backend frontend
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder docker-compose down --remove-orphans
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 BACKEND_HOST_PORT=58000 FRONTEND_HOST_PORT=58080 docker-compose up -d --no-build postgres backend frontend
docker inspect --format '<health-and-isolation-fields>' foundation-intelligence-postgres-1 foundation-intelligence-backend-1 foundation-intelligence-frontend-1
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -tAc 'SELECT current_database(), current_user, current_setting('"'"'server_version'"'"');'
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:58000/health/live
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:58000/health/ready
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:58080/
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:58080/health/ready
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 BACKEND_HOST_PORT=58000 FRONTEND_HOST_PORT=58080 docker-compose stop
docker inspect --format '<exit-fields>' foundation-intelligence-postgres-1 foundation-intelligence-backend-1 foundation-intelligence-frontend-1
docker logs --tail 50 foundation-intelligence-backend-1
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 BACKEND_HOST_PORT=58000 FRONTEND_HOST_PORT=58080 docker-compose down --remove-orphans
```

Result: PostgreSQL 16.14, backend and frontend all became healthy through
health-based dependencies. Backend readiness returned PostgreSQL `healthy`;
frontend static HTML loaded. An initial frontend `/health/ready` request exposed
a SPA fallback bug; the corrected prefix proxy then returned the backend JSON
readiness response. Backend/frontend roots are read-only, all Linux capabilities
are dropped and `no-new-privileges` is active. Uvicorn logged `Application
shutdown complete`; PostgreSQL/frontend exited 0 and the init-wrapped backend
reported the delivered SIGTERM as 143. Containers and network were removed;
the local PostgreSQL volume and images were retained.

## Phase 3 — PostgreSQL schema

### Schema and runtime implementation

Read-only SQLite `pragma_table_info` and distinct-value queries captured every
source column plus observed classification, conversion, provenance and link
status before target constraints were written. The authoritative DDL was added
as Alembic revision `0001_postgresql_foundation`; no schema was generated from
mutable ORM metadata.

```zsh
venv/bin/python -m compileall -q src alembic
venv/bin/python -m flake8 src alembic --count --select=E9,F63,F7,F82 --show-source --statistics
PYTHONPATH=src venv/bin/python -m pytest src/tests/test_postgres_schema.py -q
git diff --check
```

Result: static schema/search/cursor tests and the production import guard pass.
The normal suite later records 304 passed, one live test skipped and 76.41%
coverage.

### Real PostgreSQL Alembic and integration gate

PostgreSQL remained bound only to local port 55432 and used the non-production
Compose secret file. Commands passed the password by file path and never logged
its value.

```zsh
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 docker-compose up -d --no-build postgres
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic upgrade head
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest src/tests/test_postgres_schema.py -q
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic downgrade base
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -tAc '<public table inventory>'
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic upgrade head
docker-compose build backend
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic downgrade base
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 docker-compose --profile operations run --rm migration
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -tAc '<catalog constraint/index summary>'
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest src/tests/test_postgres_schema.py -q
```

The first real search test failed because asyncpg could not infer the SQL type
of a null optional status parameter. Casting that bind to PostgreSQL `text`
resolved the defect; the repeated real suite passes 4/4. Both host and
container zero-to-head upgrades pass. After downgrade, only `alembic_version`
remained. The final catalog reports 25 application tables, 30 FKs, zero
unvalidated FKs, 117 checks, the three search indexes and extensions `pg_trgm`
and `plpgsql`.

### Production-mode boundary

```zsh
APP_ENV=production AUTH_MODE=oidc OIDC_ISSUER=https://issuer.invalid OIDC_AUDIENCE=foundation-intelligence OIDC_JWKS_URL=https://issuer.invalid/jwks CORS_ORIGINS=https://app.invalid DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m uvicorn bff.main:app --host 127.0.0.1 --port 58001 --no-server-header
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:58001/health/live
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:58001/health/ready
curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{http_code}' 'http://127.0.0.1:58001/api/charities/directory/organizations?query=Alpha'
```

Result: the process logged PostgreSQL repository initialization, liveness and
readiness returned 200 with PostgreSQL healthy, anonymous search returned 401,
and Ctrl-C logged complete application shutdown. The `.invalid` OIDC values
were validation-only placeholders and were never contacted. The subprocess
import guard separately proves production imports no `sqlite3` module.

## Phase 4 — deterministic SQLite-to-PostgreSQL migration

### Source and capacity preflight

```zsh
PYTHONPATH=src venv/bin/python -m migration.sqlite_to_postgres preflight --source src/data/charities.db --expected-checksum 8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651 --expected-schema-version 7
sqlite3 'file:src/data/charities.db?mode=ro&immutable=1' '<classification, currency, country, date-shape and exchange-period aggregate queries>'
shasum -a 256 src/data/charities.db
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

The preflight returned schema `7`, `integrity_check=ok`, the approved checksum
and all 12 source counts. The source is 2,100,543,488 bytes; the conservative
minimum-free estimate is 23,340,679,168 bytes including a 10 GiB safety margin.
All capacity checks passed. The protected checksums remained
`8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`
and `d40c8b0114f8c5ef728884dd0e8632ecc6f9f03912fdf8ba709556f9ba3c1f2a`.

### Implementation and real-PostgreSQL tests

```zsh
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 docker-compose up -d --no-build postgres
PYTHONPATH=src venv/bin/python -m py_compile src/migration/sqlite_to_postgres.py src/tests/test_sqlite_to_postgres_migration.py
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest src/tests/test_sqlite_to_postgres_migration.py -q
PYTHONPATH=src venv/bin/python -m pytest -q
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic downgrade 0001_postgresql_foundation
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic downgrade 0002_exchange_rate_period
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
```

The fixture gate progressed from two unit passes plus one skip to a final five
real-PostgreSQL passes. It covers immutable source access, checksum rejection,
activation, repeated no-op, quarantine, failed-candidate retry, conflicting
override rollback, full global-record transaction rollback, dataset rollback
and restoration of any pre-existing active dataset. The final normal suite is
308 passed, 2 intentional skips, 8 subtests and 53 known warnings.

Two source-fidelity defects were found by full reconciliation and fixed with
append-only Alembic revisions. `exchange_rate_date` contains 302,049 monthly
`YYYY-MM` periods, not malformed dates. In addition, 8,736 grant award values
contain full ISO timestamps; truncating them created 85 artificial duplicate
groups. Revisions `0002_exchange_rate_period` and
`0003_grant_award_timestamp` preserve both values exactly and passed their
downgrade/upgrade cycles.

### Full migration attempts and failure isolation

The migration command shape used throughout was:

```zsh
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m migration.sqlite_to_postgres migrate --source src/data/charities.db --expected-checksum 8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651 --expected-schema-version 7 --dataset-version <version> --code-revision <full-git-sha> --actor-id codex-local-remediation --actor-type ci --output-directory <private-tmp-output> --batch-size 10000
```

Executed full checkpoints:

- `262b4a894dd1f3c869ddfb43301b20990bf5f42d` stopped and quarantined the monthly exchange-period type mismatch before activation.
- One invocation containing an unverified SHA string was interrupted before PostgreSQL connection/mutation. The next invocation used `git rev-parse HEAD`; no record or candidate was created by the interrupted command.
- `9afbc4a18de5d52a159dccfe4fa55ef168069e99` loaded all rows but was rejected because truncated award dates produced 4,356 rather than 4,271 business-key duplicate groups.
- The rejected run had written 18,964 exchange rates and one override under the pre-staging implementation. With zero active datasets verified, exactly those derived rows were deleted in one local transaction (`DELETE 18964`, `DELETE 1`) before the atomic-staging correction was retested.
- `919aa96e835775b79a09aa639f5cc57826ec77c7` produced the first successful full snapshot `sqlite-v7-8fc0cce61c81`.
- An initial `r2` load under `a74d75c0772b6aaff839d9460302e8d3eca158de` was interrupted while inactive after a progress query exposed that a fixture cleanup had not restored the preceding active snapshot. `rollback --dataset-version sqlite-v7-8fc0cce61c81` restored it before the test-isolation fix.
- `d6d2b69d1f9ff7dd8bc6f58021060586b3c17757` produced the final full snapshot `sqlite-v7-8fc0cce61c81-r2` and manifest run `60af368e-c440-5521-9648-5ab272f9ddb6`.

Read-only `docker-compose exec ... psql -Atc` queries repeatedly checked only
candidate status, active flags, schema revision, constraint validation and
aggregate table counts during these runs. No source rows, secrets or credential
values were printed.

### Manifest, idempotency and rollback gate

```zsh
python3 -c '<Draft202012Validator check_schema and manifest validation>'
jq '<required manifest fields and reconciliation failures>' /private/tmp/fip-phase4-full-report-d6d2b69/migration-sqlite-v7-8fc0cce61c81-r2.json
<repeat the exact final migrate command for sqlite-v7-8fc0cce61c81-r2>
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m migration.sqlite_to_postgres rollback --dataset-version sqlite-v7-8fc0cce61c81
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -Atc '<active dataset and core-count checks>'
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m migration.sqlite_to_postgres rollback --dataset-version sqlite-v7-8fc0cce61c81-r2
```

The committed JSON report validates against Draft 2020-12. All source/target
counts and controls pass, including 30 catalog-derived FK relationships with
zero violations. The exact repeated migration returns
`idempotent_noop=true` and the same run ID. Full rollback to the first snapshot
retained 302,546 active grants and 397,469 active registry rows; switching back
to `r2` succeeded and left exactly one active dataset.

### Final container rebuild and inspection

```zsh
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config docker build --pull=false --target backend-runtime -t foundation-intelligence-backend:local .
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config scripts/verify_container_image.sh foundation-intelligence-backend:local
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001 --entrypoint python foundation-intelligence-backend:local -c 'from migration.sqlite_to_postgres import MIGRATION_SCHEMA_VERSION; print(MIGRATION_SCHEMA_VERSION)'
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001 --workdir /app --entrypoint alembic foundation-intelligence-backend:local heads
docker image inspect --format '<id|digests|size|architecture|os|user>' foundation-intelligence-backend:local
```

The legacy builder re-downloaded only hash-locked PyPI artifacts from the
approved hosts and did not pull a base image. No dependency version changed.
The final image passes the complete contract, contains migration and Alembic
head `0003_grant_award_timestamp`, and has local image ID
`sha256:e43491e5e7080e0923b9d777aa1f985bfd3c4897482d662d0be7bf7364758b91`.
It is an unpushed local image and therefore has no repository digest.

## Phase 5 — PostgreSQL application conversion

### Runtime implementation and static gates

```zsh
python3 -m py_compile src/bff/postgres/*.py src/bff/main.py src/bff/audit.py src/bff/schemas.py
git diff --check
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_security.py src/tests/test_database.py src/tests/test_postgres_schema.py -k 'not integration'
PYTHONPATH=src venv/bin/python -c '<compare all legacy and PostgreSQL APIRoute method/path contracts>'
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_postgres_application.py -k 'not TestPostgreSQLApplicationIntegration'
```

The PostgreSQL router matches all 25 existing organization/grant contracts.
Production now selects PostgreSQL-backed admin routes in addition to the data
routes. Manual triggers create durable jobs rather than filesystem status,
locks, logs or subprocesses. Production audit middleware awaits the append-only
PostgreSQL sink. Static/security/database tests passed without network access.

### Real PostgreSQL application gate

The retained PostgreSQL 16.14 container was healthy on loopback port 55432.
Every invocation used `DATABASE_PASSWORD_FILE`; no password was printed.

```zsh
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 docker-compose ps
APP_ENV=test AUTH_MODE=disabled RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_postgres_application.py -vv
```

The first repetitions exposed four PostgreSQL-specific defects: `grant` was a
reserved SQL alias, an integer interval bind was inferred as text, an aggregate
CTE retained the pre-CTE alias and joined detail columns were ambiguous. Each
was corrected and the affected real journey was repeated. The final Phase-5
suite passes five tests: complete reads and Pydantic response validation,
transactional link/cache/job/audit mutations with outer rollback, missing-DB
startup failure, production PostgreSQL route selection and real readiness.

### Regression and protected-state gates

```zsh
PYTHONPATH=src venv/bin/python -m pytest -q
shasum -a 256 src/data/charities.db
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

Result: 310 passed, five intentional skips, eight subtests and 53 known
deprecation warnings. The SQLite checksum remains
`8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`;
the aggregate audit checksum remains
`d40c8b0114f8c5ef728884dd0e8632ecc6f9f03912fdf8ba709556f9ba3c1f2a`.
No packages or images were downloaded in Phase 5. No AWS, paid/live API,
scraper/model, upload or push action occurred.

## Phase 6 — performance and concurrency

### Baseline, migration and materialization

All PostgreSQL access remained on `127.0.0.1:55432` or through the retained local Compose container. Commands used the local password file; its value was never printed.

```zsh
PYTHONPATH=src DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/python scripts/benchmark_postgres.py --samples 1 --concurrency 2
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic upgrade head
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -Atc "SELECT refresh_analytics_materializations('<active-dataset>')"
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic downgrade 0003_grant_award_timestamp
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder venv/bin/alembic upgrade head
```

The first `0004` upgrade rolled back transactionally because SQLAlchemy treated a colon in a function string literal as a bind marker. Removing that punctuation resolved the parser collision. After adding country connections and two grant source-ID indexes, the full `0004 -> 0003 -> 0004` cycle passed and the active materialization refreshed to 204,220 rows. No active dataset changed.

The diagnostic single-sample baseline measured health 2.66 ms, organization list 324.29 ms, map 2,003.54 ms, trends 413.14 ms, themes 1,092.86 ms, summary 4,347.61 ms, dashboard 8,941.12 ms, two concurrent dashboards 16,025.45 ms, exact registry 15,038.99 ms, text registry 16,841.02 ms and country funders 800.02 ms. These values drove the aggregate/index work and are not represented as percentile evidence.

### Static, PostgreSQL and performance tests

```zsh
PYTHONPATH=src venv/bin/python -m py_compile src/bff/postgres/base.py src/bff/postgres/analytics_repository.py src/bff/postgres/funder_repository.py src/bff/postgres/registry_repository.py src/bff/postgres/routes.py src/migration/sqlite_to_postgres.py src/tests/test_postgres_application.py src/tests/test_postgres_performance.py src/tests/test_postgres_schema.py scripts/benchmark_postgres.py scripts/load_test_api.py alembic/versions/0004_versioned_analytics_materializations.py
venv/bin/python -m flake8 src alembic scripts/benchmark_postgres.py scripts/load_test_api.py --count --select=E9,F63,F7,F82 --show-source --statistics
git diff --check
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_postgres_performance.py
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_postgres_performance.py src/tests/test_postgres_application.py
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_postgres_application.py src/tests/test_postgres_schema.py src/tests/test_sqlite_to_postgres_migration.py
```

The dedicated gate finishes 5/5, performance plus application finishes 10/10, and application/schema/migration finishes 15/15. The latter exercises real candidate activation, materialization, rollback and cleanup. Static compilation, blocking Flake8 and diff whitespace checks pass.

Recorded diagnostic failures were resolved and repeated:

- An invocation with `TEST_DATABASE_URL=postgresql+asyncpg://foundation_app@127.0.0.1:55432/...` omitted the password and produced four authentication failures; the prescribed `RUN_POSTGRES_INTEGRATION=1` plus secret-file configuration then passed.
- The schema fixture attempted to insert a second active dataset. It now transactionally marks the prior dataset `rolled_back`, creates the fixture, deletes it and restores the exact prior active status; the repeated test passes.
- Ten simultaneous health requests alongside a heavy query exceeded the five-connection pool by construction. The isolation gate now matches the bounded five-request pool workload and passes below 100 ms.
- One combined run observed a 4,006 ms single cold dashboard outlier and 1,110.944 ms instrumented search plan. Cold dashboard evidence now uses 20 independent cache-cleared samples and checks p95; `EXPLAIN` retains index verification while endpoint p95 independently enforces the 1-second search SLO.
- A no-results search fixture contained common English tokens and legitimately produced trigram matches. A high-entropy token now verifies the corrected nonzero overall `registry_count` on an empty result page.

### Final benchmark and authenticated in-process API load

```zsh
PYTHONPATH=src APP_ENV=test DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_POOL_SIZE=5 DATABASE_MAX_OVERFLOW=5 DATABASE_POOL_TIMEOUT_SECONDS=5 DATABASE_CONNECT_TIMEOUT_SECONDS=5 DATABASE_STATEMENT_TIMEOUT_MS=30000 venv/bin/python scripts/benchmark_postgres.py --samples 10 --concurrency 5
PYTHONPATH=src APP_ENV=production DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_POOL_SIZE=5 DATABASE_MAX_OVERFLOW=5 DATABASE_POOL_TIMEOUT_SECONDS=5 DATABASE_CONNECT_TIMEOUT_SECONDS=5 DATABASE_STATEMENT_TIMEOUT_MS=30000 venv/bin/python scripts/load_test_api.py --samples 20 --concurrency 5
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -At -F '|' -c '<version, active dataset, materialization and aggregate count queries>'
shasum -a 256 src/data/charities.db
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

The first API-load attempt used the normal 140-request application rate limit and correctly received HTTP 429 before the concurrent dashboard section. The local test harness was changed to 10,000 requests for this bounded run only and uses a generated in-memory RSA/JWKS/JWT plus `MemoryAuditSink`; it makes no identity, network or persistent audit calls. A missing `.venv/bin/python` invocation failed before execution and was repeated with the repository's existing `venv/bin/python`.

Final repository cold-dashboard p95 is 255.31 ms. Final production-mode API p95 values are health 3.70 ms, organization list 245.73 ms, map 4.95 ms, lazy map connections 6.04 ms, overview 43.45 ms, yearly trends 5.09 ms, exact registry 18.01 ms, text registry 83.93 ms and country funder ranking 19.20 ms. Five concurrent dashboards complete in 472.80 ms at 10.575/s with zero errors. Endpoint error rates are all zero; cache hit ratio is 0.8333 and no pool connection remains checked out.

The catalog query reports PostgreSQL 16.14, Alembic `0004_versioned_analytics`, exactly one active dataset and 204,220 controlled aggregate rows. It also reports 39 validated FKs, zero unvalidated FKs and 136 checks. Protected checksums remain `8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651` and `d40c8b0114f8c5ef728884dd0e8632ecc6f9f03912fdf8ba709556f9ba3c1f2a`.

No dependency/image download, AWS access, paid/live external call, scraper/model invocation, upload or push occurred in Phase 6.

### Final normal suite and container rebuild

```zsh
PYTHONPATH=src venv/bin/python -m pytest -q
DOCKER_CONFIG=/private/tmp/fip-phase2-docker-config DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock docker build --pull=false --target backend-runtime -t foundation-intelligence-backend:local .
```

The normal suite passes 311 tests, skips nine explicit live-environment tests, passes eight route subtests and emits the same 53 dependency/test-client deprecation warnings. The first Docker request was rejected before process creation because the execution service's usage/approval limit had been reached. After the user explicitly reconfirmed the exact local action, the same command succeeded. The pinned base and hash-locked dependency layers all reported `Using cache`; no pull, package download or dependency resolution occurred. The final local arm64 image is 354,456,439 bytes, runs as `10001:10001` and has image ID `sha256:cf71388a8fc83cdc32632ea2cf8ea9b7b27d4d68b164f848cd6e97b49905af8a`.

The scoped local checkpoint was then attempted:

```zsh
git add <explicit Phase-6 file list>
```

The ordinary sandbox failed with `Unable to create .git/index.lock: Operation not permitted`. The required escalated repetition was rejected before process creation by the same execution-service usage/approval limit. No index lock remained and no indirect `.git` write was attempted. The user then explicitly reconfirmed targeted staging and the local Phase-6 commit, while continuing to prohibit broad staging and push.
