import unittest

from migration.release_gate import evaluate_release_state


class TestReleaseGate(unittest.TestCase):
    def test_approved_active_dataset_passes(self):
        result = evaluate_release_state(
            {
                "schema_version": "0007_worker_execution",
                "active_dataset_count": 1,
                "dataset_version": "dataset-approved",
                "migration_status": "active",
                "reconciliation_results": {
                    "grant_count": {"status": "pass"},
                    "registry_count": {"status": "pass"},
                },
                "materialization_active": True,
                "active_quality_blocks": 0,
                "dead_letter_count": 0,
            }
        )
        self.assertTrue(result["ready"])
        self.assertTrue(all(result["checks"].values()))

    def test_any_mismatch_fails_closed_without_sensitive_output(self):
        result = evaluate_release_state(
            {
                "schema_version": "unexpected",
                "active_dataset_count": 2,
                "dataset_version": "dataset-rejected",
                "migration_status": "failed",
                "reconciliation_results": {"grant_count": {"status": "fail"}},
                "materialization_active": False,
                "active_quality_blocks": 1,
                "dead_letter_count": 1,
            }
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["reconciliation_failures"], ["grant_count"])
        self.assertNotIn("password", result)
        self.assertNotIn("connection", result)


if __name__ == "__main__":
    unittest.main()
