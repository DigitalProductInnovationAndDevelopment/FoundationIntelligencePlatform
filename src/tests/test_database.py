import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from bff.database import DatabaseConfigurationError, DatabaseSettings
from bff.main import app


class _HealthyDatabase:
    async def readiness(self, *, expected_schema_version):
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
    async def readiness(self, *, expected_schema_version):
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

    def test_readiness_reflects_postgresql_state(self):
        original = app.state.database
        client = TestClient(app)
        try:
            app.state.database = _HealthyDatabase()
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["checks"]["postgresql"], "healthy")
            self.assertEqual(ready.json()["checks"]["schema_version"], "healthy")

            app.state.database = _UnavailableDatabase()
            unavailable = client.get("/health/ready")
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()["checks"]["postgresql"], "unavailable")
        finally:
            app.state.database = original


if __name__ == "__main__":
    unittest.main()
