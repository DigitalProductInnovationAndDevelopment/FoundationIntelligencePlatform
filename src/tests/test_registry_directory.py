import json
import os
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient
from bff.main import app
from bff.repositories import get_charity_repository
from bff.repositories import SQLiteCharityRepository
from data.db_loader import create_tables
from data.registry import (
    REGISTRY_FTS_TABLE,
    REGISTRY_TABLE,
    import_charity_commission_registry,
)
from preprocessing.enrichment import normalize_geography_sources


def registry_record(
    organization_number,
    charity_number,
    name,
    status="Registered",
    income=None,
    expenditure=None,
    city="London",
    region="Greater London",
):
    return {
        "date_of_extract": "2026-07-09T00:00:00",
        "organisation_number": organization_number,
        "registered_charity_number": charity_number,
        "linked_charity_number": 0,
        "charity_name": name,
        "charity_registration_status": status,
        "date_of_registration": "2010-01-01T00:00:00",
        "latest_acc_fin_period_end_date": "2025-12-31T00:00:00",
        "latest_income": income,
        "latest_expenditure": expenditure,
        "charity_contact_address1": "1 Register Street",
        "charity_contact_address4": city,
        "charity_contact_address5": region,
        "charity_contact_postcode": "SW1A 1AA",
        "charity_activities": "Registered charitable activity.",
    }


class TestRegistryDirectory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "charities.db")
        self.source_path = os.path.join(self.temp_dir.name, "publicextract.charity.json")
        self.conn = sqlite3.connect(self.db_path)
        create_tables(self.conn)
        self.conn.execute(
            """
            INSERT INTO charities (charity_id, name, type, raw_cc_data, primary_source, source_names)
            VALUES (1001, 'Alpha Foundation', 'Charity', '{}', 'Charity Commission for England and Wales', '["Charity Commission for England and Wales"]')
            """
        )
        normalized, _ = normalize_geography_sources(
            [{"name": "Ghana", "countryCode": "GH"}], "beneficiary_geography"
        )
        self.conn.execute(
            """
            INSERT INTO grants (
                grant_id, funding_charity_id, funding_name, recipient_name, amount, currency,
                description, date, beneficiary_geography, beneficiary_geography_normalized,
                source, source_record_id, source_url, ingestion_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grant-alpha", 1001, "Alpha Foundation", "Recipient", 100.0, "GBP", "Grant",
                "2025-01-01", json.dumps([{"name": "Ghana", "countryCode": "GH"}]),
                json.dumps(normalized), "360Giving", "grant-alpha", "https://example.test/grant-alpha",
                "2026-07-10T00:00:00Z",
            ),
        )
        self.conn.commit()
        records = [
            registry_record(101, 1001, "Alpha Foundation, CIO", income=100_000, expenditure=90_000),
            registry_record(102, 1002, "Beta & Community Trust", income=None, expenditure=0, city="Bristol", region="Somerset"),
            registry_record(103, 1003, "Gamma Foundation", status="Removed", income=20_000, expenditure=None),
            registry_record(104, 1004, "Alpha Foundation CIO", income=75_000, expenditure=65_000),
            registry_record(105, 1005, "Élan Children’s Charity", income=5_000, expenditure=4_000),
            {"registered_charity_number": 9999, "charity_name": "Invalid without stable organization number"},
        ]
        with open(self.source_path, "w", encoding="utf-8") as handle:
            json.dump(records, handle)
        self.first_import = import_charity_commission_registry(self.db_path, self.source_path, batch_size=2)
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    async def test_import_is_idempotent_and_keeps_lightweight_rows(self):
        self.assertEqual(self.first_import["records_read"], 6)
        self.assertEqual(self.first_import["records_inserted"], 5)
        self.assertEqual(self.first_import["invalid_records"], 1)
        repeat = import_charity_commission_registry(self.db_path, self.source_path, batch_size=3)
        self.assertEqual(repeat["records_inserted"], 0)
        self.assertEqual(repeat["records_updated"], 5)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(f"SELECT COUNT(*) FROM {REGISTRY_TABLE}").fetchone()[0]
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({REGISTRY_TABLE})")}
        conn.close()
        self.assertEqual(count, 5)
        self.assertNotIn("raw_json", columns)

    async def test_exact_number_and_normalized_name_search(self):
        exact = await self.repo.get_registry_page(charity_number="1001")
        self.assertEqual([item["registry_id"] for item in exact["results"]], ["cc:101"])
        name = await self.repo.get_registry_page(query="alpha foundation cio")
        self.assertEqual({item["registry_id"] for item in name["results"]}, {"cc:101", "cc:104"})
        unicode_name = await self.repo.get_registry_page(query="elan children")
        self.assertEqual(unicode_name["results"][0]["registry_id"], "cc:105")

    async def test_status_financial_filters_and_unknown_values(self):
        removed = await self.repo.get_registry_page(status="Removed")
        self.assertEqual([item["registry_id"] for item in removed["results"]], ["cc:103"])
        income = await self.repo.get_registry_page(income_min=70_000, income_max=110_000)
        self.assertEqual({item["registry_id"] for item in income["results"]}, {"cc:101", "cc:104"})
        detail = await self.repo.get_registry_detail("cc:102")
        self.assertIsNone(detail["income"])
        self.assertEqual(detail["expenditure"], 0.0)

    async def test_cursor_pages_are_stable_and_nonduplicated(self):
        first = await self.repo.get_registry_page(limit=2, sort="name")
        second = await self.repo.get_registry_page(limit=2, sort="name", cursor=first["next_cursor"])
        first_ids = {item["registry_id"] for item in first["results"]}
        second_ids = {item["registry_id"] for item in second["results"]}
        self.assertTrue(first["has_more"])
        self.assertFalse(first_ids.intersection(second_ids))
        with self.assertRaisesRegex(ValueError, "Invalid directory cursor"):
            await self.repo.get_registry_page(cursor="not-a-valid-cursor")
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            await self.repo.get_registry_page(limit=101)

    async def test_accepted_identifier_link_exposes_only_observed_grant_data(self):
        linked = await self.repo.get_registry_detail("cc:101")
        registry_only = await self.repo.get_registry_detail("cc:102")
        self.assertEqual(linked["enriched_profile"]["match_method"], "exact_identifier")
        self.assertTrue(linked["enriched_profile"]["has_grant_data"])
        self.assertIn("Observed 360Giving", linked["observed_grant_data_message"])
        self.assertIsNone(registry_only["enriched_profile"])
        self.assertEqual(
            registry_only["observed_grant_data_message"],
            "Registry entry available. No observed grant data is currently linked to this organization.",
        )
        geography_scoped = await self.repo.get_registry_page(beneficiary_geography="Ghana")
        self.assertEqual([item["registry_id"] for item in geography_scoped["results"]], ["cc:101"])

    async def test_registry_import_cannot_change_grant_map_geography_or_totals(self):
        baseline = await self.repo.get_grants_map()
        self.assertEqual(baseline["known_geography_count"], 1)
        self.assertEqual(baseline["items"][0]["region_or_country_code"], "GH")
        # The registry addresses are in London; importing them must not create UK
        # beneficiary geography or alter the stored 360Giving aggregation.
        after = await self.repo.get_grants_map()
        self.assertEqual(after["known_geography_count"], baseline["known_geography_count"])
        self.assertEqual(after["items"], baseline["items"])

    async def test_organization_profiles_follow_selected_sources(self):
        observed = await self.repo.get_all(sources=["360Giving"])
        unavailable_source = await self.repo.get_all(sources=["Philea"])

        self.assertEqual([item["registered_charity_number"] for item in observed], [1001])
        self.assertEqual(unavailable_source, [])

    async def test_critical_queries_have_indexes_or_fts(self):
        conn = sqlite3.connect(self.db_path)
        number_plan = conn.execute(
            f"EXPLAIN QUERY PLAN SELECT registry_id FROM {REGISTRY_TABLE} WHERE charity_number = ?", ("1001",)
        ).fetchall()
        fts_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (REGISTRY_FTS_TABLE,)
        ).fetchone()
        conn.close()
        self.assertTrue(any("idx_registry_charity_number" in str(row) for row in number_plan))
        self.assertTrue(fts_exists is not None)

    async def test_directory_api_is_bounded_and_summary_only(self):
        app.dependency_overrides[get_charity_repository] = lambda: self.repo
        try:
            with TestClient(app) as client:
                login = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
                response = client.get(
                    "/api/charities/directory/organizations?query=alpha&limit=50",
                    cookies=login.cookies,
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertLessEqual(len(payload["results"]), 50)
                self.assertNotIn("activity_text", payload["results"][0])
                self.assertNotIn("grants", payload["results"][0])
                self.assertEqual(
                    client.get(
                        "/api/charities/directory/organizations?limit=101",
                        cookies=login.cookies,
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.get(
                        "/api/charities/directory/organizations?cursor=invalid",
                        cookies=login.cookies,
                    ).status_code,
                    400,
                )
                detail = client.get(
                    "/api/charities/directory/organizations/cc:102",
                    cookies=login.cookies,
                )
                self.assertEqual(detail.status_code, 200)
                self.assertIn("No observed grant data", detail.json()["observed_grant_data_message"])
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
