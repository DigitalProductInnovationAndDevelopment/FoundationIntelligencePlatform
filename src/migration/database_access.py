"""Least-privilege PostgreSQL runtime role provisioning and verification.

This module is invoked only by an explicit migration/release task. The
long-running application imports the resulting reader/writer credentials but
never receives the master credential used here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Iterable, Mapping
import uuid

import asyncpg

from governance.retention import GovernanceConfiguration, load_governance_configuration
from pipelines.durable import SourceConfiguration, load_source_configurations


DATABASE_ACCESS_LOCK_ID = 2_083_370_803_871
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Every relation read by the PostgreSQL API, readiness contract, and sanitized
# operational read surface is explicit. New tables fail closed until reviewed.
READER_TABLES = (
    "alembic_version",
    "analytics_country_aggregates",
    "analytics_country_connections",
    "analytics_country_funder_rankings",
    "analytics_entity_rankings",
    "analytics_filter_values",
    "analytics_funder_relationships",
    "analytics_period_aggregates",
    "analytics_programme_aggregates",
    "analytics_scope_totals",
    "charities",
    "charity_registry_organizations",
    "data_holds",
    "dataset_versions",
    "export_jobs",
    "grant_beneficiary_countries",
    "grant_overview_facts",
    "grant_programme_categories",
    "grant_source_funder_facts",
    "grants",
    "job_dispatch_outbox",
    "job_events",
    "job_runs",
    "materialization_versions",
    "organization_registry_links",
    "retention_policies",
    "source_configurations",
    "source_funder_link_overrides",
    "source_funder_profile_cache",
    "source_ingestion_runs",
)

# Writer SELECT is limited to state needed by an explicit mutation or worker.
WRITER_READ_TABLES = (
    "charities",
    "data_holds",
    "dataset_versions",
    "grant_source_funder_facts",
    "idempotency_records",
    "job_dispatch_outbox",
    "job_events",
    "job_runs",
    "source_configurations",
    "source_funder_link_overrides",
    "source_funder_profile_cache",
    "source_ingestion_runs",
    "worker_heartbeats",
)

# No GRANT ALL and no blanket DML. These privileges map one-to-one to audited
# repository functions and background worker transitions.
WRITER_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "data_holds": ("INSERT", "UPDATE"),
    "data_subject_requests": ("INSERT",),
    "deletion_manifests": ("INSERT",),
    "idempotency_records": ("INSERT", "UPDATE", "DELETE"),
    "ingestion_run_manifests": ("INSERT",),
    "job_dispatch_outbox": ("INSERT", "UPDATE"),
    "job_events": ("INSERT",),
    "job_runs": ("INSERT", "UPDATE"),
    "restore_verifications": ("INSERT",),
    "retention_actions": ("INSERT",),
    "source_funder_link_overrides": ("INSERT", "UPDATE"),
    "source_funder_profile_cache": ("INSERT", "UPDATE", "DELETE"),
    "source_ingestion_runs": ("INSERT", "UPDATE"),
    "storage_objects": ("INSERT",),
    "worker_heartbeats": ("INSERT", "UPDATE"),
}
WRITER_COLUMN_PRIVILEGES: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "source_configurations": {"UPDATE": ("enabled", "updated_at")},
}
WRITER_SEQUENCES: tuple[str, ...] = ()


class DatabaseAccessConfigurationError(RuntimeError):
    """Raised before privileged SQL when prerequisite input is unsafe."""


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise DatabaseAccessConfigurationError(f"{name} is required")
    return value


def identifier(value: str, name: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise DatabaseAccessConfigurationError(f"{name} is not a safe PostgreSQL identifier")
    return value


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quoted_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def connect_admin(environment: Mapping[str, str]) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=_required(environment, "DATABASE_HOST"),
        port=int(environment.get("DATABASE_PORT", "5432")),
        user=_required(environment, "DATABASE_ADMIN_USER"),
        password=_required(environment, "DATABASE_ADMIN_PASSWORD"),
        database=_required(environment, "DATABASE_NAME"),
        ssl=environment.get("DATABASE_SSL_MODE", "disable").strip().lower(),
        command_timeout=None,
    )


async def _configure_login_role(
    connection: asyncpg.Connection,
    *,
    username: str,
    password: str,
    default_read_only: bool,
) -> None:
    role = _quoted_identifier(username)
    password_literal = _quoted_literal(password)
    exists = await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)", username
    )
    attributes = (
        f"LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
        f"NOREPLICATION NOBYPASSRLS PASSWORD {password_literal}"
    )
    if exists:
        await connection.execute(f"ALTER ROLE {role} {attributes}")
    else:
        await connection.execute(f"CREATE ROLE {role} {attributes}")
    await connection.execute(
        f"ALTER ROLE {role} SET default_transaction_read_only = "
        f"{'on' if default_read_only else 'off'}"
    )


async def _reset_runtime_privileges(
    connection: asyncpg.Connection,
    *,
    username: str,
    database_name: str,
    owner_username: str,
) -> None:
    role = _quoted_identifier(username)
    database = _quoted_identifier(database_name)
    owner = _quoted_identifier(owner_username)
    await connection.execute(f"REVOKE ALL ON DATABASE {database} FROM {role}")
    await connection.execute(f"GRANT CONNECT ON DATABASE {database} TO {role}")
    await connection.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
    await connection.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    await connection.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    column_grants = await connection.fetch(
        """
        SELECT table_name, privilege_type,
               array_agg(column_name ORDER BY column_name) AS column_names
        FROM information_schema.column_privileges
        WHERE table_schema='public' AND grantee=$1
        GROUP BY table_name, privilege_type
        ORDER BY table_name, privilege_type
        """,
        username,
    )
    for grant in column_grants:
        columns = ", ".join(
            _quoted_identifier(str(column)) for column in grant["column_names"]
        )
        await connection.execute(
            f"REVOKE {grant['privilege_type']} ({columns}) ON TABLE "
            f"{_quoted_identifier(str(grant['table_name']))} FROM {role}"
        )
    await connection.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
    await connection.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {role}")
    memberships = await connection.fetch(
        """
        SELECT parent.rolname AS parent_role
        FROM pg_auth_members AS membership
        JOIN pg_roles AS parent ON parent.oid=membership.roleid
        JOIN pg_roles AS member ON member.oid=membership.member
        WHERE member.rolname=$1
        ORDER BY parent.rolname
        """,
        username,
    )
    for membership in memberships:
        parent_role = identifier(str(membership["parent_role"]), "parent role")
        await connection.execute(
            f"REVOKE {_quoted_identifier(parent_role)} FROM {role}"
        )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {role}"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON SEQUENCES FROM {role}"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON FUNCTIONS FROM {role}"
    )


async def configure_reader_role(
    connection: asyncpg.Connection,
    environment: Mapping[str, str],
) -> None:
    username = identifier(
        _required(environment, "DATABASE_READER_USER"), "DATABASE_READER_USER"
    )
    password = _required(environment, "DATABASE_READER_PASSWORD")
    owner = identifier(
        _required(environment, "DATABASE_ADMIN_USER"), "DATABASE_ADMIN_USER"
    )
    database = identifier(_required(environment, "DATABASE_NAME"), "DATABASE_NAME")
    await _configure_login_role(
        connection,
        username=username,
        password=password,
        default_read_only=True,
    )
    await connection.execute(
        f"REVOKE ALL ON DATABASE {_quoted_identifier(database)} FROM PUBLIC"
    )
    await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    await connection.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quoted_identifier(owner)} "
        "IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    await _reset_runtime_privileges(
        connection,
        username=username,
        database_name=database,
        owner_username=owner,
    )
    role = _quoted_identifier(username)
    tables = ", ".join(_quoted_identifier(table) for table in READER_TABLES)
    await connection.execute(f"GRANT SELECT ON TABLE {tables} TO {role}")


async def configure_writer_role(
    connection: asyncpg.Connection,
    environment: Mapping[str, str],
) -> None:
    username = identifier(
        _required(environment, "DATABASE_WRITER_USER"), "DATABASE_WRITER_USER"
    )
    password = _required(environment, "DATABASE_WRITER_PASSWORD")
    owner = identifier(
        _required(environment, "DATABASE_ADMIN_USER"), "DATABASE_ADMIN_USER"
    )
    database = identifier(_required(environment, "DATABASE_NAME"), "DATABASE_NAME")
    await _configure_login_role(
        connection,
        username=username,
        password=password,
        default_read_only=False,
    )
    await _reset_runtime_privileges(
        connection,
        username=username,
        database_name=database,
        owner_username=owner,
    )
    role = _quoted_identifier(username)
    read_tables = ", ".join(
        _quoted_identifier(table) for table in WRITER_READ_TABLES
    )
    await connection.execute(f"GRANT SELECT ON TABLE {read_tables} TO {role}")
    for table, privileges in WRITER_TABLE_PRIVILEGES.items():
        privilege_list = ", ".join(privileges)
        await connection.execute(
            f"GRANT {privilege_list} ON TABLE {_quoted_identifier(table)} TO {role}"
        )
    for table, grants in WRITER_COLUMN_PRIVILEGES.items():
        for privilege, columns in grants.items():
            column_list = ", ".join(_quoted_identifier(column) for column in columns)
            await connection.execute(
                f"GRANT {privilege} ({column_list}) ON TABLE "
                f"{_quoted_identifier(table)} TO {role}"
            )


async def bootstrap_runtime_configuration(
    connection: asyncpg.Connection,
    *,
    sources: Iterable[SourceConfiguration] | None = None,
    governance: GovernanceConfiguration | None = None,
) -> dict[str, int]:
    """Insert only absent static defaults; never overwrite legitimate state."""
    governance_configuration = governance or load_governance_configuration()
    governance_configuration.validate()
    policy_records = [
        policy.database_record(policy_status=governance_configuration.policy_status)
        for policy in governance_configuration.policies
    ]
    inserted_policies = 0
    for record in policy_records:
        result = await connection.execute(
            """
            INSERT INTO retention_policies (
                retention_class, classification, policy_status,
                archive_after_days, delete_after_days, export_expire_after_days,
                destructive_deletion_enabled, production_approved,
                configuration_checksum
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (retention_class) DO NOTHING
            """,
            record["retention_class"],
            record["classification"],
            record["policy_status"],
            record["archive_after_days"],
            record["delete_after_days"],
            record["export_expire_after_days"],
            record["destructive_deletion_enabled"],
            record["production_approved"],
            record["configuration_checksum"],
        )
        inserted_policies += int(result.endswith(" 1"))

    source_records = [
        configuration.database_record()
        for configuration in (sources or load_source_configurations())
    ]
    inserted_sources = 0
    for record in source_records:
        result = await connection.execute(
            """
            INSERT INTO source_configurations (
                source_name, source_owner, technical_owner, legal_status,
                licence_status, terms_url, rate_limit_per_minute, user_agent,
                freshness_sla_hours, schedule_expression, enabled,
                governance_blocked, last_success_at, watermark, classification,
                retention_class, schema_version, credentials_reference,
                retry_limit, timeout_seconds, maximum_pages, maximum_records,
                configuration_checksum
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                CAST($13 AS timestamptz),$14,$15,$16,$17,$18,$19,$20,$21,$22,$23
            )
            ON CONFLICT (source_name) DO NOTHING
            """,
            record["source_name"],
            record["source_owner"],
            record["technical_owner"],
            record["legal_status"],
            record["licence_status"],
            record["terms_url"],
            record["rate_limit_per_minute"],
            record["user_agent"],
            record["freshness_sla_hours"],
            record["schedule_expression"],
            record["enabled"],
            record["governance_blocked"],
            record["last_success"],
            record["watermark"],
            record["classification"],
            record["retention_class"],
            record["schema_version"],
            record["credentials_reference"],
            record["retry_limit"],
            record["timeout_seconds"],
            record["maximum_pages"],
            record["maximum_records"],
            record["configuration_checksum"],
        )
        inserted_sources += int(result.endswith(" 1"))
    return {
        "source_configurations_inserted": inserted_sources,
        "retention_policies_inserted": inserted_policies,
    }


async def _connect_runtime(
    environment: Mapping[str, str], *, prefix: str
) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=_required(environment, "DATABASE_HOST"),
        port=int(environment.get("DATABASE_PORT", "5432")),
        user=_required(environment, f"DATABASE_{prefix}_USER"),
        password=_required(environment, f"DATABASE_{prefix}_PASSWORD"),
        database=_required(environment, "DATABASE_NAME"),
        ssl=environment.get("DATABASE_SSL_MODE", "disable").strip().lower(),
        command_timeout=None,
    )


async def _statement_denied(connection: asyncpg.Connection, statement: str) -> bool:
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute(statement)
    except (
        asyncpg.InsufficientPrivilegeError,
        asyncpg.ReadOnlySQLTransactionError,
    ):
        return True
    finally:
        await transaction.rollback()
    return False


async def verify_reader_role(environment: Mapping[str, str]) -> dict[str, bool]:
    connection = await _connect_runtime(environment, prefix="READER")
    try:
        return {
            "analytics_select_succeeded": (
                await connection.fetchval(
                    "SELECT COUNT(*) >= 0 FROM analytics_scope_totals"
                )
                is True
            ),
            "tls_in_use": bool(
                await connection.fetchval(
                    "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                )
            ),
            "default_read_only": (
                await connection.fetchval("SHOW default_transaction_read_only") == "on"
            ),
            "insert_denied": await _statement_denied(
                connection,
                "INSERT INTO idempotency_records "
                "(actor_id, action, idempotency_key, request_hash, status, expires_at) "
                "VALUES ('permission-check','permission-check','permission-check',"
                "'permission-check','reserved',CURRENT_TIMESTAMP)",
            ),
            "update_denied": await _statement_denied(
                connection, "UPDATE dataset_versions SET status=status WHERE FALSE"
            ),
            "delete_denied": await _statement_denied(
                connection, "DELETE FROM job_runs WHERE FALSE"
            ),
            "ddl_denied": await _statement_denied(
                connection, "CREATE TABLE fip_reader_permission_probe (id integer)"
            ),
            "drop_denied": await _statement_denied(
                connection, "DROP TABLE charities"
            ),
        }
    finally:
        await connection.close()


async def verify_writer_role(environment: Mapping[str, str]) -> dict[str, bool]:
    connection = await _connect_runtime(environment, prefix="WRITER")
    probe = f"permission-check-{uuid.uuid4()}"
    request_hash = uuid.uuid4().hex + uuid.uuid4().hex
    transaction = connection.transaction()
    allowed_dml = False
    try:
        await transaction.start()
        await connection.execute(
            """
            INSERT INTO idempotency_records (
                actor_id, action, idempotency_key, request_hash, status, expires_at
            ) VALUES ($1,'permission-check',$2,$3,'reserved',CURRENT_TIMESTAMP + INTERVAL '1 minute')
            """,
            probe,
            probe,
            request_hash,
        )
        await connection.execute(
            "UPDATE idempotency_records SET status=status WHERE actor_id=$1",
            probe,
        )
        await connection.execute(
            "DELETE FROM idempotency_records WHERE actor_id=$1", probe
        )
        await connection.execute(
            "UPDATE source_configurations SET enabled=enabled WHERE FALSE"
        )
        allowed_dml = True
    finally:
        await transaction.rollback()
    try:
        return {
            "select_succeeded": await connection.fetchval("SELECT 1") == 1,
            "allowed_dml_succeeded": allowed_dml,
            "unlisted_dml_denied": await _statement_denied(
                connection, "UPDATE charities SET name=name WHERE FALSE"
            ),
            "unlisted_column_update_denied": await _statement_denied(
                connection,
                "UPDATE source_configurations "
                "SET credentials_reference=credentials_reference WHERE FALSE",
            ),
            "ddl_denied": await _statement_denied(
                connection, "CREATE TABLE fip_writer_permission_probe (id integer)"
            ),
            "drop_denied": await _statement_denied(
                connection, "DROP TABLE charities"
            ),
            "create_role_denied": await _statement_denied(
                connection, "CREATE ROLE fip_writer_permission_probe"
            ),
        }
    finally:
        await connection.close()


async def grant_snapshot(
    connection: asyncpg.Connection, usernames: Iterable[str]
) -> dict[str, Any]:
    principals = tuple(usernames)
    table_rows = await connection.fetch(
        """
        SELECT grantee, table_name, privilege_type
        FROM information_schema.role_table_grants
        WHERE table_schema='public' AND grantee=ANY($1::text[])
        ORDER BY grantee, table_name, privilege_type
        """,
        principals,
    )
    column_rows = await connection.fetch(
        """
        SELECT grantee, table_name, column_name, privilege_type
        FROM information_schema.role_column_grants
        WHERE table_schema='public' AND grantee=ANY($1::text[])
        ORDER BY grantee, table_name, column_name, privilege_type
        """,
        principals,
    )
    sequence_rows = await connection.fetch(
        """
        SELECT grantee, object_name, privilege_type
        FROM information_schema.role_usage_grants
        WHERE object_schema='public' AND object_type='SEQUENCE'
          AND grantee=ANY($1::text[])
        ORDER BY grantee, object_name, privilege_type
        """,
        principals,
    )
    return {
        "table_privileges": [dict(row) for row in table_rows],
        "column_privileges": [dict(row) for row in column_rows],
        "sequence_privileges": [dict(row) for row in sequence_rows],
    }


async def run(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    task_environment = dict(os.environ if environment is None else environment)
    reader = identifier(
        _required(task_environment, "DATABASE_READER_USER"), "DATABASE_READER_USER"
    )
    writer = identifier(
        _required(task_environment, "DATABASE_WRITER_USER"), "DATABASE_WRITER_USER"
    )
    if reader == writer:
        raise DatabaseAccessConfigurationError("Reader and writer principals must differ")
    admin = await connect_admin(task_environment)
    try:
        acquired = await admin.fetchval(
            "SELECT pg_try_advisory_lock($1)", DATABASE_ACCESS_LOCK_ID
        )
        if not acquired:
            raise DatabaseAccessConfigurationError(
                "another database access prerequisite task holds the lock"
            )
        bootstrap = await bootstrap_runtime_configuration(admin)
        await configure_reader_role(admin, task_environment)
        await configure_writer_role(admin, task_environment)
        snapshot = await grant_snapshot(admin, (reader, writer))
        reader_verification = await verify_reader_role(task_environment)
        writer_verification = await verify_writer_role(task_environment)
        if not all(reader_verification.values()) or not all(writer_verification.values()):
            raise RuntimeError("database access prerequisite verification failed")
        return {
            "reader": reader,
            "writer": writer,
            "bootstrap": bootstrap,
            "reader_verification": reader_verification,
            "writer_verification": writer_verification,
            "grants": snapshot,
        }
    finally:
        await admin.close()


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
