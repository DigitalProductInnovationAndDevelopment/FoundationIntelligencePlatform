import unittest
from unittest.mock import AsyncMock, patch

from migration.aws_entrypoint import (
    AwsMigrationConfigurationError,
    _connect_admin,
    _configure_application_role,
    _identifier,
    _verify_application_role,
)


class _RecordingConnection:
    def __init__(self, role_exists: bool = False):
        self.role_exists = role_exists
        self.statements: list[str] = []

    async def fetchval(self, statement: str, *_args):
        self.statements.append(statement)
        return self.role_exists

    async def execute(self, statement: str):
        self.statements.append(statement)


class _VerifiedApplicationConnection:
    async def fetchval(self, statement: str):
        if statement == "SELECT 1":
            return 1
        if statement.startswith("SELECT ssl FROM pg_stat_ssl"):
            return True
        if statement == "SHOW default_transaction_read_only":
            return "on"
        raise AssertionError(f"Unexpected query: {statement}")

    async def execute(self, statement: str):
        if statement.startswith("UPDATE dataset_versions"):
            import asyncpg

            raise asyncpg.ReadOnlySQLTransactionError("read-only transaction")
        raise AssertionError(f"Unexpected statement: {statement}")

    async def close(self):
        return None


class TestAwsMigrationRoleSafety(unittest.IsolatedAsyncioTestCase):
    async def test_admin_connection_requires_tls_for_rds(self):
        connection = AsyncMock()
        with patch(
            "migration.aws_entrypoint.asyncpg.connect",
            new=AsyncMock(return_value=connection),
        ) as connect:
            result = await _connect_admin(
                {
                    "DATABASE_HOST": "private-postgresql.internal",
                    "DATABASE_NAME": "foundation_intelligence",
                    "DATABASE_ADMIN_USER": "foundation_admin",
                    "DATABASE_ADMIN_PASSWORD": "runtime-only-secret",
                    "DATABASE_SSL_MODE": "require",
                }
            )
        self.assertIs(result, connection)
        self.assertEqual(connect.await_args.kwargs["ssl"], "require")

    async def test_application_role_verification_requires_tls_and_denied_update(self):
        connection = _VerifiedApplicationConnection()
        with patch(
            "migration.aws_entrypoint.asyncpg.connect",
            new=AsyncMock(return_value=connection),
        ) as connect:
            result = await _verify_application_role(
                {
                    "DATABASE_HOST": "private-postgresql.internal",
                    "DATABASE_NAME": "foundation_intelligence",
                    "DATABASE_APP_USER": "foundation_app",
                    "DATABASE_APP_PASSWORD": "runtime-only-secret",
                    "DATABASE_SSL_MODE": "require",
                }
            )
        self.assertEqual(connect.await_args.kwargs["ssl"], "require")
        self.assertEqual(
            result,
            {
                "select_succeeded": True,
                "tls_in_use": True,
                "default_read_only": True,
                "update_denied": True,
            },
        )

    async def test_application_role_is_select_only_and_default_read_only(self):
        connection = _RecordingConnection()
        await _configure_application_role(
            connection,  # type: ignore[arg-type]
            {
                "DATABASE_APP_USER": "foundation_app",
                "DATABASE_APP_PASSWORD": "generatedsecret",
                "DATABASE_ADMIN_USER": "foundation_admin",
                "DATABASE_NAME": "foundation_intelligence",
            },
        )
        sql = "\n".join(connection.statements)
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE", sql)
        self.assertIn("GRANT SELECT ON ALL TABLES", sql)
        self.assertIn("REVOKE ALL ON ALL SEQUENCES", sql)
        self.assertIn("REVOKE EXECUTE ON ALL FUNCTIONS", sql)
        self.assertIn("default_transaction_read_only = on", sql)
        self.assertNotIn("GRANT SELECT, INSERT", sql)

    async def test_existing_role_is_demoted_before_reuse(self):
        connection = _RecordingConnection(role_exists=True)
        await _configure_application_role(
            connection,  # type: ignore[arg-type]
            {
                "DATABASE_APP_USER": "foundation_app",
                "DATABASE_APP_PASSWORD": "generatedsecret",
                "DATABASE_ADMIN_USER": "foundation_admin",
                "DATABASE_NAME": "foundation_intelligence",
            },
        )
        alter_role = next(
            statement
            for statement in connection.statements
            if statement.startswith("ALTER ROLE \"foundation_app\" LOGIN")
        )
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE", alter_role)

    def test_unsafe_identifiers_fail_before_sql(self):
        with self.assertRaises(AwsMigrationConfigurationError):
            _identifier("foundation_app; DROP SCHEMA public", "DATABASE_APP_USER")


if __name__ == "__main__":
    unittest.main()
