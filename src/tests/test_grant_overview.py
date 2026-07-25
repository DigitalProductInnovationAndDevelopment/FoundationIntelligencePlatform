import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from bff.main import app
from bff.repositories import JSONCharityRepository, SQLiteCharityRepository, get_charity_repository
from data.db_loader import create_tables


class GrantOverviewFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "overview.db")
        self.conn = sqlite3.connect(self.db_path)
        create_tables(self.conn)
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def grant(self, grant_id, *, date, amount, donor="Alpha Fund", recipient="Recipient", geography="Ghana", programme="Health", currency="GBP", source="360Giving"):
        self.conn.execute(
            """
            INSERT INTO grants (
                grant_id, funding_name, recipient_name, amount, currency, date,
                beneficiary_geography, beneficiary_geography_normalized,
                programme_area_source, programme_area_inferred, programme_area_scores,
                source, source_record_id, ingestion_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?, ?, '2026-07-01T00:00:00Z')
            """,
            (
                grant_id, donor, recipient, amount, currency, date,
                json.dumps([{"name": geography, "countryCode": "GH" if geography == "Ghana" else "KE"}]),
                json.dumps([{"name": geography, "code": "GH" if geography == "Ghana" else "KE", "scope": "country"}]),
                json.dumps([programme]), source, grant_id,
            ),
        )
        self.conn.commit()

    def set_eur_conversion(self, grant_id, amount_eur, status="ecb_award_date"):
        self.conn.execute(
            "UPDATE grants SET amount_eur = ?, conversion_status = ? WHERE grant_id = ?",
            (amount_eur, status, grant_id),
        )
        self.conn.commit()


class TestGrantOverview(GrantOverviewFixture):
    async def test_filter_scope_is_shared_by_kpis_map_trends_and_themes(self):
        self.grant("GH-HEALTH", date="2025-01-15", amount=100, geography="Ghana", programme="Health")
        self.grant("KE-EDU", date="2025-02-15", amount=250, geography="Kenya", programme="Education", donor="Beta Fund")

        result = await self.repo.get_grant_overview(
            currency="GBP",
            date_from="2025-01-01",
            date_to="2025-01-31",
            beneficiary_geographies=["Ghana"],
            programme_areas=["Health"],
            donor="alpha",
            granularity="monthly",
        )

        self.assertEqual(result["kpis"]["grants_monitored"], 1)
        self.assertEqual(result["kpis"]["awarded_funding"], 100.0)
        self.assertEqual(result["map"]["known_geography_count"], 1)
        self.assertEqual(result["map"]["items"][0]["region_or_country_name"], "Ghana")
        self.assertEqual(result["trends"]["items"][0]["grant_count"], 1)
        self.assertEqual(result["themes"]["items"][0]["programme_area"], "Health")
        self.assertEqual(result["themes"]["classification_coverage"]["qualifying_grant_count"], 1)

    async def test_auto_granularity_uses_monthly_for_short_and_yearly_for_long_ranges(self):
        self.grant("JAN", date="2025-01-15", amount=100)
        self.grant("MAR", date="2025-03-15", amount=100)
        short = await self.repo.get_grant_overview(currency="GBP", date_from="2025-01-01", date_to="2025-03-31")
        self.assertEqual(short["trends"]["granularity"], "monthly")

        self.grant("OLD", date="2020-01-15", amount=100)
        long = await self.repo.get_grant_overview(currency="GBP")
        self.assertEqual(long["trends"]["granularity"], "yearly")
        self.assertEqual(long["trends"]["items"][0]["month"], "2020")

    async def test_unclassified_grants_remain_explicit(self):
        self.grant("UNCLASSIFIED", date="2025-01-15", amount=50, programme="Unknown source label")
        result = await self.repo.get_grant_overview(currency="GBP")
        item = next(item for item in result["themes"]["items"] if item["programme_area"] == "Unclassified")
        self.assertEqual(item["allocated_amount"], 50.0)
        self.assertEqual(result["themes"]["classification_coverage"]["unclassified_grant_count"], 1)

    async def test_auto_uses_only_backfilled_eur_values_and_raw_filters_remain_native(self):
        self.grant("GBP-GH", date="2025-01-15", amount=100, geography="Ghana", currency="GBP")
        self.grant("EUR-KE", date="2025-02-15", amount=200, geography="Kenya", currency="EUR")
        self.set_eur_conversion("GBP-GH", 125)
        self.set_eur_conversion("EUR-KE", 200, "native_eur")

        result = await self.repo.get_grant_overview()

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["kpis"]["grants_monitored"], 2)
        self.assertEqual(result["kpis"]["awarded_funding"], 325.0)
        self.assertEqual(result["kpis"]["currency"], "EUR")
        self.assertEqual(result["map"]["status"], "available")
        self.assertEqual(result["map"]["known_geography_count"], 2)
        self.assertEqual({item["region_or_country_name"] for item in result["map"]["items"]}, {"Ghana", "Kenya"})
        self.assertEqual({item["total_amount"] for item in result["map"]["items"]}, {125.0, 200.0})
        self.assertTrue(result["map"]["funding_mode_available"])

        native_eur = await self.repo.get_grant_overview(currency="EUR")
        self.assertEqual(native_eur["kpis"]["grants_monitored"], 1)
        self.assertEqual(native_eur["kpis"]["awarded_funding"], 200.0)
        self.assertEqual(native_eur["applied_filters"]["currency_mode"], "source_currency")

    async def test_source_selection_changes_the_shared_overview_scope(self):
        self.grant("OBSERVED", date="2025-01-15", amount=100, source="360Giving")
        self.grant("OTHER", date="2025-01-16", amount=250, source="Other source")

        observed = await self.repo.get_grant_overview(currency="GBP", sources=["360Giving"])
        other = await self.repo.get_grant_overview(currency="GBP", sources=["Other source"])

        self.assertEqual(observed["kpis"]["grants_monitored"], 1)
        self.assertEqual(observed["kpis"]["awarded_funding"], 100.0)
        self.assertEqual(other["kpis"]["grants_monitored"], 1)
        self.assertEqual(other["kpis"]["awarded_funding"], 250.0)

    async def test_reuses_persistent_overview_cache_for_the_same_scope(self):
        self.grant("CACHED", date="2025-01-15", amount=100)
        first = await self.repo.get_grant_overview(currency="GBP")

        with patch.object(self.repo, "_overview_source_rows", side_effect=AssertionError("cache miss")):
            cached = await self.repo.get_grant_overview(currency="GBP")

        self.assertEqual(cached, first)

    async def test_connections_are_deferred_until_explicitly_requested(self):
        self.grant("FLOW", date="2025-01-15", amount=100, geography="Ghana")
        self.conn.execute(
            "UPDATE grants SET raw_grant_data = ? WHERE grant_id = ?",
            (json.dumps({"fundingOrganization": [{"addressCountry": "United Kingdom"}]}), "FLOW"),
        )
        self.conn.commit()

        default_view = await self.repo.get_grant_overview(currency="GBP")
        with_connections = await self.repo.get_grant_overview(
            currency="GBP", include_connections=True
        )

        self.assertEqual(default_view["map"]["connections"], [])
        self.assertEqual(default_view["map"]["connection_grant_count"], 0)
        self.assertEqual(with_connections["map"]["connection_grant_count"], 1)
        self.assertEqual(with_connections["map"]["connections"][0]["origin_country_code"], "GB")
        self.assertEqual(with_connections["map"]["connections"][0]["destination_country_code"], "GH")

    async def test_country_options_use_the_derived_country_index(self):
        self.grant("GH", date="2025-01-15", amount=100, geography="Ghana")
        self.grant("KE", date="2025-02-15", amount=100, geography="Kenya")

        options = await self.repo.get_beneficiary_geography_options(sources=["360Giving"])

        self.assertEqual(options, ["Ghana", "Kenya"])

    async def test_overview_keeps_mapped_countries_visible_at_low_coverage(self):
        self.grant("MAPPED", date="2025-01-15", amount=100, geography="Ghana")
        for grant_id in ("UNMAPPED-1", "UNMAPPED-2", "UNMAPPED-3"):
            self.grant(grant_id, date="2025-01-15", amount=100, geography="Ghana")
            self.conn.execute(
                "UPDATE grants SET beneficiary_geography = '[]', beneficiary_geography_normalized = '[]' WHERE grant_id = ?",
                (grant_id,),
            )
        self.conn.commit()

        result = await self.repo.get_grant_overview(currency="GBP")

        self.assertEqual(result["map"]["status"], "available")
        self.assertEqual(result["map"]["coverage_percentage"], 25.0)
        self.assertEqual([item["region_or_country_code"] for item in result["map"]["items"]], ["GH"])

    async def test_overview_endpoint_validates_dates_and_exposes_one_payload(self):
        self.grant("GH", date="2025-01-15", amount=100)
        app.dependency_overrides[get_charity_repository] = lambda: self.repo
        try:
            with TestClient(app) as client:
                login = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
                invalid = client.get("/api/charities/grants/overview?date_from=2025-02-10&date_to=2025-01-01", cookies=login.cookies)
                self.assertEqual(invalid.status_code, 400)
                response = client.get("/api/charities/grants/overview?currency=GBP&beneficiary_geographies=Ghana", cookies=login.cookies)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["kpis"]["grants_monitored"], 1)
                self.assertEqual(payload["map"]["known_geography_count"], 1)
                self.assertEqual(payload["themes"]["classification_coverage"]["qualifying_grant_count"], 1)
                trend = client.get(
                    "/api/charities/grants/overview/trends?currency=GBP&beneficiary_geographies=Ghana&granularity=monthly",
                    cookies=login.cookies,
                )
                self.assertEqual(trend.status_code, 200)
                self.assertEqual(trend.json()["items"][0]["month"], "2025-01")
                self.assertEqual(trend.json()["items"][0]["mapped_grant_count"], 1)
                self.assertEqual(trend.json()["granularity"], "monthly")
                geographies = client.get("/api/charities/grants/beneficiary-geographies", cookies=login.cookies)
                self.assertEqual(geographies.status_code, 200)
                self.assertEqual(geographies.json(), ["Ghana"])
        finally:
            app.dependency_overrides.clear()

    async def test_json_fallback_returns_an_explicit_unavailable_overview(self):
        fallback = JSONCharityRepository(os.path.join(self.temp_dir.name, "missing.json"))
        payload = await fallback.get_grant_overview(currency="GBP", granularity="monthly")

        self.assertEqual(payload["status"], "transaction_data_unavailable")
        self.assertEqual(payload["kpis"]["grants_monitored"], 0)
        self.assertEqual(payload["trends"]["status"], "transaction_data_unavailable")


if __name__ == "__main__":
    unittest.main()
