# Configuration

Configuration comes from two places: environment variables (runtime and secrets) and
versioned JSON files under `config/` (policy and definitions).

The guiding rule is **fail closed**. Configuration errors raise at import time rather than
letting the process start with an unsafe posture — `validate_security_settings()` in
`src/bff/config.py:162` raises `SecurityConfigurationError`, and
`TransitionSettings.validate()` in `src/transition/runtime.py:49` raises
`TransitionConfigurationError`.

## Environment variables

The complete reference — every variable, its default, and its validation boundary — is
**`docs/remediation/environment-variable-reference.md`**. It is verified against the code
and is the document to trust.

Start from `.env.example`, which is well commented and groups variables by concern. The
root `.env` is Git-ignored and is loaded only when `APP_ENV` is `development` or `test`
(`src/bff/config.py:18`) — it is deliberately ignored in staging and production, where
values must be injected by the runtime.

The variables you will actually touch:

| Purpose | Variables |
|---|---|
| Environment | `APP_ENV`, `DATA_RUNTIME_MODE` |
| Local auth (required to use the API at all) | `AUTH_MODE=development`, `DEV_AUTH_ENABLED`, `DEV_AUTH_USERNAME`, `DEV_AUTH_PASSWORD`, `DEV_AUTH_SECRET` (≥32 chars) |
| Production auth | `AUTH_MODE=oidc`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` or `OIDC_JWKS_JSON` |
| Database | `DATABASE_URL`, or `DATABASE_HOST`/`PORT`/`NAME`/`USER` plus `DATABASE_PASSWORD_FILE` |
| Transport | `CORS_ORIGINS`, `RATE_LIMIT_*`, `MAX_REQUEST_BODY_BYTES`, `REQUEST_TIMEOUT_SECONDS` |
| Local pipeline paths | `DB_PATH`, `DATA_PATH`, `SCORE_CONFIG_PATH` |

Secrets rules, enforced in code:

- Prefer `DATABASE_PASSWORD_FILE` (a mounted file) over `DATABASE_PASSWORD`.
- `DEV_AUTH_SECRET` must be at least 32 characters.
- Wildcard `CORS_ORIGINS` is rejected outright; production additionally requires HTTPS.
- The proxy refuses to forward browser `Authorization` or `Cookie` headers.
- `VITE_*` values are compiled into the JavaScript bundle and are **public**. Never place a
  credential in one.
- Terraform variables do not accept AWS credentials; deployment identity uses GitHub OIDC.

## `config/` — versioned policy files

These are executable configuration, not documentation. Code reads them at runtime and
validates `configuration_version`.

### `runtime-transition.json`

Selects the storage runtime. `default_operational_mode` is `postgresql`;
`temporary_modes` are `sqlite_migration_source` and `shadow_compare`;
`production_fallback` is `false`. `shadow` bounds the comparison middleware (queue depth,
response byte cap, timeout, recorded-difference cap, approved-unordered paths, ignored
operational paths). `journeys` lists the 21 compared journeys.

Read by `src/transition/runtime.py`. `DATA_RUNTIME_MODE` overrides the default mode.

### `observability.json`

The executable telemetry definition: `service`, `expected_schema_version` (currently
`0006_governance_retention`), 21 `metrics` and 15 `alarms`.

`expected_schema_version` is what the readiness probe and migration gate compare the live
Alembic revision against — **update it whenever you add a migration**, or readiness will
fail. Coverage of API latency/errors, DB pool, query latency, cache hit rate,
dataset/source age, pipeline duration/failures, conversion gaps, programme/geography
coverage, queue age, DLQ, worker failures, task restarts and estimated cost.

Alarm thresholds derive from the locally reconciled dataset. The USD 500 cost threshold is
a proposed fail-safe, not an approved budget. Owners must approve production thresholds.
Terraform maps these same definitions to CloudWatch — none has been created in AWS.

Read by `src/observability/metrics.py`; exposed at `/api/admin/observability/metrics`.

### `data-governance.json`

The authoritative governance configuration. Three flags matter most, and all are
restrictive by default:

```json
"policy_status": "proposed",
"destructive_deletion_enabled": false,
"production_activation_approved": false,
"restore_before_delete_required": true
```

Also carries 6 `data_owners`, 14 `classifications`, `field_exposure_policies` for 5
serializers, `log_redaction` rules, a 7-item `privacy_checklist`, `backup_policy`,
`pitr_policy`, `service_recovery` targets and a 9-step `data_subject_workflow`.

Read by `src/governance/retention.py` and `src/governance/exposure.py`. Retention routes
plan and report; with `destructive_deletion_enabled: false` they never delete. Legal and
privacy approval is **not** implied by this file — see
`docs/remediation/data-governance-register.md`.

### `source-pipelines.json`

Eight source definitions, each carrying `source_name`, `source_owner`, `technical_owner`,
`legal_status` and schedule state. `legal_status` is currently `unresolved` for at least
one source, and an unresolved status **blocks schedule enablement** through
`PUT /api/admin/pipeline/sources/{source_name}/schedule`. That is intentional fail-closed
behaviour, not a bug.

Read by `src/pipelines/durable.py` (`load_source_configurations`). The readiness probe
checks that this configuration is synchronized with the database.

### `scoring.example.json`

The **experimental** relevance score configuration: `score_version`
(`example-relevance-v2`), `configuration_status: "experimental"`, `score_target`,
`missing_data_behavior` (`zero_for_missing_components`), `review_confidence_threshold`
(0.6), five component `weights`, an `example_target_profile` and four stated
`assumptions`.

Read by `src/scoring/engine.py`, overridable with `SCORE_CONFIG_PATH`. These weights carry
**no client approval**. Any production use requires an approved target definition,
approved weights and an approved decision policy, none of which exist in the repository.

### `config/golden/`

`api-contract.json` and `transition-domain.json` are golden fixtures asserted by
`src/tests/test_api_golden.py` and the transition tests. Changing an API response shape
means updating these deliberately — that is the point.

## Adding configuration

1. Add the variable to `src/bff/config.py` (or the relevant settings class) with an
   explicit default and a validation rule.
2. Add it to `.env.example` with a comment.
3. Add it to `docs/remediation/environment-variable-reference.md`.
4. If it affects deployment, thread it through `docker-compose.yml`, the `Dockerfile` and
   the Terraform task definition in `infra/terraform/modules/platform/compute.tf`.

For a new `config/*.json` file, bump and check `configuration_version` in the loader — all
existing loaders reject unknown versions rather than guessing.
