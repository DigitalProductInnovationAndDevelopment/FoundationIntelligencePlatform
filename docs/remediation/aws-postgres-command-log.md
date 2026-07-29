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

## Phase 7 — frontend remediation

All application and browser execution in this phase remained local. The
initial remediation used the existing `frontend/node_modules` and npm
lockfile. After the user separately approved the named test dependencies,
only `registry.npmjs.org` was contacted to resolve and install the exact
Playwright/axe packages documented below. No browser or image download,
external API call, AWS action, upload or push occurred.

### Baseline and source inspection

```zsh
cd frontend && npm test
cd frontend && npm run lint
cd frontend && npm run build
rg <frontend request, hook, key, dialog, CSS and bundle patterns> frontend/src
npm ls --depth=0
```

The baseline passed eight tests but Oxlint reported five React hook warnings.
The production main chunk was 1,963.34 kB raw / 611.99 kB gzip and Vite emitted
the large-chunk warning. The stylesheet also contained a runtime Google Fonts
import. Playwright and axe packages were absent.

### Static, unit and bundle gates

```zsh
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build
cd frontend && npm run check:bundle
node --check frontend/scripts/check-runtime-layout.mjs
git diff --check
```

Final results: Oxlint emits zero warnings; all 13 tests pass; TypeScript and
Vite pass; initial JavaScript is 87.80 KiB gzip / 120 KiB, initial CSS is 18.59
KiB gzip / 25 KiB and the largest deferred chunk is 392.37 KiB gzip / 425 KiB.
No generated `frontend/dist/` artifact is tracked.

### Local browser journeys

```zsh
cd frontend && npm run test:runtime
```

The script builds the current source, starts Vite Preview on `127.0.0.1:4173`,
launches the already installed Google Chrome 150 in headless mode with
background networking disabled, injects deterministic local API responses and
uses the Chrome DevTools Protocol. It stops both local processes and removes
its isolated temporary browser profile afterward.

The final run passes at 320, 390, 768, 1024, 1440 and 1920 pixels. It checks
page overflow, visible control bounds/names, KPI cropping, map-first ordering,
wrapped map controls, one initial overview request, one interaction-triggered
connection request, filter drawer viewport bounds, Tab trapping, Escape/focus
restoration and browser console/runtime exceptions. At 320 and 1024 it also
navigates through Donor Directory and Advanced Charity Commission Search and
checks the Registry empty state and Registry filter drawer.

Early diagnostic runs correctly failed because the first checker counted the
intentionally off-canvas closed mobile sidebar, checked focus before React's
asynchronous focus tick and once exercised a stale `dist/` build. The checker
now tests only intersecting visible controls, waits for focus and always builds
current source. A Strict Mode focus restoration issue was fixed by retaining
the trigger reference until the animation-frame focus completes.

### Named-tool pre-approval check and immutable checks

```zsh
cd frontend && npm ls @playwright/test playwright @axe-core/playwright axe-core --depth=0
node --version
npm --version
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version
shasum -a 256 src/data/charities.db
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
git status --short
git diff --check
git diff --stat
```

The named Playwright/axe package query returned `(empty)`. At that point,
installing them required an npm-registry download and a lockfile update, so the
work stopped for explicit approval. The active SQLite checksum remained
`8fc0cce61c81d54869a3cc9a61d9378e1cb03f2b9607a70c2836b52fba257651`;
the aggregate audit checksum remains
`d40c8b0114f8c5ef728884dd0e8632ecc6f9f03912fdf8ba709556f9ba3c1f2a`.
No Phase-7 files were staged or committed before that approval.

### Approved exact dependency install and named browser gate

The user then explicitly approved downloads for the named Playwright and axe
dependencies. Network access was restricted to `registry.npmjs.org`, install
scripts and audits were disabled, and `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`
prevented browser downloads. The already installed Google Chrome remained the
only browser executable used.

```zsh
cd frontend && npm view @playwright/test version --registry=https://registry.npmjs.org
cd frontend && npm view @axe-core/playwright version --registry=https://registry.npmjs.org
cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --package-lock-only --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
cd frontend && npm ls @playwright/test playwright playwright-core @axe-core/playwright axe-core --depth=1
cd frontend && npm run test:e2e
```

The registry resolved exact direct versions `@playwright/test@1.62.0` and
`@axe-core/playwright@4.12.1`. Their locked transitive runtime packages are
`playwright@1.62.0`, `playwright-core@1.62.0` and `axe-core@4.12.1`; npm also
locked the platform-optional `fsevents@2.3.2`. The exact lockfile integrity
values are recorded in `docs/remediation/aws-postgres-dependency-locks.md`.
The lockfile-only diff showed only these named packages and their required
transitive entries; no existing package version changed. `npm ci` added 80
local packages and performed no lifecycle script, audit or browser download.

The first axe-enabled run found four genuine accessibility defects: invalid
SVG-group labelling, insufficient active-navigation contrast, a skipped
heading level and an unnamed compact-header disclosure. Each source issue was
corrected and the complete gate was repeated. The final named gate passes all
six viewport projects: eight tests pass and four deliberately redundant
secondary journeys are skipped. The Overview journey runs at 320, 390, 768,
1024, 1440 and 1920 pixels; the Donor/Registry journey additionally runs at
320 and 1024 pixels. Every executed page reports zero axe violations, browser
console errors, runtime errors and unexpected API requests.

## Phase 8 — durable pipelines and storage contracts

All execution remained local. No source scraper, news/model call, dependency
download, AWS API, S3/SQS/EventBridge/Step Functions action, upload or push
occurred.

### Static and contract gates

```zsh
PYTHONPATH=src venv/bin/python -m py_compile alembic/versions/0005_durable_pipeline.py src/pipelines/durable.py src/pipelines/durable_worker.py src/bff/postgres/idempotency_repository.py src/bff/postgres/job_repository.py src/bff/postgres/pipeline_repository.py src/bff/postgres/admin_routes.py src/bff/security.py src/bff/main.py src/tests/test_durable_pipeline.py
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_durable_pipeline.py
venv/bin/python -m flake8 src/bff/postgres/idempotency_repository.py src/bff/postgres/job_repository.py src/bff/postgres/pipeline_repository.py src/pipelines/durable.py src/pipelines/durable_worker.py src/tests/test_durable_pipeline.py alembic/versions/0005_durable_pipeline.py --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/python -m json.tool config/source-pipelines.json
git diff --check
```

The first contract run correctly exposed two test-only expectation defects:
the immutable-store negative test supplied a differently sized payload and
therefore reached the length guard before its expected checksum guard, and a
text scan matched the explanatory word `subprocess` in a docstring. The
fixture now uses equal-length changed bytes and the coordination test checks
imports/calls rather than prose. The repeated local gate passes seven tests
with the one PostgreSQL-only test intentionally skipped. Python compilation,
blocking Flake8, JSON parsing and whitespace checks pass.

### Local PostgreSQL migration and integration

```zsh
docker-compose ps
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_durable_pipeline.py
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic downgrade 0004_versioned_analytics
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
PYTHONPATH=src venv/bin/python -m pytest -q
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_durable_pipeline.py src/tests/test_postgres_application.py src/tests/test_postgres_schema.py
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -At -F '|' -c '<revision, table, FK, check, active-dataset and source-control counts>'
```

The retained PostgreSQL 16.14 container was already healthy; no image pull or
build occurred. Initial upgrade, `0005 -> 0004 -> 0005` and the repeated real
integration all pass. The dedicated run passes 8/8 and the combined
Phase-8/application/schema run passes 18/18. The normal suite passes 318
tests, skips ten explicit live-environment tests, passes eight route subtests
and emits the existing 53 dependency/test-client deprecation warnings.

The first catalog command omitted the Compose password-file variable and was
rejected by Compose before `psql` started; it was repeated with the existing
local secret-file path. Final catalog evidence is Alembic
`0005_durable_pipeline`, 40 application tables, 49 FKs, 161 checks, active
dataset `sqlite-v7-8fc0cce61c81-r2`, eight source configurations, zero enabled
schedules and eight governance blocks. Test transactions roll back their job,
idempotency, ingestion and object fixtures. The production-startup integration
persists only the authoritative eight disabled source-control rows.

## Phase 9 — governance and retention

All retention execution was dry-run/report-only. No row, object, source file,
backup or dataset was deleted or archived.

### Configuration and local gates

```zsh
PYTHONPATH=src venv/bin/python -m py_compile alembic/versions/0006_governance_retention.py src/governance/retention.py src/governance/exposure.py src/bff/postgres/governance_repository.py src/bff/postgres/governance_routes.py src/bff/postgres/admin_routes.py src/bff/utils/logging.py src/bff/main.py src/bff/schemas.py src/tests/test_governance_retention.py
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_governance_retention.py src/tests/test_security.py
venv/bin/python -m flake8 alembic/versions/0006_governance_retention.py src/governance src/bff/postgres/governance_repository.py src/bff/postgres/governance_routes.py src/tests/test_governance_retention.py --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/python -m json.tool config/data-governance.json
git diff --check
```

The local governance/security run passes 19 tests, deliberately skips its one
PostgreSQL-only method and passes eight route/security subtests. Configuration
validation confirms 14 required classifications, unique retention classes,
no delete windows, no destructive/production activation, a complete role
register with unresolved named owners, a data-subject workflow and explicit
field policies. Compilation, blocking Flake8, JSON and whitespace pass.

### Local PostgreSQL migration and evidence

```zsh
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_governance_retention.py
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic downgrade 0005_durable_pipeline
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/alembic upgrade head
PYTHONPATH=src venv/bin/python -m pytest -q
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_governance_retention.py src/tests/test_durable_pipeline.py src/tests/test_postgres_application.py src/tests/test_postgres_schema.py
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -At -F '|' -c '<revision, table, constraint, policy and destructive-control counts>'
```

The dedicated Phase-9 PostgreSQL run passes 9/9. It creates and releases an
incident hold, proves the hold overrides a due archive, records held/archive
dry-run manifests, proves deletion/restore evidence is immutable, reports an
expired export, creates a hashed data-subject request and rolls the entire
fixture back. The active dataset never changes.

The `0006 -> 0005 -> 0006` cycle passes. The normal regression passes 326
tests, skips 11 explicit live tests and passes eight subtests; combined
Phase-9/Phase-8/application/schema passes 27/27. Final catalog evidence is
revision `0006_governance_retention`, 45 tables, 55 FKs, 189 checks and 14
policies with zero destructive flags, zero delete windows, zero active holds
and zero persisted deletion manifests. Production-startup configuration sync
persists only the 14 proposed, non-destructive policies.

No dependency download, AWS call, paid/live API, upload or push occurred.

## Phase 10 — observability

All execution remained local. No CloudWatch/AWS operation, dependency
download, external API, upload or push occurred.

```zsh
PYTHONPATH=src venv/bin/python -m py_compile src/observability/metrics.py src/bff/utils/logging.py src/bff/database.py src/bff/main.py src/bff/postgres/observability_routes.py src/pipelines/durable_worker.py src/tests/test_observability.py src/tests/test_database.py
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_observability.py src/tests/test_database.py src/tests/test_security.py
venv/bin/python -m flake8 src/observability src/bff/utils/logging.py src/bff/database.py src/bff/main.py src/bff/postgres/observability_routes.py src/pipelines/durable_worker.py src/tests/test_observability.py src/tests/test_database.py --count --select=E9,F63,F7,F82 --show-source --statistics
python3 -m json.tool config/observability.json
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_observability.py
PYTHONPATH=src venv/bin/python -m pytest -q
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_observability.py src/tests/test_governance_retention.py src/tests/test_durable_pipeline.py src/tests/test_postgres_application.py src/tests/test_postgres_schema.py
docker-compose exec -T postgres psql -U foundation_app -d foundation_intelligence -At -F '|' -c '<revision, active dataset, source/policy, outbox and DLQ counts>'
shasum -a 256 src/data/charities.db
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
git diff --check
```

An initial static run exposed that the new JSON formatter had removed the
public `RedactingFormatter` compatibility class required by the Phase-1
security test; the compatibility formatter was restored. The first real
PostgreSQL test correctly exhausted the application pool but expected the
standard-library timeout class instead of SQLAlchemy's timeout class. Only
the test expectation changed. The repeated Phase-10 PostgreSQL run passes
6/6, the combined regression passes 33/33 and the normal suite passes 331
tests with 12 explicit live-environment skips, eight subtests and the existing
53 dependency/test-client warnings.

Final catalog evidence is revision `0006_governance_retention`, active dataset
`sqlite-v7-8fc0cce61c81-r2`, eight source configurations, fourteen retention
policies, zero pending/failed outbox rows and zero dead-lettered jobs.

## Phase 11 — Terraform AWS infrastructure definitions

No AWS API, provider registry, remote state, DNS/certificate endpoint or paid
service was contacted.

```zsh
command -v terraform tofu tflint checkov tfsec trivy semgrep syft grype
terraform version
docker image ls --format '{{.Repository}}:{{.Tag}} {{.Digest}} {{.ID}}' hashicorp/terraform
PYTHONPATH=src venv/bin/python scripts/validate_terraform_static.py
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_terraform_contract.py src/tests/test_database.py
PYTHONPATH=src venv/bin/python -m py_compile scripts/validate_terraform_static.py src/bff/database.py src/tests/test_terraform_contract.py src/tests/test_database.py
venv/bin/python -m flake8 scripts/validate_terraform_static.py src/bff/database.py src/tests/test_terraform_contract.py src/tests/test_database.py --count --select=E9,F63,F7,F82 --show-source --statistics
PYTHONPATH=src venv/bin/python -m pytest -q
git diff --check
```

`terraform version` failed with `zsh:1: command not found: terraform`.
The local Docker image query returned no Terraform image. No Terraform/OpenTofu
CLI, tflint, checkov, tfsec, trivy or semgrep is installed, and no provider
lock exists. Downloads from `registry.terraform.io` are not authorised.
Consequently fmt/init/validate/security scan/plan remain `NOT TESTED`.

The offline checker reports 26 Terraform files, 101 resource blocks and 58
AWS resource types across dev/staging. It validates balanced structure,
required services, private/deletion-protected RDS, public-access-blocked KMS
buckets without expiration, non-root/private/digest ECS, disabled schedules,
OIDC subject/audience and bounded wildcard contexts. The full regression
passes 334 tests with 12 explicit live skips, eight subtests and the existing
53 dependency/test-client warnings.

## Phase 12 — CI/CD and supply chain

Approved network activity was restricted to PyPI/files.pythonhosted.org for
the exact mypy lock/install and registry.npmjs.org for `npm audit`. No other
host, GitHub workflow, AWS API, image registry push or upload was used.

```zsh
venv/bin/pip index versions mypy --index-url=https://pypi.org/simple
venv/bin/pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --index-url=https://pypi.org/simple --output-file=requirements.txt requirements.in
venv/bin/python -m pip install --require-hashes -r requirements.txt
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
PYTHONPATH=src venv/bin/python scripts/generate_sbom.py
PYTHONPATH=src venv/bin/python scripts/check_licenses.py
ruby -e '<parse each workflow with stdlib YAML>' .github/workflows/*.yml
PYTHONPATH=src venv/bin/python scripts/validate_ci_workflows.py
PYTHONPATH=src venv/bin/python scripts/validate_terraform_static.py
PYTHONPATH=src venv/bin/python -m pytest -q '<focused Phase-12 contract tests>'
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_ci_performance_smoke.py
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder PYTHONPATH=src venv/bin/python -m migration.release_gate
cd frontend && npm run lint && npm test && npm run build
cd frontend && npm run test:e2e
PYTHONPATH=src venv/bin/python -m pytest -q
DOCKER_CONFIG=/private/tmp/docker-config-no-creds DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock docker build --pull=false --target backend-runtime --tag foundation-intelligence-backend:phase12 .
DOCKER_CONFIG=/private/tmp/docker-config-no-creds DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock scripts/verify_container_image.sh foundation-intelligence-backend:phase12
DOCKER_CONFIG=/private/tmp/docker-config-no-creds DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock docker run --rm --network none --entrypoint python foundation-intelligence-backend:phase12 -c '<runtime contract imports>'
cd frontend && npm audit --audit-level=high --ignore-scripts --registry=https://registry.npmjs.org
```

PyPI resolved `mypy==2.3.0` plus `ast-serialize==0.6.0`, `librt==0.13.0`,
`mypy-extensions==1.1.0` and `pathspec==1.1.1`; every hash is in
`requirements.txt` and no existing version changed. SBOM output is 70 Python
and 128 npm components. Licence scanning covers 155 installed components with
zero unknown/forbidden declarations. npm reports zero vulnerabilities.

The first local Playwright attempt was blocked before tests by sandbox
`listen EPERM` on `127.0.0.1:4174`; the approved local-port repeat passes 8
tests with 4 intentional skips. The first image import one-liner used the wrong
test attribute after successfully importing modules; the corrected isolated
smoke reports 14 governance policies, 21 metrics and eight sources.

Final normal regression: 342 passing, 13 explicit live skips and eight
subtests. The data-free non-root image is 354,624,742 bytes with ID
`sha256:172dab7c1c7842b0b34f0991d97f8ae34391d36e6ece628db4a63672c36781e9`.

## Phase 13 — Shadow comparison and cutover preparation

No AWS API, production traffic, paid API, live news provider, upload or Git
remote was contacted. The final frontend rebuild/install contacted only the
explicitly approved `registry.npmjs.org` through deterministic `npm ci`.

```zsh
PYTHONPATH=src venv/bin/python -m pytest -q src/tests/test_shadow_transition.py src/tests/test_transition_golden.py
PYTHONPATH=src venv/bin/mypy --config-file mypy.ini
PYTHONPATH=src venv/bin/python -m flake8 src/transition scripts/verify_transition.py scripts/verify_local_rollback.py --count --select=E9,F63,F7,F82 --show-source --statistics
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python scripts/verify_transition.py --write-golden
DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python scripts/verify_local_rollback.py
POSTGRES_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder POSTGRES_HOST_PORT=55432 DOCKER_CONFIG=/private/tmp/docker-config-no-creds DOCKER_HOST=unix:///Users/manuelgrabmayer/.docker/run/docker.sock scripts/verify_local_restore.sh
shasum -a 256 src/data/charities.db config/golden/transition-domain.json
find docs/audits -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

The first two transition-query executions failed on the date projection because
`asyncpg` required `date` objects rather than ISO strings. Explicit conversion
was added. The next full execution exposed only exact-representation differences:
SQLite integer minor units versus integral PostgreSQL `NUMERIC`; exact integral
normalization removed the type-only difference without tolerance or rounding.
The expanded final comparison passes 18 projections with zero differences.

The first rollback execution stopped before activation because the migration
module still hard-coded schema `0004_versioned_analytics` while Alembic was at
`0006_governance_retention`. Migration and release gates now read the one
versioned expected schema contract. The repeat switches from
`sqlite-v7-8fc0cce61c81-r2` to `sqlite-v7-8fc0cce61c81` and back, with equal
counts and active materialization.

The full logical restore produced a 247,509,368-byte custom archive (SHA-256
`2c571954768ba4379f3e61160fb808cbc0bd35e6e13ec2f0b4d776c760ceae87`),
restored all 5.45 GB of database state into an isolated database, and matched
schema, active dataset, charity/registry/grant counts, eligible EUR minor total
and materialization state. The exact temporary database and archive were then
removed and their absence verified.

Final local validation added:

```zsh
docker build --pull=false --target backend-runtime --tag foundation-intelligence-backend:phase13 --tag foundation-intelligence-backend:local .
scripts/verify_container_image.sh foundation-intelligence-backend:phase13
docker run --rm --network none --entrypoint python foundation-intelligence-backend:phase13 -c '<transition and schema imports>'
docker-compose up -d --no-build backend frontend
curl --fail --silent --show-error http://127.0.0.1:8501/health/live
curl --fail --silent --show-error http://127.0.0.1:8501/health/ready
docker-compose restart backend
curl --retry 10 --retry-delay 2 --retry-connrefused --fail --silent --show-error http://127.0.0.1:8501/health/ready
PYTHONPATH=src venv/bin/coverage run --source=src/bff,src/migration,src/pipelines,src/transition -m pytest -q
venv/bin/coverage report --skip-empty
RUN_POSTGRES_INTEGRATION=1 DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432 DATABASE_NAME=foundation_intelligence DATABASE_USER=foundation_app DATABASE_PASSWORD_FILE=/private/tmp/fip-compose-secret-placeholder DATABASE_STATEMENT_TIMEOUT_MS=120000 PYTHONPATH=src venv/bin/python -m pytest -q '<combined PostgreSQL suites>'
docker build --pull=false --target frontend-runtime --tag foundation-intelligence-frontend:phase13 --tag foundation-intelligence-frontend:local .
cd frontend && PHASE7_BASE_URL=http://127.0.0.1:8081 npm run test:e2e
cd frontend && npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
cd frontend && npm run lint && npm test -- --run && npm run build
```

Normal regression passes 352 tests, 13 skips and eight subtests with measured
61% coverage across the selected BFF/migration/pipeline/transition source. The
combined PostgreSQL run passes 55. Frontend passes 13 unit tests and its bundle
budgets. The first E2E run against the pre-existing stale frontend container
failed eight tests. The current pinned frontend image was rebuilt, the container
recreated, and the repeat passes eight tests with four intentional skips.

The frontend image build and final local install used the approved npm registry
through `npm ci`; 80 packages were installed locally on the final run. No other
host was contacted. Backend image `sha256:101071f6c750...338ee` is 354,658,326
bytes/non-root; frontend image `sha256:8f665856111e...9ad1a` is 56,241,904
bytes/non-root. Stack start, liveness, readiness and backend restart pass.

## Final acceptance — exact CI coverage replay

```zsh
PYTHONPATH=src venv/bin/pytest -q --cov=src --cov-report=term --cov-fail-under=70
```

Result: exit 2 during collection. Four tests importing `scripts.*` failed with
`ModuleNotFoundError: No module named 'scripts'`. This reproduced the literal
workflow invocation and identified that the installed `pytest` entry point did
not retain the repository root when `PYTHONPATH=src` was set.

The backend and PostgreSQL workflow steps were changed from bare `pytest` to
`python -m pytest`. The offline workflow validator now rejects a bare pytest
entry point. The corrected exact CI coverage gate was then run:

```zsh
PYTHONPATH=src venv/bin/python -m pytest -q --cov=src --cov-report=term --cov-fail-under=70
```

Result: exit 0; 352 passed, 13 skipped, eight subtests passed, 53 warnings and
72.73% total coverage across `src`, above the required 70% floor. No network,
AWS, database mutation, upload or push occurred.

## Final acceptance — native Terraform formatting

The approved Docker Hub-only pull resolved
`hashicorp/terraform:1.9.8` to manifest digest
`sha256:18f9986038bbaf02cf49db9c09261c778161c51dcc7fb7e355ae8938459428cd`
and local image ID
`sha256:97aaea908f872c3c60b75e9bffd6eeae34386c0e9671d6b2a1e30418ea702269`.

```zsh
docker run --rm --network none -v '<repository>:/workspace:ro' -w /workspace hashicorp/terraform:1.9.8 fmt -check -recursive infra/terraform
```

The first run failed on `edge.tf`: Terraform rejects nested `allow`, `none`
and `block` blocks written inside a single-line parent block. After expanding
those blocks, the parser listed 15 additional files requiring canonical
formatting. The pinned CLI formatted those files with no network, and the final
identical `fmt -check -recursive` exits 0.

In an exact temporary copy, still with `--network none`,
`terraform init -backend=false -input=false` installed the local platform
module and then exited nonzero because `registry.terraform.io/hashicorp/aws`
could not be reached. `terraform validate` then reported `Missing required
provider`. The exact temporary directory was removed. No provider, AWS API,
state, plan, apply, destroy, upload or Git remote was accessed.
