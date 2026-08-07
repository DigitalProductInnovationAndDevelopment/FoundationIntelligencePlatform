from pathlib import Path
import unittest

from scripts.validate_terraform_static import ROOT, validate


class TestTerraformContract(unittest.TestCase):
    def test_static_structure_and_security_contract(self):
        result = validate()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["environments"], ["dev", "staging"])
        self.assertGreaterEqual(result["resource_types"], 30)
        self.assertGreaterEqual(result["resource_blocks"], 70)
        self.assertEqual(result["provider_dependent_validation"], "not_tested")
        self.assertFalse(result["aws_actions_performed"])

    def test_environment_examples_contain_no_secret_values(self):
        for environment in ("dev", "staging"):
            path = (
                ROOT
                / "infra"
                / "terraform"
                / "environments"
                / environment
                / "terraform.tfvars.example"
            )
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("aws_access_key_id", text.casefold())
            self.assertNotIn("aws_secret_access_key", text.casefold())
            self.assertNotIn("password", text.casefold())
            self.assertIn("@sha256:", text)
            self.assertIn("manage_dns     = false", text)


if __name__ == "__main__":
    unittest.main()
