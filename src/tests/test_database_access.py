from __future__ import annotations

import unittest

from migration.database_access import (
    READER_TABLES,
    WRITER_COLUMN_PRIVILEGES,
    WRITER_READ_TABLES,
    WRITER_SEQUENCES,
    WRITER_TABLE_PRIVILEGES,
    DatabaseAccessConfigurationError,
    configure_reader_role,
    configure_writer_role,
    default_integrity_evidence,
    identifier,
)


class _RecordingConnection:
    def __init__(
        self,
        *,
        role_exists: bool = False,
        column_grants: list[dict[str, object]] | None = None,
        memberships: list[dict[str, object]] | None = None,
        role_attributes: dict[str, object] | None = None,
    ):
        self.role_exists = role_exists
        self.column_grants = column_grants or []
        self.memberships = memberships or []
        self.role_attributes = role_attributes or {
            "rolname": "runtime",
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolreplication": False,
            "rolbypassrls": False,
            "rolinherit": False,
            "rolcanlogin": True,
        }
        self.statements: list[str] = []

    async def fetchval(self, statement: str, *_args):
        self.statements.append(statement)
        return self.role_exists

    async def fetchrow(self, statement: str, *_args):
        self.statements.append(statement)
        if not self.role_exists:
            return None
        return dict(self.role_attributes)

    async def execute(self, statement: str, *_args):
        self.statements.append(statement)
        if statement.startswith("CREATE ROLE"):
            self.role_exists = True
            self.role_attributes["rolinherit"] = False
            self.role_attributes["rolcanlogin"] = True
        if statement.startswith("ALTER ROLE") and "LOGIN NOINHERIT" in statement:
            self.role_attributes["rolinherit"] = False
            self.role_attributes["rolcanlogin"] = True
        return "INSERT 0 1"

    async def fetch(self, statement: str, *_args):
        self.statements.append(statement)
        if "information_schema.column_privileges" in statement:
            return self.column_grants
        if "pg_auth_members" in statement:
            return self.memberships
        return []


ENVIRONMENT = {
    "DATABASE_NAME": "foundation_intelligence",
    "DATABASE_ADMIN_USER": "foundation_admin",
    "DATABASE_READER_USER": "foundation_app",
    "DATABASE_READER_PASSWORD": "reader-secret",
    "DATABASE_WRITER_USER": "foundation_app_writer",
    "DATABASE_WRITER_PASSWORD": "writer-secret",
}


class TestDatabaseAccessContract(unittest.IsolatedAsyncioTestCase):
    async def test_reader_is_exact_select_allowlist_and_future_tables_fail_closed(self):
        connection = _RecordingConnection()
        await configure_reader_role(connection, ENVIRONMENT)  # type: ignore[arg-type]
        sql = "\n".join(connection.statements)
        self.assertIn("default_transaction_read_only = on", sql)
        self.assertIn("GRANT SELECT ON TABLE", sql)
        for table in READER_TABLES:
            self.assertIn(f'"{table}"', sql)
        self.assertNotIn("GRANT SELECT ON ALL TABLES", sql)
        self.assertNotIn("GRANT ALL", sql)
        self.assertNotIn("GRANT INSERT", sql)

    def test_release_gate_relations_are_reader_allowlisted(self):
        self.assertTrue(
            {
                "alembic_version",
                "data_quality_issues",
                "dataset_versions",
                "job_runs",
                "materialization_versions",
                "migration_runs",
            }.issubset(READER_TABLES)
        )

    async def test_writer_has_only_audited_table_and_column_dml(self):
        connection = _RecordingConnection()
        await configure_writer_role(connection, ENVIRONMENT)  # type: ignore[arg-type]
        sql = "\n".join(connection.statements)
        self.assertIn("default_transaction_read_only = off", sql)
        for table in WRITER_READ_TABLES:
            self.assertIn(f'"{table}"', sql)
        for table, privileges in WRITER_TABLE_PRIVILEGES.items():
            self.assertIn(
                f'GRANT {", ".join(privileges)} ON TABLE "{table}"',
                sql,
            )
        for table, grants in WRITER_COLUMN_PRIVILEGES.items():
            for privilege, columns in grants.items():
                rendered = ", ".join(f'"{column}"' for column in columns)
                self.assertIn(
                    f'GRANT {privilege} ({rendered}) ON TABLE "{table}"',
                    sql,
                )
        self.assertEqual(WRITER_SEQUENCES, ())
        self.assertNotIn("GRANT ALL", sql)
        self.assertNotIn("ON ALL TABLES", sql.replace("REVOKE ALL ON ALL TABLES", ""))
        self.assertNotIn('UPDATE ON TABLE "charities"', sql)
        self.assertNotIn('INSERT ON TABLE "retention_policies"', sql)

    async def test_existing_safe_writer_is_configured_without_superuser_attribute(self):
        connection = _RecordingConnection(
            role_exists=True,
            column_grants=[
                {
                    "table_name": "source_configurations",
                    "privilege_type": "UPDATE",
                    "column_names": ["credentials_reference", "enabled"],
                }
            ],
        )
        await configure_writer_role(connection, ENVIRONMENT)  # type: ignore[arg-type]
        sql = "\n".join(connection.statements)
        self.assertIn(
            'ALTER ROLE "foundation_app_writer" LOGIN NOINHERIT PASSWORD',
            sql,
        )
        self.assertNotIn("SUPERUSER", sql)
        self.assertIn(
            'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "foundation_app_writer"',
            sql,
        )
        self.assertIn(
            'REVOKE UPDATE ("credentials_reference", "enabled") ON TABLE '
            '"source_configurations" FROM "foundation_app_writer"',
            sql,
        )

    async def test_existing_role_with_dangerous_attribute_fails_closed(self):
        connection = _RecordingConnection(
            role_exists=True,
            role_attributes={
                "rolname": "foundation_app_writer",
                "rolsuper": False,
                "rolcreaterole": True,
                "rolcreatedb": False,
                "rolreplication": False,
                "rolbypassrls": False,
                "rolinherit": False,
                "rolcanlogin": True,
            },
        )
        with self.assertRaises(DatabaseAccessConfigurationError):
            await configure_writer_role(connection, ENVIRONMENT)  # type: ignore[arg-type]
        self.assertFalse(
            any(statement.startswith("ALTER ROLE") for statement in connection.statements)
        )

    async def test_existing_role_with_membership_fails_closed(self):
        connection = _RecordingConnection(
            role_exists=True,
            memberships=[{"parent_role": "legacy_writer"}],
        )
        with self.assertRaises(DatabaseAccessConfigurationError):
            await configure_writer_role(connection, ENVIRONMENT)  # type: ignore[arg-type]
        self.assertFalse(
            any(statement.startswith("ALTER ROLE") for statement in connection.statements)
        )

    def test_default_integrity_evidence_requires_preservation_and_only_defaults(self):
        evidence = default_integrity_evidence(
            before={
                "source_configurations": {"existing-source": "a" * 64},
                "retention_policies": {"existing-policy": "b" * 64},
            },
            after={
                "source_configurations": {
                    "existing-source": "a" * 64,
                    "default-source": "c" * 64,
                },
                "retention_policies": {
                    "existing-policy": "b" * 64,
                    "default-policy": "d" * 64,
                },
            },
            expected_default_ids={
                "source_configurations": ("default-source",),
                "retention_policies": ("default-policy",),
            },
        )
        self.assertTrue(
            evidence["source_configurations"]["existing_checksums_unchanged"]
        )
        self.assertTrue(
            evidence["retention_policies"]["only_missing_defaults_added"]
        )

    def test_unsafe_or_shared_principal_input_is_rejected(self):
        with self.assertRaises(DatabaseAccessConfigurationError):
            identifier("foundation_app;drop", "DATABASE_READER_USER")


if __name__ == "__main__":
    unittest.main()
