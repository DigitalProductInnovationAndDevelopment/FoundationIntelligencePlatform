import unittest

from migration.aws_entrypoint import (
    AwsMigrationConfigurationError,
    _configure_application_role,
    _identifier,
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


class TestAwsMigrationRoleSafety(unittest.IsolatedAsyncioTestCase):
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
