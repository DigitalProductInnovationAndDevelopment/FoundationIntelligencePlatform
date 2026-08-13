# Runtime PostgreSQL access architecture

Status: implemented local release gate. AWS role provisioning remains pending the
separate `EXECUTE DB ACCESS PREREQUISITE` approval.

## Principals and secret boundaries

| Principal | Runtime use | Database rights | Secret availability |
|---|---|---|---|
| Reader (`foundation_app`) | Startup pool construction, readiness and every normal read | `CONNECT`, schema `USAGE`, explicit table `SELECT`; default transactions read-only | Application task and migration/release task |
| Writer (`foundation_app_writer`) | Explicitly authorized operator/admin mutations, workers and durable idempotency | `CONNECT`, schema `USAGE`, explicit read/DML/column allowlists below | Application task only after the prerequisite succeeds; prerequisite task |
| Migration/owner (`foundation_admin`) | Alembic, role/grant maintenance and idempotent static bootstrap | Existing privileged RDS migration/owner rights | Migration/release and DB-prerequisite execution roles only |

The application task execution role cannot read the RDS master secret. Missing
writer configuration fails closed with HTTP 503 for a mutation; it never falls
back to reader or migration credentials. Reader readiness remains independent
of writer availability.

## Startup and GET side-effect audit

| Code path | Trigger | Prior operation/table | Reason | Required at startup? | New target |
|---|---|---|---|---|---|
| `bff.main.lifespan` -> `PipelineRepository.synchronize_sources` | Every non-public PostgreSQL startup | `INSERT ... ON CONFLICT DO UPDATE`, `source_configurations` | Persist code-owned source defaults | No | Explicit migration/release bootstrap; insert missing rows only |
| `bff.main.lifespan` -> `GovernanceRepository.synchronize_policies` | Every non-public PostgreSQL startup | `INSERT ... ON CONFLICT DO UPDATE`, `retention_policies` | Persist policy defaults | No | Explicit migration/release bootstrap; insert missing rows only |
| Request audit middleware -> `PostgresAuditSink` | Authenticated request, including GET | `INSERT`, `audit_events` | Durable request audit | No database side effect is acceptable for GET | Structured application logs; no runtime DB grant |
| Readiness and liveness | Health probe | `SELECT` only / no SQL | Runtime health | Yes | Reader pool only |
| Repository construction and dependency resolution | Import/startup/request | None | Construct lazy engines/session factories | Yes | Reader by default; writer only in explicit mutation dependencies |

There are no module-import, startup, lifespan, dependency-initialization,
liveness or readiness DML operations. Cache hydration, funder enrichment and
last-access persistence are not performed by GET handlers. The news endpoints
may call an external provider when separately configured, but do not persist a
database mutation.

## Productive mutation matrix

All listed idempotent HTTP mutations additionally use the writer-backed
`idempotency_records` store (`SELECT`, `INSERT`, `UPDATE`, `DELETE`). Backend
RBAC is evaluated before the writer dependency.

| Endpoint / job | App role | Repository operation | Tables read | Tables written | SQL privileges/session |
|---|---|---|---|---|---|
| `POST /api/charities/directory/organizations/enrich` | operator | `PostgresJobRepository.enqueue` | `dataset_versions`, `job_runs` | `job_runs`, `job_dispatch_outbox`, `job_events` | writer: `SELECT`, `INSERT` |
| `POST /api/charities/grants/funders/enrich` | operator | `PostgresJobRepository.enqueue` | `dataset_versions`, `job_runs` | `job_runs`, `job_dispatch_outbox`, `job_events` | writer: `SELECT`, `INSERT` |
| `POST /api/charities/grants/funders/{key}/profile-cache` | operator | `SourceFunderRepository.queue_profile_cache` | `dataset_versions`, `grant_source_funder_facts`, `source_funder_link_overrides`, `source_funder_profile_cache`, `job_runs` | `job_runs`, `source_funder_profile_cache` | writer: allowlisted `SELECT`, `INSERT`, `UPDATE` |
| `POST /api/charities/grants/funders/{key}/reset-to-observed` | admin | `SourceFunderRepository.reset` | `dataset_versions`, `grant_source_funder_facts`, `source_funder_link_overrides` | `source_funder_link_overrides`, `source_funder_profile_cache` | writer: `SELECT`, `INSERT`, `UPDATE`, `DELETE` |
| `POST /api/charities/grants/funders/{key}/relink` | admin | `SourceFunderRepository.relink` | previous set plus `charities` | `source_funder_link_overrides`, `source_funder_profile_cache` | writer: allowlisted DML |
| `POST /api/admin/pipeline/trigger` | operator | `PostgresJobRepository.enqueue` | `dataset_versions`, `job_runs` | `job_runs`, `job_dispatch_outbox`, `job_events` | writer: `SELECT`, `INSERT` |
| `PUT /api/admin/pipeline/sources/{name}/schedule` | admin | `PipelineRepository.set_source_enabled` | `source_configurations` | `source_configurations.enabled`, `.updated_at` | writer: `SELECT`, column `UPDATE` only |
| `POST /api/admin/governance/retention/dry-run` | admin | `active_holds`, `record_retention_plan` | `data_holds` | `retention_actions`, `deletion_manifests` | writer: `SELECT`, `INSERT` |
| `POST /api/admin/governance/holds` | admin | `create_hold` | none | `data_holds` | writer: `INSERT` |
| `POST /api/admin/governance/holds/{id}/release` | admin | `release_hold` | `data_holds` | `data_holds` | writer: `SELECT`, `UPDATE` |
| `POST /api/admin/governance/data-subject-requests` | admin | `create_data_subject_request` | none | `data_subject_requests` | writer: `INSERT` |
| Durable worker claim/heartbeat/succeed/fail/requeue | internal operator worker | `PostgresJobRepository` worker methods | `job_runs`, `job_events` | `job_runs`, `job_events`, `worker_heartbeats` | writer: allowlisted `SELECT`, `INSERT`, `UPDATE` |
| Durable outbox publisher | internal operator worker | dispatch methods | `job_dispatch_outbox` | `job_dispatch_outbox` | writer: `SELECT`, `UPDATE` |
| Source ingestion worker | internal operator worker | `PipelineRepository` ingestion methods | `dataset_versions`, `source_configurations`, `source_ingestion_runs` | `source_ingestion_runs`, `storage_objects`, `ingestion_run_manifests` | writer: allowlisted `SELECT`, `INSERT`, `UPDATE` |
| Restore evidence | explicit release/governance worker | `record_restore_verification` | none | `restore_verifications` | writer: `INSERT` |

`POST /api/charities/{number}/score` is a pure calculation and remains on the
reader. Cognito user management changes Cognito only and receives no database
session. Static source/policy synchronization is not reachable from an HTTP
route or normal worker startup; it is release-path bootstrap code.

## Exact database allowlists

Reader `SELECT` tables:

```text
alembic_version
analytics_country_aggregates
analytics_country_connections
analytics_country_funder_rankings
analytics_entity_rankings
analytics_filter_values
analytics_funder_relationships
analytics_period_aggregates
analytics_programme_aggregates
analytics_scope_totals
charities
charity_registry_organizations
data_holds
dataset_versions
export_jobs
grant_beneficiary_countries
grant_overview_facts
grant_programme_categories
grant_source_funder_facts
grants
job_dispatch_outbox
job_events
job_runs
materialization_versions
organization_registry_links
retention_policies
source_configurations
source_funder_link_overrides
source_funder_profile_cache
source_ingestion_runs
```

Writer `SELECT` tables:

```text
charities
data_holds
dataset_versions
grant_source_funder_facts
idempotency_records
job_dispatch_outbox
job_events
job_runs
source_configurations
source_funder_link_overrides
source_funder_profile_cache
source_ingestion_runs
worker_heartbeats
```

Writer table DML:

| Table | Privileges |
|---|---|
| `data_holds` | `INSERT`, `UPDATE` |
| `data_subject_requests` | `INSERT` |
| `deletion_manifests` | `INSERT` |
| `idempotency_records` | `INSERT`, `UPDATE`, `DELETE` |
| `ingestion_run_manifests` | `INSERT` |
| `job_dispatch_outbox` | `INSERT`, `UPDATE` |
| `job_events` | `INSERT` |
| `job_runs` | `INSERT`, `UPDATE` |
| `restore_verifications` | `INSERT` |
| `retention_actions` | `INSERT` |
| `source_funder_link_overrides` | `INSERT`, `UPDATE` |
| `source_funder_profile_cache` | `INSERT`, `UPDATE`, `DELETE` |
| `source_ingestion_runs` | `INSERT`, `UPDATE` |
| `storage_objects` | `INSERT` |
| `worker_heartbeats` | `INSERT`, `UPDATE` |
| `source_configurations` | column-level `UPDATE(enabled, updated_at)` |

No runtime sequence privilege is required: runtime identifiers are UUIDs and
the identity-backed dataset version is release-owned. No blanket current or
default table DML/SELECT grant is used. Provisioning first demotes role
attributes, revokes role memberships and existing database/schema/table/column/
sequence/function/default privileges, then applies the exact allowlists.

## Prerequisite order and rollback boundary

1. Create and review the separate DB-access prerequisite stack. It may create
   only the generated writer secret, log group, one-off ARM64 task definition
   and its narrowly scoped roles.
2. Do not update the application service. The existing task continues with the
   reader secret and existing image.
3. Only after the exact execute gate, execute the prerequisite stack and then
   explicitly run its task in the existing private ECS/RDS network.
4. The task acquires an advisory lock, inserts only missing static defaults,
   configures exact reader/writer grants and verifies both principals. It does
   not migrate datasets or change schema.
5. Only after successful AWS permission evidence may the main stack receive the
   writer secret ARN and a new application image.

The prerequisite template does not contain an ECS service, RDS, VPC, ALB,
CloudFront or Cognito resource and cannot perform a service cutover by itself.
