# Audit Command Log

Audit date: 2026-07-28  
Working directory for repository commands: `/Users/manuelgrabmayer/netlight - github/FoundationIntelligencePlatform`

Commands below were actually executed. Read-only discovery commands that differed only by a search expression are grouped, with their patterns and results stated. Secrets and environment values are intentionally absent. Exit `0` means the command itself succeeded; HTTP status is recorded separately because `curl` normally exits 0 for 4xx/5xx responses.

## Initial state and inventory

| Command | Exit | Result |
|---|---:|---|
| `pwd` | 0 | Expected repository path. |
| `date -u '+%Y-%m-%dT%H:%M:%SZ'` and `date '+%Y-%m-%dT%H:%M:%S%z %Z'` | 0 | Audit start recorded as 2026-07-28T19:16:52Z / 21:16:52 CEST. |
| `git status --short --branch` | 0 | Branch clean at start, ahead 1. |
| `git branch --show-current` | 0 | `12-fr-12-add-dashboard-filtering-and-drill-down`. |
| `git rev-parse HEAD` | 0 | `97d9b02491866edc9b5d1aec1183dc73e2914626`. |
| `git remote -v` | 0 | GitHub remote identified; no network write executed. |
| `uname -a`, `sw_vers`, `uname -m` | 0 | macOS 26.4.1 / Darwin 25.4.0 / arm64. |
| `python3 --version`, `venv/bin/python --version`, `node --version`, `npm --version`, `docker --version`, `docker-compose --version` | 0 | Python 3.12.3, Node 22.20.0, npm 10.9.3, Docker 28.1.1 CLI, standalone Compose 2.36.0. |
| `docker compose version` | 1 | Docker CLI reported `unknown command`; standalone `docker-compose` used. |
| `rg --files`, `find . -maxdepth 3 -type f`, `find . -name AGENTS.md -print` | 0 | Repository, config, test, workflow and data inventories created; only dependency-local Recharts AGENTS file found. |
| `du -ah src/data frontend .git | sort -h` and `find ... -exec stat ...` variants | 0 | Active DB 2.10 GB; registry DB 1.70 GB; large JSON/JSONL/raw artifacts identified. |
| `git ls-files`, `git check-ignore -v .env src/data/charities.db frontend/dist .pytest_cache` | 0 | Secrets/DB/build/test artifacts ignored; large raw JSON and notebooks tracked. |
| `git ls-files -z | xargs -0 ...` secret/size scans and `rg` secret-pattern scans | 0 | No high-confidence API token/private key in tracked files; no developer absolute-path coupling found. |
| `awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/{print $1}' .env .env.example frontend/.env.example` | 0 | Variable names inventoried without values. |
| `find . -maxdepth 4 \( -name '*.tf' -o -name 'template.yaml' -o -name 'cdk.json' -o -name 'serverless.yml' \)` | 0 | No AWS IaC found. |
| `rg -n 'sqlite3|PRAGMA|INSERT OR REPLACE|ON CONFLICT|strftime|rowid|json_extract|json_each|fts5|COLLATE NOCASE' src -g '*.py'` | 0 | SQLite portability surface quantified. |
| `rg -n 'ArgumentParser\(|@click\.command|typer\.Typer' src -g '*.py'` | 0 | Sixteen CLI entry files identified. |

## Installation, tests, lint and build

| Command | Exit | Result |
|---|---:|---|
| `cd frontend && npm ci` | 0 | 74 packages installed from lockfile. |
| `cd frontend && npm run test` | 0 | 8 passed, 0 failed. |
| `cd frontend && npm run lint` | 0 | Five React hook dependency warnings, no error exit. |
| `cd frontend && npm run build` | 0 | Vite production build passed; main JS 1,963.84 KB / 612.12 KB gzip; chunk warning. |
| `PYTHONPATH=src venv/bin/python -m pytest src/tests --cov=bff --cov-report=term-missing --cov-fail-under=70` | 0 | 280 passed, 3 warnings, 76.07% coverage. |
| `PYTHONPATH=src venv/bin/python -m pytest --collect-only -q src/tests` | 0 | Complete backend test/function inventory collected. |
| `venv/bin/python -m compileall -q src` | 0 | Python source compiled. |
| `venv/bin/python -m flake8 src --count --select=E9,F63,F7,F82 --show-source --statistics` | 0 | No blocking syntax/undefined-name class issue. |
| `venv/bin/python -m flake8 src --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics` | 0 | 1,071 advisory findings; command intentionally never fails. |
| `cd frontend && npm audit --audit-level=high` | 1 | Registry/DNS unavailable under restricted network; escalation not approved. Vulnerability result unknown. |
| `python3 -m venv /private/tmp/fip-audit-venv` | 0 | Clean isolated environment created. |
| `/private/tmp/fip-audit-venv/bin/pip install -r requirements.txt` | 1 | Restricted DNS/network prevented native clean install; escalation not approved. |
| `command -v gitleaks trivy osv-scanner pip-audit` | non-zero | None installed. |

## Development startup and local processes

| Command | Exit / state | Result |
|---|---:|---|
| `./start_backend.sh` | running | FastAPI development process started on 127.0.0.1:8000. |
| `cd frontend && npm run dev -- --host 127.0.0.1` | running | Vite development process started on 127.0.0.1:5173. |
| `curl -I http://127.0.0.1:5173` | 0 / HTTP 200 | Frontend reachable. |
| `curl -sS http://127.0.0.1:8000/health` | 0 / HTTP 200 | Backend healthy. |
| Interactive `Ctrl-C` against prior backend/frontend sessions, then the same starts again | clean stop | Stop/restart behavior verified before the audit; current restarted processes were intentionally left running. |
| A second sandboxed `./start_backend.sh` while port 8000 was occupied | 3 | Sandbox bind attempt reported operation not permitted; inconclusive and therefore rerun with local-process permission. |
| A second permitted `./start_backend.sh` while port 8000 was occupied | 3 | Clear `[Errno 48] ... address already in use`; original backend remained healthy. |
| `cd frontend && npm run dev -- --host 127.0.0.1 --strictPort --port 5173` while 5173 was occupied | 1 | Clear `Port 5173 is already in use`; original frontend remained healthy. |

## SQLite staging, validation and restore

| Command | Exit | Result |
|---|---:|---|
| `sqlite3 -readonly src/data/charities.db ".backup '/private/tmp/fip-audit-staging-20260728.db'"` | 0 | Coherent 2,100,543,488-byte staging copy created. |
| `shasum -a 256 /private/tmp/fip-audit-staging-20260728.db` | 0 | `609208...895d` before staging migration exercise. |
| `sqlite3 -readonly /private/tmp/fip-audit-staging-20260728.db 'PRAGMA quick_check;'` | 0 | `ok`, 15.15 s. |
| `sqlite3 -readonly /private/tmp/fip-audit-staging-20260728.db 'PRAGMA integrity_check;'` | 0 | `ok`, 39.08 s. |
| `sqlite3 -readonly /private/tmp/fip-audit-staging-20260728.db 'PRAGMA foreign_key_check;'` | 0 | Zero rows. |
| `sqlite3 -readonly /private/tmp/fip-audit-staging-20260728.db '.tables'` and `.schema`/`PRAGMA table_info`/`foreign_key_list`/`index_list` queries | 0 | Schema, tables, indexes, FKs and FTS triggers inventoried. |
| Read-only SQL `COUNT`, `COUNT(DISTINCT ...)`, duplicate, orphan, amount/date, currency, geo, programme, organization and `dbstat` queries against staging | 0 | Counts and anomaly cohorts recorded in database report. |
| `PYTHONPATH=src venv/bin/python` staging script calling `create_tables(reset=False)` twice and `validate_database(require_foreign_keys=True)` | 0 | Idempotent; schema 7, unchanged counts, valid. |
| SQLite `.backup` from staging to `/private/tmp/fip-audit-restore-20260728.db` | 0 | Restore copy created. |
| `shasum -a 256 /private/tmp/fip-audit-restore-20260728.db` | 0 | `6f665f...0755`; logical row/FK/quick checks passed. |
| `PYTHONPATH=src venv/bin/python -m data.benchmark_registry --db /private/tmp/fip-audit-staging-20260728.db --query foundation --charity-number 200027` | 0 | FTS 651.171 ms, exact 1.997 ms, filtered 8.89 ms; expected indexes used. |

## CLI entry-point checks

The following exact pattern was run for each listed path:

```zsh
PYTHONPATH=src venv/bin/python "$cli" --help >/dev/null 2>&1
```

All returned exit 0:

```text
src/data/benchmark_registry.py
src/data/registry.py
src/pipelines/backfill_ecb_exchange_rates.py
src/pipelines/curate_europe_tech_grants.py
src/pipelines/extend_observed_360giving_pilot.py
src/pipelines/import_observed_360giving_grants.py
src/pipelines/prewarm_grant_overview_cache.py
src/pipelines/reclassify_grant_enrichment.py
src/pipelines/run_pipeline.py
src/pipelines/sample_360giving_publishers.py
src/preprocessing/extract_geo_topic.py
src/preprocessing/extract_impressum.py
src/scrapers/360giving.py
src/scrapers/hinchilla.py
src/scrapers/philea.py
src/scrapers/register_of_charities.py
```

These checks prove command startup/help only. Live scraper/import commands were not executed.

## API inventory, success, timing and error cases

| Command | Exit / HTTP | Result |
|---|---:|---|
| `curl -sS http://127.0.0.1:8000/openapi.json \| jq -r '.paths \| keys[]'` | 0 | 31 paths inventoried. |
| OpenAPI `jq` query enumerating each GET/POST/PUT/PATCH/DELETE and `operationId` | 0 | 36 operations; core proxy has a duplicate operation-ID warning across methods. |
| Initial timing loop using `/api/health`, `/api/grants/overview`, `/api/registry/...`, `/api/grants/source-funders...` | 0 / HTTP 404 except charities | Incorrect guessed paths were identified, discarded and logged honestly. |
| Corrected 10-sample loop for `/health`, `/api/charities/grants/overview`, `/api/charities/directory/organizations?limit=10`; 5 samples for `/api/charities?limit=20` | 0 / HTTP 200 | Warm timings recorded in performance report. |
| Five calls to `/api/charities/grants/funders?beneficiary_country=DE&limit=20` | 0 / HTTP 200 | 0.605–2.009 s. |
| One call to funders without `beneficiary_country` | 0 / HTTP 422 | Required-query validation confirmed. |
| `curl` GETs for charity 1075920 detail, grants and Sankey | 0 / HTTP 200 | 812 grants; 31 nodes/30 links. Temporary responses stored in `/private/tmp`. |
| `curl -H 'Content-Type: application/json' -d '{"target_profile":{"programme_areas":["Education"],"geographies":["Germany"],"minimum_annual_expenditure":100000,"target_average_grant_amount":50000,"currency":"EUR","organization_types":["Foundation"]}}' http://127.0.0.1:8000/api/charities/1075920/score` | 0 / HTTP 200 | Score 90.0, confidence 0.875, not a prediction. |
| Directory `limit=101`, `income_min=-1`; invalid score type; invalid news lookback; missing funder country | 0 / HTTP 422 | Input constraints and sanitized details confirmed. |
| `curl -sS http://127.0.0.1:8000/api/charities/999999999` | 0 / HTTP 404 | Clear missing-resource message. |
| `curl -sS http://127.0.0.1:8000/api/core/health` | 0 / HTTP 503 | Unavailable fixed downstream returns sanitized 503. |
| Concurrent read-only request set: heavy map plus trends/themes/charities/registry/funders | 0 / HTTP 200 | Heavy map 67.5668 s; other requests each waited about 55.435 s. |

All active-DB API calls above were read-only except the score POST, which is deterministic/read-only. Valid admin/enrich/relink/reset/cache mutations were not called during the audit.

## Browser commands

Headless Chrome was run with a fresh profile and `--no-sandbox --disable-gpu`. Each command exited 0 and wrote the named file:

```zsh
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-1440 --window-size=1440,900 --screenshot='docs/audits/screenshots/overview-1440x900.png' 'http://127.0.0.1:5173/'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-warm --window-size=1440,900 --virtual-time-budget=10000 --screenshot='docs/audits/screenshots/overview-warm-1440x900.png' 'http://127.0.0.1:5173/'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-mobile --window-size=390,844 --virtual-time-budget=12000 --screenshot='docs/audits/screenshots/overview-mobile-390x844.png' 'http://127.0.0.1:5173/'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-ipadl --window-size=1024,768 --virtual-time-budget=12000 --screenshot='docs/audits/screenshots/overview-ipad-landscape-1024x768.png' 'http://127.0.0.1:5173/'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-ipadp --window-size=768,1024 --virtual-time-budget=12000 --screenshot='docs/audits/screenshots/overview-ipad-portrait-768x1024.png' 'http://127.0.0.1:5173/'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-large --window-size=1920,1080 --virtual-time-budget=8000 --screenshot='docs/audits/screenshots/overview-large-1920x1080.png' 'http://127.0.0.1:5173/'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-donors --window-size=1440,900 --virtual-time-budget=12000 --screenshot='docs/audits/screenshots/donor-directory-1440x900.png' 'http://127.0.0.1:5173/?view=donors'
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --disable-gpu --no-sandbox --user-data-dir=/private/tmp/fip-chrome-audit-loaded --window-size=1440,900 --hide-scrollbars --run-all-compositor-stages-before-draw --virtual-time-budget=45000 --screenshot='docs/audits/screenshots/overview-loaded-1440x900.png' 'http://127.0.0.1:5173/'
```

Browser console/runtime logs also showed repeated React duplicate-key errors for `Awarded to-5Rights Foundation`.

## Docker commands

| Command | Exit | Result |
|---|---:|---|
| `docker info` | 1 initially | Docker daemon unavailable. |
| `open -a 'Docker 3'` | 0 | Installed Docker Desktop variant started; daemon later responsive. |
| `docker-compose build bff` | 1 initially | `docker-credential-desktop` missing. No credential file changed. |
| `mkdir -p /private/tmp/fip-audit-docker-config` and an empty `config.json` (`{}`) created for audit-only Docker config | 0 | Avoided dependency on missing credential helper; contains no credentials. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker-compose build bff` | 0 | Clean dependency install and image build passed; 8.81 GB context, 9.37 GB image. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker image inspect foundationintelligenceplatform-bff` | 0 | ARM64, root/default user, no healthcheck. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker history foundationintelligenceplatform-bff --no-trunc` | 0 | 8.81 GB `COPY src` layer and large build-essential layer identified. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker run --rm ...` inspection commands | 0 | Embedded 2.10 GB active DB and 1.70 GB registry DB confirmed; UID 0. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker-compose up -d bff` | 0 | Development Compose service started. |
| `curl -sS http://127.0.0.1:8000/health` against Compose service | 0 / HTTP 200 | Start health verified. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker-compose restart bff` | 0 | Restart passed; health returned 200. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker-compose down` | 0 | Container/network stopped and removed cleanly. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker run -d --name fip-audit-prodlike -p 127.0.0.1:8001:8000 foundationintelligenceplatform-bff` | 0 | Dockerfile default Uvicorn command started. |
| Immediate `curl` to `http://127.0.0.1:8001/health` | 52 / HTTP 000 | Empty reply during application startup. |
| Repeated `curl -sS http://127.0.0.1:8001/health` | 0 / HTTP 200 | Default-image startup verified after initialization. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker stop fip-audit-prodlike` | 0 | Audit container stopped. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker rm fip-audit-prodlike` | 0 | Audit-only container removed. Image retained for evidence. |

## Final artifact and service validation

| Command | Exit | Result |
|---|---:|---|
| `test -s` plus `wc -l -c` for every required Markdown report | 0 | All eight required/detail reports exist and are non-empty. |
| `awk` status aggregation over `feature-test-matrix.md` | 0 | 74 rows: 32 PASS, 37 PARTIAL, 4 FAIL, 1 NOT TESTABLE. |
| `awk` status aggregation over `requirements-traceability.md` | 0 | 32 rows: 4 PASS, 16 PARTIAL, 9 FAIL, 3 NOT IMPLEMENTED. |
| `find docs/audits/screenshots -maxdepth 1 -type f -name '*.png' \| wc -l` | 0 | Eight screenshots. |
| `sips -g pixelWidth -g pixelHeight docs/audits/screenshots/*.png` (iterated) | 0 | All eight PNG dimensions match their filenames. |
| High-confidence private-key/AWS/Google/OpenAI token-pattern scan over `docs/audits` | 0 | No match. |
| `stat` on staging and restore DB paths | 0 | Both retained at 2,100,543,488 bytes. |
| `git status --short --branch` | 0 | Only `docs/audits/` untracked; branch still ahead 1. |
| OpenAPI `jq` count | 0 | 31 paths and 36 HTTP operations. |
| Final `curl` health calls to ports 8000 and 5173 | 0 / HTTP 200 | Original backend/frontend still running after port-conflict tests. |
| `DOCKER_CONFIG=/private/tmp/fip-audit-docker-config docker ps --format ...` | 0 | No running audit container. |

## Commands deliberately not executed

- No `git commit`, `git push`, AWS CLI, Terraform, CDK, CloudFormation or deployment command.
- No valid active admin pipeline trigger, enrichment, relink, reset or cache mutation.
- No `DELETE`, `DROP`, reset, vacuum, repair or mass-update against the active database.
- No live full scraper/import refresh and no paid AI call during the audit.
- No deletion of staging/restore databases, raw/processed data or the large Docker image.
