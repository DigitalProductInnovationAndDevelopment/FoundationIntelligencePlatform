import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard.charity_commission import (
    build_charity_commission_index,
    filter_charity_commission_records,
    find_charity_commission_match,
    load_charity_commission_cache,
    normalize_charity_commission_record,
    normalize_charity_commission_records,
    summarize_charity_commission_records,
)


class TestCharityCommissionDashboardData(unittest.TestCase):
    def setUp(self):
        self.raw_record = {
            "registered_charity_number": 123456,
            "suffix": 0,
            "link": "https://example.test/register/123456",
            "all_details": {
                "organisation_number": 987654,
                "reg_charity_number": 123456,
                "charity_name": "Example Foundation",
                "charity_type": "Trust",
                "reg_status": "R",
                "date_of_registration": "2020-01-01",
                "latest_acc_fin_year_end_date": "2025-03-31",
                "latest_income": 1_200_000,
                "latest_expenditure": 850_000,
                "address_line_one": "1 Example Street",
                "address_line_five": "London",
                "address_post_code": "SW1A 1AA",
                "email": "hello@example.test",
                "phone": "020 0000 0000",
                "web": "https://example.test",
                "CharityAoOCountryContinent": [
                    {"country": "Kenya", "continent": "Africa"},
                ],
                "CharityAoORegion": [{"region": "London"}],
                "who_what_where": [
                    {"classification_type": "Who", "classification_desc": "Children"},
                    {"classification_type": "What", "classification_desc": "Education"},
                    {"classification_type": "How", "classification_desc": "Makes grants"},
                ],
            },
            "assets_liabilities": [
                {
                    "fin_period_end_date": "2025-03-31",
                    "assets_own_use": 100_000,
                    "assets_long_term_investment": 300_000,
                    "assets_other_assets": 50_000,
                    "assets_total_liabilities": 25_000,
                }
            ],
            "primary_grants": {"primary_purpose_grant_making": True},
            "financial_history": [
                {
                    "financial_period_end_date": "2025-03-31",
                    "income": 1_200_000,
                    "expenditure": 850_000,
                },
                {
                    "financial_period_end_date": "2024-03-31",
                    "income": 1_000_000,
                    "expenditure": 800_000,
                },
            ],
        }

    def test_missing_file_returns_friendly_state(self):
        with tempfile.TemporaryDirectory() as directory:
            result = load_charity_commission_cache(Path(directory) / "missing.json")
        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["records"], [])
        self.assertIn("not found", result["message"])

    def test_malformed_json_returns_warning_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            path.write_text("{not valid json", encoding="utf-8")
            result = load_charity_commission_cache(path)
        self.assertEqual(result["state"], "malformed")
        self.assertEqual(result["records"], [])

    def test_wrapped_payload_is_supported_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text(json.dumps({"results": [self.raw_record]}), encoding="utf-8")
            with patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("UI loader must not call the network"),
            ):
                result = load_charity_commission_cache(path)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(len(result["records"]), 1)

    def test_nested_record_is_flattened_for_display(self):
        record = normalize_charity_commission_record(self.raw_record)
        self.assertEqual(record["charity_name"], "Example Foundation")
        self.assertEqual(record["registered_charity_number"], "123456")
        self.assertEqual(record["organisation_number"], "987654")
        self.assertEqual(record["registration_status"], "Active")
        self.assertEqual(record["countries"], ["Kenya"])
        self.assertEqual(record["regions"], ["London"])
        self.assertEqual(record["who_classifications"], ["Children"])
        self.assertEqual(record["what_classifications"], ["Education"])
        self.assertEqual(record["how_classifications"], ["Makes grants"])
        self.assertEqual(record["latest_income"], 1_200_000.0)
        self.assertEqual(record["latest_expenditure"], 850_000.0)
        self.assertEqual(record["assets"], 450_000.0)
        self.assertEqual(record["liabilities"], 25_000.0)
        self.assertTrue(record["primary_purpose_grant_making"])
        self.assertEqual(len(record["financial_history"]), 2)

    def test_missing_nested_fields_do_not_crash(self):
        record = normalize_charity_commission_record({"all_details": {"charity_name": "Sparse"}})
        self.assertEqual(record["charity_name"], "Sparse")
        self.assertEqual(record["registration_status"], "Unknown")
        self.assertFalse(record["has_financial_history"])
        self.assertIsNone(record["latest_income"])
        self.assertIsNone(record["primary_purpose_grant_making"])

    def test_summary_and_filters_use_availability_not_truthiness_of_flag(self):
        first = normalize_charity_commission_record(self.raw_record)
        second_raw = {
            "registered_charity_number": 222222,
            "all_details": {
                "charity_name": "Removed Charity",
                "reg_status": "RM",
            },
            "primary_grants": {"primary_purpose_grant_making": False},
        }
        second = normalize_charity_commission_record(second_raw)
        records = [first, second]
        summary = summarize_charity_commission_records(records)
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(summary["with_grant_maker_flag"], 2)
        filtered = filter_charity_commission_records(
            records,
            status="Active",
            has_latest_income="Yes",
            search="Kenya",
        )
        self.assertEqual([record["charity_name"] for record in filtered], ["Example Foundation"])

    def test_identifier_index_enriches_existing_organization(self):
        records = normalize_charity_commission_records([self.raw_record])
        index = build_charity_commission_index(records)
        existing = {"funding_info": {"charity_number": "123456"}}
        match = find_charity_commission_match(existing, index)
        self.assertIsNotNone(match)
        self.assertEqual(match["charity_name"], "Example Foundation")


if __name__ == "__main__":
    unittest.main()
