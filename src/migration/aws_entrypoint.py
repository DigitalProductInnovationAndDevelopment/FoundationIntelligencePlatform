"""Fail-closed bootstrap for the approved one-off AWS migration task.

The source file is downloaded by a separate S3-only init container. This
process holds a PostgreSQL advisory lock, applies Alembic as the RDS master,
delegates loading, reconciliation and transactional activation to
``sqlite_to_postgres.migrate``, then creates/rotates a SELECT-only application
role.
Passwords are read only from ECS secret environment variables and are never
printed or included in subprocess arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping

import asyncpg

from bff.database import DatabaseManager, DatabaseSettings
from migration.release_gate import release_state
from migration.sqlite_to_postgres import migrate, source_preflight


MIGRATION_LOCK_ID = 2_083_370_803_870
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class AwsMigrationConfigurationError(RuntimeError):
    """Raised before any database mutation when task input is incomplete."""


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise AwsMigrationConfigurationError(f"{name} is required")
    return value


def _identifier(value: str, name: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise AwsMigrationConfigurationError(f"{name} is not a safe PostgreSQL identifier")
    return value


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quoted_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _connect_admin(environment: Mapping[str, str]) -> asyncpg.Connection:
    admin_environment = dict(environment)
    admin_environment["DATABASE_USER"] = _required(environment, "DATABASE_ADMIN_USER")
    admin_environment["DATABASE_PASSWORD"] = _required(
        environment, "DATABASE_ADMIN_PASSWORD"
    )
    settings = DatabaseSettings.from_env(admin_environment)
    url = settings.sqlalchemy_url()
    return await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        ssl=settings.ssl_mode,
        command_timeout=None,
    )


def _run_alembic(environment: Mapping[str, str]) -> None:
    admin_environment = dict(environment)
    admin_environment["DATABASE_USER"] = _required(environment, "DATABASE_ADMIN_USER")
    admin_environment["DATABASE_PASSWORD"] = _required(
        environment, "DATABASE_ADMIN_PASSWORD"
    )
    subprocess.run(
        ["alembic", "upgrade", "head"],
        env=admin_environment,
        check=True,
        timeout=900,
    )


async def _configure_application_role(
    connection: asyncpg.Connection,
    environment: Mapping[str, str],
) -> None:
    application_user = _identifier(
        _required(environment, "DATABASE_APP_USER"), "DATABASE_APP_USER"
    )
    admin_user = _identifier(
        _required(environment, "DATABASE_ADMIN_USER"), "DATABASE_ADMIN_USER"
    )
    database_name = _identifier(
        _required(environment, "DATABASE_NAME"), "DATABASE_NAME"
    )
    application_password = _required(environment, "DATABASE_APP_PASSWORD")
    role = _quoted_identifier(application_user)
    password = _quoted_literal(application_password)
    exists = await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)",
        application_user,
    )
    if exists:
        await connection.execute(
            f"ALTER ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOINHERIT NOREPLICATION PASSWORD {password}"
        )
    else:
        await connection.execute(
            f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOINHERIT NOREPLICATION PASSWORD {password}"
        )
    await connection.execute(f"REVOKE ALL ON DATABASE {_quoted_identifier(database_name)} FROM PUBLIC")
    await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    await connection.execute(
        f"GRANT CONNECT ON DATABASE {_quoted_identifier(database_name)} TO {role}"
    )
    await connection.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    await connection.execute(
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}"
    )
    await connection.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")
    await connection.execute(
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}"
    )
    await connection.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    await connection.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {role}")
    owner = _quoted_identifier(admin_user)
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {role}"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"GRANT SELECT ON TABLES TO {role}"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON SEQUENCES FROM {role}"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON FUNCTIONS FROM {role}"
    )
    await connection.execute(f"ALTER ROLE {role} SET default_transaction_read_only = on")


async def run(environment: Mapping[str, str] | None = None) -> dict[str, object]:
    task_environment = dict(os.environ if environment is None else environment)
    source = Path(_required(task_environment, "MIGRATION_SOURCE_PATH"))
    output_directory = Path(_required(task_environment, "MIGRATION_OUTPUT_DIRECTORY"))
    expected_checksum = _required(task_environment, "MIGRATION_EXPECTED_CHECKSUM")
    expected_schema_version = _required(
        task_environment, "MIGRATION_EXPECTED_SCHEMA_VERSION"
    )
    dataset_version = _required(task_environment, "MIGRATION_DATASET_VERSION")
    code_revision = _required(task_environment, "MIGRATION_CODE_REVISION")
    if not re.fullmatch(r"[a-f0-9]{40}", code_revision):
        raise AwsMigrationConfigurationError(
            "MIGRATION_CODE_REVISION must be a full lowercase Git SHA"
        )
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", dataset_version):
        raise AwsMigrationConfigurationError("MIGRATION_DATASET_VERSION is unsafe")
    # Validate the immutable source before Alembic or any other database write.
    # migrate() deliberately repeats the check immediately before loading.
    source_preflight(
        source,
        expected_checksum,
        expected_schema_version,
        remote_postgres=True,
    )
    admin = await _connect_admin(task_environment)
    try:
        acquired = await admin.fetchval("SELECT pg_try_advisory_lock($1)", MIGRATION_LOCK_ID)
        if not acquired:
            raise AwsMigrationConfigurationError(
                "another migration or refresh task holds the database lock"
            )
        await asyncio.to_thread(_run_alembic, task_environment)
        previous_user = os.environ.get("DATABASE_USER")
        previous_password = os.environ.get("DATABASE_PASSWORD")
        os.environ.update(
            {
                "DATABASE_USER": _required(task_environment, "DATABASE_ADMIN_USER"),
                "DATABASE_PASSWORD": _required(
                    task_environment, "DATABASE_ADMIN_PASSWORD"
                ),
            }
        )
        try:
            report = await migrate(
                source,
                expected_checksum,
                expected_schema_version,
                dataset_version,
                code_revision,
                "ecs-migration-task",
                "service",
                output_directory,
                remote_postgres=True,
            )
            await _configure_application_role(admin, task_environment)
            os.environ.update(
                {
                    "DATABASE_USER": _required(task_environment, "DATABASE_APP_USER"),
                    "DATABASE_PASSWORD": _required(
                        task_environment, "DATABASE_APP_PASSWORD"
                    ),
                }
            )
            gate_database = DatabaseManager(DatabaseSettings.from_env())
            try:
                gate = await release_state(gate_database)
            finally:
                await gate_database.close()
        finally:
            if previous_user is None:
                os.environ.pop("DATABASE_USER", None)
            else:
                os.environ["DATABASE_USER"] = previous_user
            if previous_password is None:
                os.environ.pop("DATABASE_PASSWORD", None)
            else:
                os.environ["DATABASE_PASSWORD"] = previous_password
        if not gate["ready"]:
            raise RuntimeError("release gate rejected the migrated dataset")
        result = {
            "dataset_version": report["dataset_version"],
            "activation_status": report["activation_status"],
            "release_gate_ready": True,
        }
        output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (output_directory / "task-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return result
    finally:
        await admin.close()


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
