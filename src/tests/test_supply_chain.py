import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_licenses import ROOT, scan
from scripts.generate_sbom import document, npm_components, python_components, write_document


class TestSupplyChainContracts(unittest.TestCase):
    def test_lockfiles_generate_deterministic_cyclonedx_components(self):
        backend = document("backend", python_components())
        frontend = document("frontend", npm_components())
        self.assertEqual(backend["bomFormat"], "CycloneDX")
        self.assertEqual(backend["specVersion"], "1.5")
        self.assertGreater(len(backend["components"]), 50)
        self.assertGreater(len(frontend["components"]), 50)
        self.assertTrue(all(component.get("version") for component in backend["components"]))
        self.assertTrue(all(component.get("purl") for component in frontend["components"]))

        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.json"
            second = Path(temporary) / "second.json"
            write_document(first, backend)
            write_document(second, backend)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            json.loads(first.read_text(encoding="utf-8"))

    def test_installed_dependencies_have_no_forbidden_declared_license(self):
        result = scan(ROOT / "frontend" / "node_modules")
        self.assertEqual(result["status"], "passed", result["forbidden"])
        self.assertGreater(result["components_scanned"], 100)


if __name__ == "__main__":
    unittest.main()
