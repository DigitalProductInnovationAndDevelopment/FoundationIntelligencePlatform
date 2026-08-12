"""Least-privilege PostgreSQL runtime role provisioning and verification.

This module is invoked only by an explicit migration/release task. The
long-running application imports the resulting reader/writer credentials but
never receives the master credential used here.
"""

from __future__ import annotations

import asyncio
import hashlib
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
RUNTIME_ROLE_MEMBERSHIP_ALLOWLIST: tuple[str, ...] = ()
PUBLIC_FUNCTION_EXTENSION_ALLOWLIST = ("pg_trgm",)

SOURCE_CONFIGURATION_INTEGRITY_FIELDS = (
    "source_name",
    "source_owner",
    "technical_owner",
    "legal_status",
    "licence_status",
    "terms_url",
    "rate_limit_per_minute",
    "user_agent",
    "freshness_sla_hours",
    "schedule_expression",
    "enabled",
    "governance_blocked",
    "last_success_at",
    "watermark",
    "classification",
    "retention_class",
    "schema_version",
    "retry_limit",
    "timeout_seconds",
    "maximum_pages",
    "maximum_records",
    "configuration_checksum",
)
RETENTION_POLICY_INTEGRITY_FIELDS = (
    "retention_class",
    "classification",
    "policy_status",
    "archive_after_days",
    "delete_after_days",
    "export_expire_after_days",
    "destructive_deletion_enabled",
    "production_approved",
    "approval_reference",
    "configuration_checksum",
)


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
    state = await runtime_role_state(connection, username)
    if state is not None:
        _assert_runtime_role_safe(state, username=username)
        # SUPERUSER/NOSUPERUSER, REPLICATION and BYPASSRLS are deliberately
        # absent here. RDS release principals are not PostgreSQL superusers and
        # cannot alter those attributes, even to their already-safe values.
        await connection.execute(
            f"ALTER ROLE {role} LOGIN NOINHERIT PASSWORD {password_literal}"
        )
    else:
        await connection.execute(
            f"CREATE ROLE {role} LOGIN NOCREATEDB NOCREATEROLE NOINHERIT "
            f"NOREPLICATION NOBYPASSRLS PASSWORD {password_literal}"
        )
    _assert_runtime_role_safe(
        await _required_runtime_role_state(connection, username),
        username=username,
        require_login_posture=True,
    )
    # Read pg_roles again immediately before the second ALTER ROLE. This also
    # makes an unexpected concurrent privilege or membership change fail closed.
    _assert_runtime_role_safe(
        await _required_runtime_role_state(connection, username),
        username=username,
        require_login_posture=True,
    )
    await connection.execute(
        f"ALTER ROLE {role} SET default_transaction_read_only = "
        f"{'on' if default_read_only else 'off'}"
    )
    _assert_runtime_role_safe(
        await _required_runtime_role_state(connection, username),
        username=username,
        require_login_posture=True,
    )


async def runtime_role_state(
    connection: asyncpg.Connection, username: str
) -> dict[str, Any] | None:
    row = await connection.fetchrow(
        """
        SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolreplication,
               rolbypassrls, rolinherit, rolcanlogin
        FROM pg_roles
        WHERE rolname=$1
        """,
        username,
    )
    if row is None:
        return None
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
    state = dict(row)
    state["memberships"] = [str(item["parent_role"]) for item in memberships]
    return state


async def _required_runtime_role_state(
    connection: asyncpg.Connection, username: str
) -> dict[str, Any]:
    state = await runtime_role_state(connection, username)
    if state is None:
        raise DatabaseAccessConfigurationError(
            f"runtime role {username} disappeared during provisioning"
        )
    return state


def _assert_runtime_role_safe(
    state: Mapping[str, Any], *, username: str, require_login_posture: bool = False
) -> None:
    dangerous = (
        "rolsuper",
        "rolcreaterole",
        "rolcreatedb",
        "rolreplication",
        "rolbypassrls",
    )
    enabled = [attribute for attribute in dangerous if bool(state[attribute])]
    if enabled:
        raise DatabaseAccessConfigurationError(
            f"runtime role {username} has prohibited attributes: {', '.join(enabled)}"
        )
    memberships = set(str(item) for item in state.get("memberships", ()))
    unexpected = memberships.difference(RUNTIME_ROLE_MEMBERSHIP_ALLOWLIST)
    if unexpected:
        raise DatabaseAccessConfigurationError(
            f"runtime role {username} has unexpected memberships"
        )
    if require_login_posture and not bool(state["rolcanlogin"]):
        raise DatabaseAccessConfigurationError(
            f"runtime role {username} is not a login role"
        )
    if require_login_posture and bool(state["rolinherit"]):
        raise DatabaseAccessConfigurationError(
            f"runtime role {username} unexpectedly inherits privileges"
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
    await connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    await connection.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quoted_identifier(owner)} "
        "IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quoted_identifier(owner)} "
        "IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC"
    )
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


def _business_row_checksum(row: Mapping[str, Any], *, identifier_field: str) -> str:
    business_fields = dict(row)
    business_fields.pop(identifier_field)
    canonical = json.dumps(
        business_fields,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def default_configuration_snapshot(
    connection: asyncpg.Connection,
) -> dict[str, dict[str, str]]:
    """Return only stable IDs and hashes of explicitly non-sensitive fields."""
    source_rows = await connection.fetch(
        f"SELECT {', '.join(SOURCE_CONFIGURATION_INTEGRITY_FIELDS)} "
        "FROM source_configurations ORDER BY source_name"
    )
    policy_rows = await connection.fetch(
        f"SELECT {', '.join(RETENTION_POLICY_INTEGRITY_FIELDS)} "
        "FROM retention_policies ORDER BY retention_class"
    )
    return {
        "source_configurations": {
            str(row["source_name"]): _business_row_checksum(
                row, identifier_field="source_name"
            )
            for row in source_rows
        },
        "retention_policies": {
            str(row["retention_class"]): _business_row_checksum(
                row, identifier_field="retention_class"
            )
            for row in policy_rows
        },
    }


def default_integrity_evidence(
    *,
    before: Mapping[str, Mapping[str, str]],
    after: Mapping[str, Mapping[str, str]],
    expected_default_ids: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for relation in ("source_configurations", "retention_policies"):
        before_rows = dict(before[relation])
        after_rows = dict(after[relation])
        before_ids = set(before_rows)
        after_ids = set(after_rows)
        configured_ids = set(expected_default_ids[relation])
        expected_added = configured_ids.difference(before_ids)
        actual_added = after_ids.difference(before_ids)
        existing_preserved = before_ids.issubset(after_ids)
        checksums_unchanged = all(
            after_rows.get(stable_id) == checksum
            for stable_id, checksum in before_rows.items()
        )
        only_missing_defaults_added = actual_added == expected_added
        relation_evidence = {
            "pre_ids": sorted(before_ids),
            "post_ids": sorted(after_ids),
            "added_ids": sorted(actual_added),
            "pre_checksums": {
                stable_id: before_rows[stable_id] for stable_id in sorted(before_ids)
            },
            "post_checksums_for_pre_ids": {
                stable_id: after_rows[stable_id]
                for stable_id in sorted(before_ids.intersection(after_ids))
            },
            "existing_ids_preserved": existing_preserved,
            "existing_checksums_unchanged": checksums_unchanged,
            "only_missing_defaults_added": only_missing_defaults_added,
        }
        if not all(
            (
                existing_preserved,
                checksums_unchanged,
                only_missing_defaults_added,
            )
        ):
            raise RuntimeError(f"{relation} bootstrap integrity verification failed")
        evidence[relation] = relation_evidence
    return evidence


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
    reader = identifier(
        _required(environment, "DATABASE_READER_USER"), "DATABASE_READER_USER"
    )
    writer = identifier(
        _required(environment, "DATABASE_WRITER_USER"), "DATABASE_WRITER_USER"
    )
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
            "grant_denied": await _statement_denied(
                connection,
                f"GRANT {_quoted_identifier(writer)} TO {_quoted_identifier(reader)}",
            ),
            "create_role_denied": await _statement_denied(
                connection, "CREATE ROLE fip_reader_permission_probe"
            ),
        }
    finally:
        await connection.close()


async def verify_writer_role(environment: Mapping[str, str]) -> dict[str, bool]:
    connection = await _connect_runtime(environment, prefix="WRITER")
    writer = identifier(
        _required(environment, "DATABASE_WRITER_USER"), "DATABASE_WRITER_USER"
    )
    reader = identifier(
        _required(environment, "DATABASE_READER_USER"), "DATABASE_READER_USER"
    )
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
            "grant_denied": await _statement_denied(
                connection,
                f"GRANT {_quoted_identifier(reader)} TO {_quoted_identifier(writer)}",
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
        SELECT COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
               namespace.nspname AS table_schema,
               relation.relname AS table_name,
               acl.privilege_type
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(relation.relacl, acldefault('r', relation.relowner))
        ) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
        WHERE namespace.nspname='public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (acl.grantee=0 OR grantee.rolname=ANY($1::text[]))
        ORDER BY grantee, table_name, privilege_type
        """,
        principals,
    )
    column_rows = await connection.fetch(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
               namespace.nspname AS table_schema,
               relation.relname AS table_name,
               attribute.attname AS column_name,
               acl.privilege_type
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation ON relation.oid=attribute.attrelid
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        CROSS JOIN LATERAL aclexplode(attribute.attacl) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
        WHERE namespace.nspname='public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND (acl.grantee=0 OR grantee.rolname=ANY($1::text[]))
        ORDER BY grantee, table_name, column_name, privilege_type
        """,
        principals,
    )
    sequence_rows = await connection.fetch(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
               namespace.nspname AS object_schema,
               relation.relname AS object_name,
               acl.privilege_type
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(relation.relacl, acldefault('s', relation.relowner))
        ) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
        WHERE namespace.nspname='public'
          AND relation.relkind='S'
          AND (acl.grantee=0 OR grantee.rolname=ANY($1::text[]))
        ORDER BY grantee, object_name, privilege_type
        """,
        principals,
    )
    function_rows = await connection.fetch(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
               namespace.nspname AS object_schema,
               routine.proname AS object_name,
               extension.extname AS extension_name,
               acl.privilege_type
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid=routine.pronamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(routine.proacl, acldefault('f', routine.proowner))
        ) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
        LEFT JOIN pg_depend AS dependency
          ON dependency.classid='pg_proc'::regclass
         AND dependency.objid=routine.oid
         AND dependency.deptype='e'
        LEFT JOIN pg_extension AS extension ON extension.oid=dependency.refobjid
        WHERE namespace.nspname='public'
          AND (acl.grantee=0 OR grantee.rolname=ANY($1::text[]))
        ORDER BY grantee, object_name, privilege_type
        """,
        principals,
    )
    database_rows = await connection.fetch(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
               database.datname AS database_name,
               acl.privilege_type
        FROM pg_database AS database
        CROSS JOIN LATERAL aclexplode(
            COALESCE(database.datacl, acldefault('d', database.datdba))
        ) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
        WHERE database.datname=current_database()
          AND (acl.grantee=0 OR grantee.rolname=ANY($1::text[]))
        ORDER BY grantee, privilege_type
        """,
        principals,
    )
    schema_rows = await connection.fetch(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
               namespace.nspname AS schema_name,
               acl.privilege_type
        FROM pg_namespace AS namespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(namespace.nspacl, acldefault('n', namespace.nspowner))
        ) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid=acl.grantee
        WHERE namespace.nspname='public'
          AND (acl.grantee=0 OR grantee.rolname=ANY($1::text[]))
        ORDER BY grantee, privilege_type
        """,
        principals,
    )
    return {
        "database_privileges": [dict(row) for row in database_rows],
        "schema_privileges": [dict(row) for row in schema_rows],
        "table_privileges": [dict(row) for row in table_rows],
        "column_privileges": [dict(row) for row in column_rows],
        "sequence_privileges": [dict(row) for row in sequence_rows],
        "function_privileges": [dict(row) for row in function_rows],
    }


def _expected_grants(
    *, reader: str, writer: str, database_name: str
) -> dict[str, set[tuple[str, ...]]]:
    table_privileges: set[tuple[str, ...]] = {
        (reader, table, "SELECT") for table in READER_TABLES
    }
    table_privileges.update(
        (writer, table, "SELECT") for table in WRITER_READ_TABLES
    )
    table_privileges.update(
        (writer, table, privilege)
        for table, privileges in WRITER_TABLE_PRIVILEGES.items()
        for privilege in privileges
    )
    column_privileges: set[tuple[str, ...]] = {
        (writer, table, column, privilege)
        for table, grants in WRITER_COLUMN_PRIVILEGES.items()
        for privilege, columns in grants.items()
        for column in columns
    }
    return {
        "database_privileges": {
            (reader, database_name, "CONNECT"),
            (writer, database_name, "CONNECT"),
        },
        "schema_privileges": {
            (reader, "public", "USAGE"),
            (writer, "public", "USAGE"),
        },
        "table_privileges": table_privileges,
        "column_privileges": column_privileges,
        "sequence_privileges": set(),
        "function_privileges": set(),
    }


def grant_equality_evidence(
    snapshot: Mapping[str, Any], *, reader: str, writer: str, database_name: str
) -> dict[str, bool]:
    expected = _expected_grants(
        reader=reader, writer=writer, database_name=database_name
    )
    projections = {
        "database_privileges": ("grantee", "database_name", "privilege_type"),
        "schema_privileges": ("grantee", "schema_name", "privilege_type"),
        "table_privileges": ("grantee", "table_name", "privilege_type"),
        "column_privileges": (
            "grantee",
            "table_name",
            "column_name",
            "privilege_type",
        ),
        "sequence_privileges": ("grantee", "object_name", "privilege_type"),
        "function_privileges": ("grantee", "object_name", "privilege_type"),
    }
    evidence = {
        name: {
            tuple(str(row[column]) for column in projections[name])
            for row in snapshot[name]
        }
        == expected[name]
        for name in projections
        if name != "function_privileges"
    }
    evidence["function_privileges"] = all(
        str(row["grantee"]) == "PUBLIC"
        and str(row["privilege_type"]) == "EXECUTE"
        and str(row["extension_name"]) in PUBLIC_FUNCTION_EXTENSION_ALLOWLIST
        for row in snapshot["function_privileges"]
    )
    evidence["all_grants_equal_allowlist"] = all(evidence.values())
    return evidence


async def role_security_evidence(
    connection: asyncpg.Connection,
    *,
    username: str,
    database_name: str,
) -> dict[str, Any]:
    state = await _required_runtime_role_state(connection, username)
    _assert_runtime_role_safe(
        state, username=username, require_login_posture=True
    )
    owned_rows = await connection.fetch(
        """
        SELECT 'schemas' AS object_type, namespace.nspname AS schema_name,
               namespace.nspname AS object_name
        FROM pg_namespace AS namespace
        JOIN pg_roles AS owner ON owner.oid=namespace.nspowner
        WHERE owner.rolname=$1
        UNION ALL
        SELECT CASE WHEN relation.relkind='S' THEN 'sequences' ELSE 'tables' END,
               namespace.nspname, relation.relname
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        JOIN pg_roles AS owner ON owner.oid=relation.relowner
        WHERE owner.rolname=$1
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
        UNION ALL
        SELECT 'functions', namespace.nspname, routine.proname
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid=routine.pronamespace
        JOIN pg_roles AS owner ON owner.oid=routine.proowner
        WHERE owner.rolname=$1
        ORDER BY object_type, schema_name, object_name
        """,
        username,
    )
    owned_counts = {
        object_type: sum(
            1 for row in owned_rows if str(row["object_type"]) == object_type
        )
        for object_type in ("schemas", "tables", "sequences", "functions")
    }
    evidence = {
        "rolsuper": bool(state["rolsuper"]),
        "rolcreaterole": bool(state["rolcreaterole"]),
        "rolcreatedb": bool(state["rolcreatedb"]),
        "rolreplication": bool(state["rolreplication"]),
        "rolbypassrls": bool(state["rolbypassrls"]),
        "rolinherit": bool(state["rolinherit"]),
        "rolcanlogin": bool(state["rolcanlogin"]),
        "memberships": list(state["memberships"]),
        "database_connect": bool(
            await connection.fetchval(
                "SELECT has_database_privilege($1, $2, 'CONNECT')",
                username,
                database_name,
            )
        ),
        "database_create": bool(
            await connection.fetchval(
                "SELECT has_database_privilege($1, $2, 'CREATE')",
                username,
                database_name,
            )
        ),
        "database_temporary": bool(
            await connection.fetchval(
                "SELECT has_database_privilege($1, $2, 'TEMPORARY')",
                username,
                database_name,
            )
        ),
        "schema_usage": bool(
            await connection.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'USAGE')", username
            )
        ),
        "schema_create": bool(
            await connection.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'CREATE')", username
            )
        ),
        "owned_objects": owned_counts,
    }
    if (
        evidence["memberships"]
        or not evidence["database_connect"]
        or evidence["database_create"]
        or evidence["database_temporary"]
        or not evidence["schema_usage"]
        or evidence["schema_create"]
        or any(owned_counts.values())
    ):
        raise RuntimeError(f"runtime role {username} failed least-privilege evidence")
    return evidence


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
        for runtime_role in (reader, writer):
            state = await runtime_role_state(admin, runtime_role)
            if state is not None:
                _assert_runtime_role_safe(state, username=runtime_role)

        sources = tuple(load_source_configurations())
        governance = load_governance_configuration()
        defaults_before = await default_configuration_snapshot(admin)
        bootstrap = await bootstrap_runtime_configuration(
            admin, sources=sources, governance=governance
        )
        defaults_after = await default_configuration_snapshot(admin)
        defaults_evidence = default_integrity_evidence(
            before=defaults_before,
            after=defaults_after,
            expected_default_ids={
                "source_configurations": (
                    configuration.source_name for configuration in sources
                ),
                "retention_policies": (
                    policy.retention_class for policy in governance.policies
                ),
            },
        )
        await configure_reader_role(admin, task_environment)
        await configure_writer_role(admin, task_environment)
        snapshot = await grant_snapshot(admin, (reader, writer))
        grants_equal = grant_equality_evidence(
            snapshot,
            reader=reader,
            writer=writer,
            database_name=_required(task_environment, "DATABASE_NAME"),
        )
        if not grants_equal["all_grants_equal_allowlist"]:
            raise RuntimeError("runtime database grants differ from the allowlist")
        role_evidence = {
            "reader": await role_security_evidence(
                admin,
                username=reader,
                database_name=_required(task_environment, "DATABASE_NAME"),
            ),
            "writer": await role_security_evidence(
                admin,
                username=writer,
                database_name=_required(task_environment, "DATABASE_NAME"),
            ),
        }
        reader_verification = await verify_reader_role(task_environment)
        writer_verification = await verify_writer_role(task_environment)
        if not all(reader_verification.values()) or not all(writer_verification.values()):
            raise RuntimeError("database access prerequisite verification failed")
        return {
            "reader": reader,
            "writer": writer,
            "bootstrap": bootstrap,
            "default_integrity": defaults_evidence,
            "reader_verification": reader_verification,
            "writer_verification": writer_verification,
            "role_evidence": role_evidence,
            "grant_equality": grants_equal,
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
