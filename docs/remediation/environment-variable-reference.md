# Environment variable reference

Every variable below is read by code in this repository. Defaults and boundaries are
taken from `src/bff/config.py`, `src/bff/database.py`, `src/transition/runtime.py`,
`src/scoring/engine.py` and the scraper/pipeline modules. Validation is enforced by
`validate_security_settings` (`src/bff/config.py:162`), which raises
`SecurityConfigurationError` at import time rather than starting with an unsafe posture.

"Production" below means `APP_ENV` of `staging` or `production`.

## Environment and runtime mode

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `APP_ENV` | Yes operationally | `development` | `development`, `test`, `staging`, `production` |
| `DATA_RUNTIME_MODE` | No | `postgresql` (from `config/runtime-transition.json`) | `postgresql`, `sqlite_migration_source`, `shadow_compare`. SQLite mode is rejected in staging/production |
| `SHADOW_SQLITE_PATH` | Shadow only | none | Required when mode is `shadow_compare`; must be an existing file and must not equal `DB_PATH` |

## Authentication and session

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `AUTH_MODE` | Yes outside test | `disabled` | `disabled`, `development`, `oidc`. Production requires `oidc` |
| `SESSION_COOKIE_NAME` | No | `session_id` | Cookie name for the local development session |
| `SESSION_COOKIE_SECURE` | No | `true` in production, else `false` | Must be true in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | Positive integer; development session lifetime |

### OIDC (required when `AUTH_MODE=oidc`)

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `OIDC_ISSUER` | OIDC | none | Exact issuer; HTTPS in production |
| `OIDC_AUDIENCE` | OIDC | none | Exact audience |
| `OIDC_JWKS_URL` or `OIDC_JWKS_JSON` | OIDC | none | One is required. URL must be HTTPS in production; JSON must contain a `keys` array |
| `OIDC_ALGORITHMS` | No | `RS256` | Comma-separated asymmetric allowlist from `RS256/384/512`, `ES256/384/512` |
| `OIDC_ROLE_CLAIM` | No | `roles` | Claim name carrying role values |
| `OIDC_JWKS_CACHE_SECONDS` | No | `300` | Positive integer |

### Local development authentication (required when `AUTH_MODE=development`)

This mode is permitted only when `APP_ENV` is `development` or `test`, and is rejected
in production. With any other `AUTH_MODE`, `POST /api/auth/login` returns **404** and
all `/api/*` routes return **401** — there is no anonymous access path.

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `DEV_AUTH_ENABLED` | Development auth | `false` | Must be `true` for `AUTH_MODE=development` |
| `DEV_AUTH_USERNAME` | Development auth | none | No default is supplied; must be set explicitly |
| `DEV_AUTH_PASSWORD` | Development auth | none | No default is supplied; must be set explicitly |
| `DEV_AUTH_SECRET` | Development auth | none | **Minimum 32 characters**; token signing key |
| `DEV_AUTH_ALLOWED_HOSTS` | No | `127.0.0.1,::1,localhost` | Request host must appear here or login returns 404 |

## Transport and request safety

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `CORS_ORIGINS` | Yes operationally | `http://localhost:5173,http://127.0.0.1:5173` | Explicit origins; wildcard forbidden; HTTPS required in production |
| `RATE_LIMIT_REQUESTS` | No | `120` | Positive integer, per actor per window |
| `RATE_LIMIT_WINDOW_SECONDS` | No | `60` | Positive integer |
| `MAX_REQUEST_BODY_BYTES` | No | `1048576` | Positive integer |
| `REQUEST_TIMEOUT_SECONDS` | No | `30` | Positive integer |

## PostgreSQL

Supply either `DATABASE_URL`, or the component variables plus a password source.

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `DATABASE_URL` | Alternative | none | Must resolve to `postgresql+asyncpg`; bare `postgres`/`postgresql` schemes are upgraded to asyncpg |
| `DATABASE_HOST` | Alternative | none | Required with `DATABASE_NAME` and `DATABASE_USER` |
| `DATABASE_PORT` | No | `5432` | Positive integer |
| `DATABASE_NAME` | Alternative | none | Database name |
| `DATABASE_USER` | Alternative | none | Application role |
| `DATABASE_PASSWORD_FILE` | Preferred | none | Mounted runtime-only secret file |
| `DATABASE_PASSWORD` | Alternative | none | Runtime-injected secret; never log or commit |
| `DATABASE_POOL_SIZE` | No | `5` | Positive integer |
| `DATABASE_MAX_OVERFLOW` | No | `5` | Positive integer |
| `DATABASE_POOL_TIMEOUT_SECONDS` | No | `5` | Positive integer |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | No | `5` | Positive integer; also bounds the readiness probe |
| `DATABASE_STATEMENT_TIMEOUT_MS` | No | `30000` | Positive integer server statement limit |

## Local data paths (migration source and pipelines)

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `DB_PATH` | Migration/local only | `src/data/charities.db` | SQLite migration source; never a production fallback |
| `DATA_PATH` | Migration/local only | `src/data/raw/register_of_charities_results.json` | Raw Charity Commission source cache |
| `SCORE_CONFIG_PATH` | No | `config/scoring.example.json` | Reviewed scoring configuration |

## Core API proxy (disabled by default)

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `CORE_PROXY_ENABLED` | No | `false` | Enables `/api/core/{path}` |
| `CORE_API_URL` | Proxy only | `http://127.0.0.1:8080` | Absolute HTTP(S) URL; HTTPS in production |
| `CORE_API_ALLOWED_HOSTS` | Proxy only | empty | Must contain the `CORE_API_URL` host |
| `CORE_PROXY_ALLOWED_PATHS` | Proxy only | empty | Required allowlist when enabled |
| `CORE_PROXY_ALLOWED_METHODS` | No | `GET` | Required allowlist when enabled |
| `CORE_PROXY_FORWARD_HEADERS` | No | `accept,content-type,x-request-id` | `authorization` and `cookie` are rejected |
| `CORE_PROXY_RESPONSE_HEADERS` | No | `content-type,cache-control,etag,last-modified` | Response header allowlist |
| `CORE_API_BEARER_TOKEN` | No | none | Server-side downstream credential; never exposed to the browser |

## Optional external providers

Each requires separate network, cost and licence approval. None is needed for the
cached presentation build.

| Variable | Used by | Notes |
|---|---|---|
| `ANTHROPIC_AUTH_TOKEN` | `src/bff/news.py` | Live news summarisation |
| `ANTHROPIC_BASE_URL` | `src/bff/news.py` | Optional endpoint override |
| `ANTHROPIC_API_KEY` | `src/bff/news.py` | Alternative credential form |
| `CLAUDE_MODEL` | `src/bff/news.py` | Model identifier |
| `GEMINI_API_KEY` | `src/preprocessing/enrich_gemini.py` | Optional non-default enrichment path |
| `CHARITY_COMMISSION_API_KEY` | `src/scrapers/register_of_charities.py` | Live register refresh only |

## Test-only

| Variable | Used by | Notes |
|---|---|---|
| `RUN_POSTGRES_INTEGRATION` | `src/tests/` | Opts into PostgreSQL integration tests |
| `TEST_DATABASE_URL` | `src/tests/` | Isolated test database endpoint |

## Frontend

`VITE_*` values are embedded in the JavaScript bundle at build time and are **public**.
They must never hold a secret, credential or shared key.

| Variable | Required | Default | Boundary |
|---|---|---|---|
| `VITE_API_BASE_URL` | No | current browser hostname on port 8000 | Override only when the API runs elsewhere; mixing `localhost` and `127.0.0.1` breaks cookies |
| `VITE_LEGACY_OVERVIEW` | No | unset | Renders the legacy overview layout when set to exactly `true` |

Terraform variables do not accept AWS credentials; deployment identity uses GitHub OIDC.
