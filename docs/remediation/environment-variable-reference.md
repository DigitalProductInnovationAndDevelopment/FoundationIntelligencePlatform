# Environment variable reference

| Variable | Required | Boundary |
|---|---:|---|
| `APP_ENV` | Yes operationally | `development`, `test`, `staging`, `production` |
| `DATA_RUNTIME_MODE` | No | Defaults to `postgresql`; SQLite source is local/test only |
| `SHADOW_SQLITE_PATH` | Shadow only | Separate coherent snapshot; cannot equal active `DB_PATH` |
| `AUTH_MODE` | Yes outside test | Staging/production require `oidc` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | OIDC | Exact identity contract |
| `OIDC_JWKS_URL` or `OIDC_JWKS_JSON` | OIDC | HTTPS URL outside development or injected static JWKS |
| `OIDC_ALGORITHMS` | No | Explicit asymmetric allowlist, default `RS256` |
| `CORS_ORIGINS` | Yes operationally | Explicit HTTPS origins in staging/production; no wildcard |
| `DATABASE_URL` | Alternative | Must be `postgresql+asyncpg` |
| `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER` | Alternative | PostgreSQL endpoint components |
| `DATABASE_PASSWORD_FILE` | Preferred | Mounted runtime-only secret file |
| `DATABASE_PASSWORD` | Alternative | Runtime-injected secret; never log/commit |
| `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW` | No | Positive bounded pool values |
| `DATABASE_POOL_TIMEOUT_SECONDS` | No | Positive checkout timeout |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | No | Positive connection/readiness timeout |
| `DATABASE_STATEMENT_TIMEOUT_MS` | No | Positive server statement limit |
| `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | No | Per-actor process/app limit |
| `MAX_REQUEST_BODY_BYTES`, `REQUEST_TIMEOUT_SECONDS` | No | Request safety bounds |
| `CORE_PROXY_ENABLED` | No | Default false |
| `CORE_API_URL`, `CORE_API_ALLOWED_HOSTS` | Proxy only | Exact absolute target and host allowlist |
| `CORE_PROXY_ALLOWED_PATHS`, `CORE_PROXY_ALLOWED_METHODS` | Proxy only | Required allowlists |
| `SCORE_CONFIG_PATH` | No | Reviewed scoring configuration |
| `DB_PATH` | Migration/local only | SQLite source path; never production fallback |
| `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` | Optional | Live news requires separate network/cost approval |

Frontend `VITE_*` values are public build-time configuration and must never hold
secrets. Terraform variables do not accept AWS credentials; deployment identity
uses GitHub OIDC.
