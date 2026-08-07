import os
import tempfile
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from bff.database import DatabaseConfigurationError, DatabaseSettings
from bff.main import app


class _HealthyDatabase:
    def __init__(self):
        self.require_critical_configuration = None

    async def readiness(
        self, *, expected_schema_version, require_critical_configuration=True
    ):
        self.require_critical_configuration = require_critical_configuration
        return {
            "ready": True,
            "checks": {
                "postgresql": "healthy",
                "schema_version": "healthy",
                "active_dataset": "healthy",
                "critical_configuration": "healthy",
                "queue": "healthy",
            },
            "metadata": {
                "queue_age_seconds": 0,
                "dead_letter_count": 0,
            },
        }

    @staticmethod
    def pool_status():
        return {"checked_out": 0.0, "capacity": 1.0, "utilization_ratio": 0.0}


class _UnavailableDatabase:
    async def readiness(
        self, *, expected_schema_version, require_critical_configuration=True
    ):
        return {
            "ready": False,
            "checks": {
                "postgresql": "unavailable",
                "schema_version": "unknown",
                "active_dataset": "unknown",
                "critical_configuration": "unknown",
                "queue": "unknown",
            },
        }

    @staticmethod
    def pool_status():
        return {"checked_out": 0.0, "capacity": 1.0, "utilization_ratio": 0.0}


class TestDatabaseFoundation(unittest.TestCase):
    def test_secret_file_builds_encoded_async_postgresql_url(self):
        descriptor, password_path = tempfile.mkstemp()
        try:
            os.write(descriptor, b"local:p@ssword\n")
            os.close(descriptor)
            settings = DatabaseSettings.from_env(
                {
                    "DATABASE_HOST": "postgres",
                    "DATABASE_PORT": "5432",
                    "DATABASE_NAME": "foundation_intelligence",
                    "DATABASE_USER": "foundation_app",
                    "DATABASE_PASSWORD_FILE": password_path,
                }
            )
            url = settings.sqlalchemy_url()
            self.assertEqual(url.drivername, "postgresql+asyncpg")
            self.assertEqual(url.host, "postgres")
            self.assertEqual(url.password, "local:p@ssword")
            self.assertNotIn("local:p@ssword", url.render_as_string(hide_password=True))
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            os.unlink(password_path)

    def test_non_postgresql_url_is_rejected(self):
        settings = DatabaseSettings.from_env({"DATABASE_URL": "sqlite+aiosqlite:///tmp/test.db"})
        with self.assertRaises(DatabaseConfigurationError):
            settings.sqlalchemy_url()

    def test_runtime_secret_environment_builds_postgresql_url(self):
        settings = DatabaseSettings.from_env(
            {
                "DATABASE_HOST": "private-postgresql.internal",
                "DATABASE_NAME": "foundation_intelligence",
                "DATABASE_USER": "foundation_app",
                "DATABASE_PASSWORD": "runtime-only-secret",
            }
        )
        url = settings.sqlalchemy_url()
        self.assertTrue(settings.configured)
        self.assertEqual(url.password, "runtime-only-secret")
        self.assertNotIn("runtime-only-secret", url.render_as_string(hide_password=True))

    def test_tls_required_is_embedded_for_every_sqlalchemy_connection(self):
        settings = DatabaseSettings.from_env(
            {
                "DATABASE_HOST": "private-postgresql.internal",
                "DATABASE_NAME": "foundation_intelligence",
                "DATABASE_USER": "foundation_app",
                "DATABASE_PASSWORD": "runtime-only-secret",
                "DATABASE_SSL_MODE": "require",
            }
        )
        url = settings.sqlalchemy_url()
        self.assertEqual(url.query["ssl"], "require")
        self.assertNotIn("sslmode", url.query)

    def test_raw_database_url_round_trips_combined_reserved_password(self):
        password = "A@b:%/+#?&xyz"
        settings = DatabaseSettings.from_env(
            {
                "DATABASE_HOST": "private-postgresql.internal",
                "DATABASE_NAME": "foundation_intelligence",
                "DATABASE_USER": "foundation_admin",
                "DATABASE_PASSWORD": password,
                "DATABASE_SSL_MODE": "require",
            }
        )
        raw_url = settings.raw_sqlalchemy_url()
        self.assertNotIn("%%", raw_url)
        self.assertEqual(make_url(raw_url).password, password)

    def test_raw_database_url_round_trips_each_reserved_password_character(self):
        for character in ("%", "@", ":", "/", "+", "#", "?", "&"):
            with self.subTest(character=character):
                password = f"prefix{character}suffix"
                settings = DatabaseSettings.from_env(
                    {
                        "DATABASE_HOST": "private-postgresql.internal",
                        "DATABASE_NAME": "foundation_intelligence",
                        "DATABASE_USER": "foundation_admin",
                        "DATABASE_PASSWORD": password,
                        "DATABASE_SSL_MODE": "require",
                    }
                )
                raw_url = settings.raw_sqlalchemy_url()
                self.assertNotIn("%%", raw_url)
                self.assertEqual(make_url(raw_url).password, password)

    def test_database_url_sslmode_is_normalized_without_losing_query_parameters(self):
        settings = DatabaseSettings.from_env(
            {
                "DATABASE_URL": (
                    "postgresql+asyncpg://foundation_app:secret@postgres/database"
                    "?prepared_statement_cache_size=0&sslmode=require&sslmode=require"
                )
            }
        )
        url = settings.sqlalchemy_url()
        self.assertEqual(url.query["ssl"], "require")
        self.assertEqual(url.query["prepared_statement_cache_size"], "0")
        self.assertNotIn("sslmode", url.query)
        raw_url = settings.raw_sqlalchemy_url()
        self.assertEqual(raw_url.count("ssl=require"), 1)
        self.assertNotIn("sslmode=", raw_url)

    def test_database_url_asyncpg_ssl_remains_single_and_required(self):
        settings = DatabaseSettings.from_env(
            {
                "DATABASE_URL": (
                    "postgresql+asyncpg://foundation_app:secret@postgres/database"
                    "?prepared_statement_cache_size=0&ssl=require"
                )
            }
        )
        raw_url = settings.raw_sqlalchemy_url()
        parsed = make_url(raw_url)
        self.assertEqual(parsed.query["ssl"], "require")
        self.assertEqual(parsed.query["prepared_statement_cache_size"], "0")
        self.assertEqual(raw_url.count("ssl=require"), 1)

    def test_conflicting_database_ssl_configuration_is_rejected(self):
        with self.assertRaises(DatabaseConfigurationError):
            DatabaseSettings.from_env(
                {
                    "DATABASE_URL": (
                        "postgresql+asyncpg://foundation_app:secret@postgres/database"
                        "?sslmode=disable"
                    ),
                    "DATABASE_SSL_MODE": "require",
                }
            )

    def test_conflicting_database_url_ssl_keys_are_rejected(self):
        with self.assertRaises(DatabaseConfigurationError):
            DatabaseSettings.from_env(
                {
                    "DATABASE_URL": (
                        "postgresql+asyncpg://foundation_app:secret@postgres/database"
                        "?ssl=require&sslmode=disable"
                    )
                }
            )

    def test_local_postgresql_keeps_tls_disabled_by_default(self):
        settings = DatabaseSettings.from_env(
            {
                "DATABASE_URL": (
                    "postgresql+asyncpg://foundation_app:secret@postgres/database"
                    "?prepared_statement_cache_size=0"
                )
            }
        )
        url = settings.sqlalchemy_url()
        self.assertEqual(settings.ssl_mode, "disable")
        self.assertNotIn("ssl", url.query)
        self.assertEqual(url.query["prepared_statement_cache_size"], "0")

    def test_readiness_reflects_postgresql_state(self):
        original = app.state.database
        client = TestClient(app)
        try:
            healthy_database = _HealthyDatabase()
            app.state.database = healthy_database
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["checks"]["postgresql"], "healthy")
            self.assertEqual(ready.json()["checks"]["schema_version"], "healthy")
            self.assertTrue(healthy_database.require_critical_configuration)

            app.state.database = _UnavailableDatabase()
            unavailable = client.get("/health/ready")
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()["checks"]["postgresql"], "unavailable")
        finally:
            app.state.database = original

    def test_public_readonly_readiness_does_not_require_pipeline_configuration(self):
        original_database = app.state.database
        original_security = app.state.security_settings
        database = _HealthyDatabase()
        try:
            app.state.database = database
            app.state.security_settings = replace(
                original_security,
                app_env="demo",
                data_runtime_mode="postgresql",
                auth_mode="public_readonly",
                dev_auth_enabled=False,
                core_proxy_enabled=False,
            )
            response = TestClient(app).get("/health/ready")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(database.require_critical_configuration)
        finally:
            app.state.database = original_database
            app.state.security_settings = original_security


if __name__ == "__main__":
    unittest.main()
