import json
import tempfile
import unittest
from pathlib import Path

from pipelines.curate_europe_tech_grants import (
    beneficiary_country_codes,
    curate_file,
    resolve_country_code,
)


def grant(identifier, country_code, description="Digital skills and software access"):
    return {
        "grant_id": identifier,
        "data": {
            "id": identifier,
            "awardDate": "2025-01-15",
            "description": description,
            "beneficiaryLocation": [{"name": country_code, "countryCode": country_code}],
        },
    }


class TestEuropeTechCuration(unittest.TestCase):
    def test_country_resolution_includes_eu_eea_switzerland_and_not_uk(self):
        self.assertEqual(resolve_country_code("Germany"), "DE")
        self.assertEqual(resolve_country_code("Österreich"), "AT")
        self.assertEqual(resolve_country_code("Norway"), "NO")
        self.assertEqual(resolve_country_code("Switzerland"), "CH")
        self.assertEqual(resolve_country_code("United Kingdom"), "GB")

    def test_beneficiary_selection_never_uses_recipient_or_funder_location(self):
        record = grant("no-beneficiary", "DE")
        record["data"].pop("beneficiaryLocation")
        record["data"]["recipientOrganization"] = [{"addressCountry": "Germany"}]
        record["data"]["fundingOrganization"] = [{"addressCountry": "Germany"}]
        self.assertEqual(beneficiary_country_codes(record), [])

    def test_curation_uses_best_effort_dach_quota_and_reports_shortfall(self):
        payload = [
            {"org_id": "Publisher", "grants_made": [
                grant("DE-1", "DE"),
                grant("CH-1", "CH"),
                grant("FR-1", "FR"),
                grant("GB-1", "GB"),
                grant("DE-NON-TECH", "DE", "Community arts programme"),
            ]},
            grant("DE-1", "DE"),  # duplicate must not be counted twice
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "selected.jsonl"
            report_path = root / "report.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")

            report = curate_file(input_path, output_path, report_path, target=5, dach_share=0.60)

            selected = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["grant_id"] for row in selected], ["CH-1", "DE-1", "FR-1"])
            self.assertEqual(report["selection_counts"]["selected_dach_grants"], 2)
            self.assertFalse(report["target_met"])
            self.assertEqual(report["shortfall"], 2)
            self.assertEqual(report["selected_beneficiary_country_associations"], {"CH": 1, "DE": 1, "FR": 1})

    def test_publisher_status_rows_are_not_treated_as_grants(self):
        payload = {"records": [{
            "org_id": "publisher", "summary": {"org_id": "publisher"}, "error": "timed out"
        }, {"org_id": "funder", "summary": {"org_id": "funder"}, "grants_made": [
            grant("DE-1", "DE")
        ]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            report = curate_file(input_path, root / "selected.jsonl", root / "report.json", target=1, dach_share=1)
        self.assertEqual(report["screening_counts"]["grant_records_read"], 1)
        self.assertNotIn("missing_grant_id", report["screening_counts"])


if __name__ == "__main__":
    unittest.main()
