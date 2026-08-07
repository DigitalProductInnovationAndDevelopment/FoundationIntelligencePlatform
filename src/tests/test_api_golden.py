import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from bff.main import app


CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "config/golden/api-contract.json").read_text(
        encoding="utf-8"
    )
)


class _GoldenDatabase:
    @staticmethod
    def pool_status():
        return {"checked_out": 0.0, "capacity": 1.0, "utilization_ratio": 0.0}

    async def readiness(
        self, *, expected_schema_version, require_critical_configuration=True
    ):
        return {
            "ready": True,
            "checks": CONTRACT["health"]["ready"]["checks"],
            "metadata": {"queue_age_seconds": 0, "dead_letter_count": 0},
        }


class TestApiGoldenContract(unittest.TestCase):
    def test_health_payloads_and_required_openapi_paths(self):
        original = app.state.database
        app.state.database = _GoldenDatabase()
        try:
            client = TestClient(app)
            self.assertEqual(client.get("/health").json(), CONTRACT["health"]["legacy"])
            self.assertEqual(client.get("/health/live").json(), CONTRACT["health"]["live"])
            self.assertEqual(client.get("/health/ready").json(), CONTRACT["health"]["ready"])
            paths = client.get("/openapi.json").json()["paths"]
            for required in CONTRACT["required_paths"]:
                self.assertIn(required, paths)
        finally:
            app.state.database = original


if __name__ == "__main__":
    unittest.main()
