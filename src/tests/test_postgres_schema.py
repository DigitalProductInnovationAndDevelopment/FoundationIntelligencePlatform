import ast
import asyncio
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bff.database import DatabaseSettings
from bff.postgres.registry_repository import RegistrySearchRepository, SearchCursor


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPOSITORY_ROOT / "alembic" / "versions" / "0001_postgresql_foundation.py"
)
REQUIRED_TABLES = {
    "charities",
    "charity_registry_organizations",
    "grants",
    "grant_beneficiary_countries",
    "grant_beneficiary_terms",
    "grant_programme_categories",
    "grant_overview_facts",
    "grant_source_funder_facts",
    "organization_registry_links",
    "source_funder_link_overrides",
    "source_funder_profile_cache",
    "exchange_rates",
    "job_runs",
    "job_events",
    "audit_events",
    "dataset_versions",
    "source_ingestion_runs",
    "data_quality_issues",
    "materialization_versions",
    "retention_actions",
    "export_jobs",
}


class TestPostgreSQLSchemaContract(unittest.TestCase):
    def test_authoritative_migration_contains_required_schema_and_search(self):
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        for table_name in REQUIRED_TABLES:
            self.assertIn(f"CREATE TABLE {table_name}", source)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS pg_trgm", source)
        self.assertIn("TSVECTOR GENERATED ALWAYS", source)
        self.assertIn("USING GIN (search_vector)", source)
        self.assertIn("gin_trgm_ops", source)
        self.assertIn("FOREIGN KEY", source)
        self.assertIn("ON UPDATE CASCADE ON DELETE", source)

    def test_postgresql_runtime_foundation_does_not_import_sqlite(self):
        runtime_paths = [REPOSITORY_ROOT / "src" / "bff" / "database.py"]
        runtime_paths.extend(
            (REPOSITORY_ROOT / "src" / "bff" / "postgres").glob("*.py")
        )
        for path in runtime_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported.update(
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
            self.assertNotIn("sqlite3", imported, str(path))

    def test_production_application_import_does_not_load_sqlite(self):
        guard = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'sqlite3' or name.startswith('sqlite3.'):
        raise RuntimeError('production imported sqlite3')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import bff.main
assert bff.main.POSTGRESQL_ONLY_RUNTIME
"""
        environment = {
            **os.environ,
            "APP_ENV": "production",
            "AUTH_MODE": "disabled",
            "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
        }
        result = subprocess.run(
            [sys.executable, "-c", guard],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_cursor_round_trip_is_exact_and_bounded(self):
        cursor = SearchCursor(Decimal("0.12345678"), "registry:001")
        self.assertEqual(SearchCursor.decode(cursor.encode()), cursor)
        with self.assertRaises(ValueError):
            SearchCursor.decode("not-json")
        with self.assertRaises(ValueError):
            SearchCursor.decode("x" * 2049)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestPostgreSQLSchemaIntegration(unittest.TestCase):
    def test_real_postgresql_foreign_keys_and_search(self):
        asyncio.run(self._exercise_schema())

    async def _exercise_schema(self):
        configured_url = os.getenv("TEST_DATABASE_URL") or DatabaseSettings.from_env().sqlalchemy_url()
        engine = create_async_engine(configured_url, pool_pre_ping=True)
        dataset_version = f"schema-test-{uuid.uuid4()}"
        prior_active = None
        try:
            async with engine.connect() as connection:
                table_rows = await connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_catalog.pg_tables
                        WHERE schemaname = current_schema()
                        """
                    )
                )
                self.assertTrue(REQUIRED_TABLES.issubset(set(table_rows.scalars())))
                extension = await connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')")
                )
                self.assertTrue(extension)
                prior_active = await connection.scalar(
                    text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                )

            async with engine.begin() as connection:
                if prior_active:
                    await connection.execute(
                        text(
                            "UPDATE dataset_versions "
                            "SET is_active=FALSE, status='rolled_back' "
                            "WHERE dataset_version=:version"
                        ),
                        {"version": prior_active},
                    )
                await connection.execute(
                    text(
                        """
                        INSERT INTO dataset_versions (
                            dataset_version, status, is_active, activated_at
                        ) VALUES (:version, 'active', TRUE, CURRENT_TIMESTAMP)
                        """
                    ),
                    {"version": dataset_version},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO charity_registry_organizations (
                            dataset_version, registry_id, charity_number,
                            registered_name, normalized_name, source_name, imported_at
                        ) VALUES
                            (:version, 'registry:001', '1', 'Alpha Aid',
                             'alpha aid', 'fixture', CURRENT_TIMESTAMP),
                            (:version, 'registry:002', '2', 'Alpha Aid',
                             'alpha aid', 'fixture', CURRENT_TIMESTAMP)
                        """
                    ),
                    {"version": dataset_version},
                )

            sessions = async_sessionmaker(engine, expire_on_commit=False)
            page = await RegistrySearchRepository(sessions).search("Alpha", limit=1)
            self.assertEqual(page["items"][0]["registry_id"], "registry:001")
            self.assertIsNotNone(page["next_cursor"])
            second_page = await RegistrySearchRepository(sessions).search(
                "Alpha", cursor=page["next_cursor"], limit=1
            )
            self.assertEqual(second_page["items"][0]["registry_id"], "registry:002")

            with self.assertRaises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO grants (
                                dataset_version, grant_id, funding_charity_id,
                                recipient_name
                            ) VALUES (:version, 'orphan', 999999, 'Recipient')
                            """
                        ),
                        {"version": dataset_version},
                    )
        finally:
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text("DELETE FROM dataset_versions WHERE dataset_version=:version"),
                        {"version": dataset_version},
                    )
                    if prior_active:
                        await connection.execute(
                            text(
                                "UPDATE dataset_versions "
                                "SET is_active=TRUE, status='active' "
                                "WHERE dataset_version=:version"
                            ),
                            {"version": prior_active},
                        )
            finally:
                await engine.dispose()


if __name__ == "__main__":
    unittest.main()
