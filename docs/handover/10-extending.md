# Extending the system

Each section names an existing implementation to copy. Following the established pattern
matters more here than usual, because several invariants (dataset versioning, provenance
separation, idempotency, fail-closed configuration) are enforced by tests that will reject
work that ignores them.

**Before editing any route or repository:** confirm you are in `src/bff/postgres/`, not
`src/bff/charity.py`. See [02-architecture.md](02-architecture.md).

---

## Add an API endpoint

**Copy:** `GET /api/charities/grants/themes` in `src/bff/postgres/routes.py`.

1. **Schema** — add request/response models to `src/bff/schemas.py`. Response models are
   the API contract; be explicit about optional fields and coverage/status fields.
2. **Repository method** — add the query to the owning repository under
   `src/bff/postgres/`, and declare it on the matching `Protocol` in
   `src/bff/postgres/interfaces.py`. All SQL lives here, never in the route.
3. **Route** — add a thin handler to `src/bff/postgres/routes.py` that resolves the
   repository via the module's `_organizations(request)` / `_analytics(request)` helpers
   and returns the model.
4. **Authorization** — the router already applies `require_roles(Role.VIEWER, ...)`. For a
   higher requirement, add an explicit `dependencies=[Depends(require_roles(Role.OPERATOR,
   action="...", idempotent=True))]`. Choose an action string that matches the existing
   `domain.verb` convention.
5. **Mutations** — pass `idempotent=True` so an `Idempotency-Key` header is required and
   durably reserved. Never mutate without it.
6. **Document** — add the route to [05-api-reference.md](05-api-reference.md) *and* to
   `docs/remediation/aws-postgres-route-inventory.md` with its role and idempotency.
7. **Test** — extend `src/tests/test_bff.py` or the matching domain test. If the response
   shape is part of the golden contract, update `config/golden/api-contract.json`
   deliberately.

## Add a repository

**Copy:** `src/bff/postgres/registry_repository.py`.

- Take a session factory in `__init__`; never construct an engine.
- Every method is `async` and uses SQLAlchemy Core/ORM over asyncpg.
- Add the `Protocol` to `interfaces.py` so handlers depend on the interface.
- Filter by dataset version wherever the table is dataset-scoped.
- Bound every list query. Existing caps: 100 registry rows, 250 country connections, 50
  funder relationships per funder.
- Paginate with a deterministic cursor — a sort key plus a unique tie-break, encoded
  opaquely. `registry_repository.py` shows the rank/ID pattern.

## Add a database migration

```bash
PYTHONPATH=src venv/bin/alembic revision -m "0007_your_change"
PYTHONPATH=src venv/bin/alembic upgrade head
PYTHONPATH=src venv/bin/alembic downgrade -1   # prove it reverses
PYTHONPATH=src venv/bin/alembic upgrade head
```

Rules:

- Follow the `000N_snake_case_name` convention.
- Write a real `downgrade()`. The CI migration job upgrades from an empty database and
  the local gate proves `downgrade base` leaves only `alembic_version`.
- Dataset-scoped tables must include the dataset version in their primary and foreign
  keys.
- Money is `NUMERIC(24,4)`, rates `NUMERIC(24,12)`, derived minor units `BIGINT`. Never
  float.
- Anything queried or filtered is a typed column. JSONB is only for raw payloads,
  evidence, manifests and audit detail.
- Declare explicit `ON UPDATE` / `ON DELETE` behaviour on every relationship.
- **Update `expected_schema_version` in `config/observability.json`.** Readiness compares
  the live revision against it and will fail otherwise.
- If you add an aggregate table, extend `refresh_analytics_materializations()`.
- Update [06-data-model.md](06-data-model.md) and
  `docs/remediation/aws-postgres-schema.md`.

## Add a data source (scraper)

**Copy:** `src/scrapers/philea.py` for a simple cached source,
`src/scrapers/register_of_charities.py` for an API-backed one.

1. Write the collector in `src/scrapers/`. It must cache raw responses and be resumable.
2. Retain provenance on every record: source name, source record ID, source URL where
   supplied, ingestion timestamp, raw payload.
3. Map records into common organization/grant shape in
   `src/preprocessing/consolidate.py`, or write an adapter like
   `src/preprocessing/philea_adapter.py` if identity handling is non-trivial.
4. Assign non-colliding IDs. Philea uses deterministic **negative** local IDs precisely so
   they cannot collide with positive UK charity registration numbers.
5. Deduplicate conservatively. The established thresholds: exact normalized name or domain
   merges across sources; fuzzy ≥0.92 auto-merges across sources only; 0.82–0.92 becomes a
   *review candidate*, not a merge.
6. Register the source in `config/source-pipelines.json` with `source_owner`,
   `technical_owner` and `legal_status`. **An unresolved `legal_status` blocks schedule
   enablement** — that is intended.
7. Add a test with a cached fixture. See `src/tests/test_source_funders.py`.

Never let a new source's values overwrite a source fact from another source. Add, do not
merge.

## Add a pipeline

**Copy:** `src/pipelines/import_observed_360giving_grants.py`.

- Make it idempotent and resumable — it will be retried.
- Write to a staging location, validate, then swap atomically. `src/data/db_loader.py`
  shows the pattern: a failed load must leave the active dataset untouched.
- Register it as a job type in `src/pipelines/durable.py` so it can be enqueued through
  `POST /api/admin/pipeline/trigger` rather than run as a subprocess.
- Emit structured job events; do not print.
- Add it to the allowed mode list, which is bounded and validated.

## Add enrichment rules

**Edit:** `src/preprocessing/enrichment.py`. It is the single active source of programme
and geography taxonomy — do not create a parallel one.

- Version the rule set. Stored classifications record which version produced them.
- Write to `*_inferred` fields only. Never touch `*_source`.
- Emit evidence and a confidence value with every classification.
- Keep `headquarters_country` separate from `beneficiary_geography_normalized`. They are
  different facts and conflating them silently corrupts the map.
- Re-run `src/pipelines/reclassify_grant_enrichment.py` to reclassify stored data
  atomically against the new taxonomy.
- Report coverage. Never fill a gap with a guess.

## Add a frontend view

**Copy:** `frontend/src/components/RegistryDirectory.tsx`.

1. Create the component in `frontend/src/components/`, export default, accept `apiBase`
   and `online` props like its siblings.
2. Lazy-import it in `App.tsx` (`const X = lazy(() => import("./components/X"))`) — the
   bundle budget depends on this.
3. Fetch with `credentials: "include"` and an `AbortController` signal.
4. For mutations, use `mutationHeaders(reason)` from `frontend/src/lib/http.ts`.
5. If it uses grant filters, take them from `frontend/src/lib/grantScope.ts` rather than
   inventing a shape.
6. Model loading per section (`idle | loading | ready | empty | partial | error`), not
   globally. Distinguish *empty* from *error* from *unknown* in the UI.
7. Run `npm run build` — it enforces 120 KiB initial JS, 25 KiB initial CSS and 425 KiB per
   deferred chunk (gzip).

## Add configuration

1. Add it to `src/bff/config.py` with an explicit default and a validation rule that fails
   closed.
2. Add it to `.env.example` with a comment.
3. Add it to `docs/remediation/environment-variable-reference.md`.
4. Thread it through `docker-compose.yml`, the `Dockerfile` and the Terraform task
   definition in `infra/terraform/modules/platform/compute.tf` if it affects deployment.

## Add a dependency

Dependencies are hash-pinned and the supply chain is gated.

```bash
# edit requirements.in or requirements-runtime.in, then
./venv/bin/pip-compile --generate-hashes requirements.in
```

Then re-run `scripts/generate_sbom.py` and `scripts/check_licenses.py`. The licence gate
rejects AGPL, SSPL and GPL declarations. `src/tests/test_supply_chain.py` will fail if the
locks and manifests disagree.

## Invariants you must not break

1. Source facts, inferred values and platform-derived values stay in separate fields.
2. Absent data is explicit (`transaction_data_unavailable`, `organization_level_only`),
   never zero.
3. Amounts are never summed across currencies; multi-country amounts are excluded and
   counted, never split or duplicated.
4. Exactly one active dataset; activation happens in a single transaction after
   reconciliation.
5. `audit_events` is append-only.
6. Every mutation requires an `Idempotency-Key`.
7. No anonymous access to `/api/*`; no `AUTH_MODE` other than `oidc` in staging or
   production.
8. Configuration errors fail at import, not at first request.
9. The runtime image contains no data.
