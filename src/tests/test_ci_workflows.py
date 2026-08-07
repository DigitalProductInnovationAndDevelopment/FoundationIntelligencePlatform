import unittest

from scripts.validate_ci_workflows import validate


class TestCiWorkflowContract(unittest.TestCase):
    def test_pr_and_deployment_workflows_are_complete_and_guarded(self):
        result = validate()
        self.assertEqual(result["status"], "passed")
        self.assertGreaterEqual(result["ci_gate_markers"], 20)
        self.assertEqual(
            result["staging_requires_protected_environments"],
            ["staging-publish", "staging"],
        )
        self.assertFalse(result["production_enabled"])
        self.assertEqual(result["workflow_execution"], "not_tested")


if __name__ == "__main__":
    unittest.main()
