import json
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal

from fastapi.testclient import TestClient

from bff.main import app
from bff.repositories import SQLiteCharityRepository, get_charity_repository
from data.db_loader import create_tables


class GrantAnalyticsFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "analytics.db")
        self.conn = sqlite3.connect(self.db_path)
        create_tables(self.conn)
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def grant(
        self,
        grant_id,
        *,
        date="2025-01-15",
        amount=100,
        currency="GBP",
        source_categories=None,
        inferred_categories=None,
        scores=None,
        source="360Giving",
        ingested="2026-01-10T12:00:00Z",
    ):
        self.conn.execute(
            """
            INSERT INTO grants (
                grant_id, funding_name, recipient_name, amount, currency, date,
                programme_area_source, programme_area_inferred, programme_area_scores,
                source, source_record_id, ingestion_timestamp
            ) VALUES (?, 'Test donor', 'Test recipient', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant_id,
                amount,
                currency,
                date,
                json.dumps(source_categories or []),
                json.dumps(inferred_categories or []),
                json.dumps(scores or {}),
                source,
                grant_id,
                ingested,
            ),
        )
        self.conn.commit()


class TestGrantTrends(GrantAnalyticsFixture):
    async def test_aggregates_same_and_multiple_months_in_chronological_order(self):
        self.grant("JAN-1", date="2025-01-03", amount=10.01)
        self.grant("JAN-2", date="2025-01-31T00:00:00Z", amount=20.02)
        self.grant("MAR", date="2025-03-01", amount=30.03)

        result = await self.repo.get_grant_trends("GBP", months=3)

        self.assertEqual(result["date_basis"], "award_date")
        self.assertEqual(result["period"], {
            "from": "2025-01", "to": "2025-03", "months": 3,
            "anchor": "latest_available_award_month",
        })
        self.assertEqual([item["month"] for item in result["items"]], [
            "2025-01", "2025-02", "2025-03"
        ])
        self.assertEqual(result["items"][0]["grant_count"], 2)
        self.assertEqual(result["items"][0]["total_amount"], 30.03)
        self.assertEqual(result["items"][1]["coverage_status"], "unknown")
        self.assertIsNone(result["items"][1]["total_amount"])
        self.assertEqual(result["items"][2]["total_amount"], 30.03)

    async def test_exposes_date_and_amount_quality_and_keeps_numeric_zero(self):
        self.grant("VALID", amount=10)
        self.grant("MISSING-DATE", date=None, amount=5)
        self.grant("INVALID-DATE", date="not-a-date", amount=5)
        self.grant("INVALID-CALENDAR-DATE", date="2025-02-30", amount=5)
        self.grant("MISSING-AMOUNT", amount=None)
        self.grant("INVALID-AMOUNT", amount="not-money")
        self.grant("ZERO", amount=0)
        self.grant("NEGATIVE", amount=-7.25)

        result = await self.repo.get_grant_trends("GBP", months=1)

        self.assertEqual(result["excluded"]["missing_date"], 1)
        self.assertEqual(result["excluded"]["invalid_date"], 2)
        self.assertEqual(result["excluded"]["missing_amount"], 1)
        self.assertEqual(result["excluded"]["invalid_amount"], 1)
        self.assertEqual(result["excluded"]["negative_amount"], 1)
        self.assertEqual(result["zero_amount_count"], 1)
        self.assertEqual(result["items"][0]["grant_count"], 2)
        self.assertEqual(result["items"][0]["total_amount"], 10.0)
        self.assertEqual(result["amount_policy"]["negative_amounts"], "excluded_and_reported")

    async def test_requires_currency_selection_and_rejects_unsupported_currency(self):
        self.grant("GBP", amount=100, currency="GBP")
        self.grant("EUR", amount=200, currency="EUR")
        self.grant("BAD-CURRENCY", amount=300, currency="")
        self.grant("MALFORMED-CURRENCY", amount=400, currency="12?")

        mixed = await self.repo.get_grant_trends(months=1)
        selected = await self.repo.get_grant_trends("EUR", months=1)
        unsupported = await self.repo.get_grant_trends("USD", months=1)

        self.assertEqual(mixed["status"], "currency_selection_required")
        self.assertEqual(mixed["available_currencies"], ["EUR", "GBP"])
        self.assertEqual(selected["items"][0]["total_amount"], 200.0)
        self.assertEqual(selected["excluded"]["currency_filtered"], 1)
        self.assertEqual(selected["excluded"]["unsupported_currency"], 2)
        self.assertEqual(unsupported["status"], "unsupported_currency")

    async def test_latest_source_month_anchors_period_instead_of_current_month(self):
        self.grant("OLD", date="2020-05-20", amount=50)

        result = await self.repo.get_grant_trends("GBP", months=2)

        self.assertEqual(result["latest_award_date"], "2020-05-20")
        self.assertEqual(result["period"]["from"], "2020-04")
        self.assertEqual(result["period"]["to"], "2020-05")
        self.assertEqual(result["items"][0]["coverage_status"], "unknown")

    async def test_empty_and_no_qualifying_records_return_truthful_states(self):
        empty = await self.repo.get_grant_trends("GBP")
        self.assertEqual(empty["status"], "no_data")

        self.grant("INVALID", date="not-a-date", amount=None)
        no_qualifying = await self.repo.get_grant_trends("GBP")
        self.assertEqual(no_qualifying["status"], "no_qualifying_records")
        self.assertEqual(no_qualifying["items"], [])


class TestGrantThemes(GrantAnalyticsFixture):
    async def test_valid_source_category_takes_precedence_over_inference(self):
        self.grant(
            "SOURCE", amount=100, source_categories=["Education"],
            inferred_categories=["Health"], scores={"Health": 0.9},
        )

        result = await self.repo.get_grant_themes("GBP")

        self.assertEqual([item["programme_area"] for item in result["items"]], ["Education"])
        self.assertEqual(result["classification_coverage"]["source_classified_grant_count"], 1)
        self.assertEqual(result["items"][0]["inferred_classified_grant_count"], 0)

    async def test_invalid_source_does_not_suppress_valid_inference(self):
        self.grant(
            "FALLBACK", amount=100, source_categories=["Unknown scheme name"],
            inferred_categories=["Health"], scores={"Health": 0.85},
        )

        result = await self.repo.get_grant_themes("GBP")

        self.assertEqual(result["items"][0]["programme_area"], "Health")
        coverage = result["classification_coverage"]
        self.assertEqual(coverage["invalid_source_label_count"], 1)
        self.assertEqual(coverage["inferred_classified_grant_count"], 1)

    async def test_low_confidence_inference_and_missing_category_are_unclassified(self):
        self.grant(
            "LOW", amount=10, inferred_categories=["Health"], scores={"Health": 0.54}
        )
        self.grant("NONE", amount=20)

        result = await self.repo.get_grant_themes("GBP")

        self.assertEqual(result["items"][0]["programme_area"], "Unclassified")
        self.assertEqual(result["items"][0]["allocated_amount"], 30.0)
        self.assertEqual(result["items"][0]["unclassified_grant_count"], 2)
        self.assertEqual(result["classification_coverage"]["unclassified_grant_count"], 2)
        self.assertEqual(result["classification_coverage"]["low_confidence_inference_count"], 1)
        self.assertEqual(result["inference_confidence_threshold"], 0.55)

    async def test_equal_split_preserves_amount_for_two_and_three_categories(self):
        self.grant(
            "TWO", amount=100.01,
            inferred_categories=["Health", "Education"],
            scores={"Health": 0.8, "Education": 0.8},
        )
        self.grant(
            "THREE", amount=0.01,
            inferred_categories=["Health", "Education", "Youth/Children Development"],
            scores={
                "Health": 0.8, "Education": 0.8,
                "Youth/Children Development": 0.8,
            },
        )

        result = await self.repo.get_grant_themes("GBP")

        self.assertEqual(result["qualifying_amount"], 100.02)
        self.assertEqual(result["allocated_amount"], 100.02)
        self.assertEqual(
            sum(Decimal(str(item["allocated_amount"])) for item in result["items"]),
            Decimal("100.02"),
        )
        self.assertAlmostEqual(
            sum(item["weighted_grant_count"] for item in result["items"]), 2.0, places=5
        )
        health = next(item for item in result["items"] if item["programme_area"] == "Health")
        self.assertEqual(health["distinct_grant_count"], 2)
        self.assertEqual(result["classification_coverage"]["multiple_programme_area_grant_count"], 2)

    async def test_single_category_receives_complete_amount_and_counts_are_distinct(self):
        self.grant(
            "ONE", amount=12.34, inferred_categories=["Health"], scores={"Health": 0.8}
        )

        result = await self.repo.get_grant_themes("GBP")
        item = result["items"][0]

        self.assertEqual(item["allocated_amount"], 12.34)
        self.assertEqual(item["distinct_grant_count"], 1)
        self.assertEqual(item["weighted_grant_count"], 1.0)
        self.assertEqual(item["inferred_classified_grant_count"], 1)

    async def test_amount_exclusions_zero_and_currency_isolation_are_explicit(self):
        self.grant("VALID", amount=10)
        self.grant("ZERO", amount=0)
        self.grant("MISSING", amount=None)
        self.grant("INVALID", amount="bad")
        self.grant("NEGATIVE", amount=-2)
        self.grant("EUR", amount=90, currency="EUR")

        result = await self.repo.get_grant_themes("GBP")

        self.assertEqual(result["qualifying_amount"], 10.0)
        self.assertEqual(result["classification_coverage"]["qualifying_grant_count"], 2)
        self.assertEqual(result["zero_amount_count"], 1)
        self.assertEqual(result["excluded"]["missing_amount"], 1)
        self.assertEqual(result["excluded"]["invalid_amount"], 1)
        self.assertEqual(result["excluded"]["negative_amount"], 1)
        self.assertEqual(result["excluded"]["currency_filtered"], 1)

    async def test_empty_and_currency_selection_states(self):
        empty = await self.repo.get_grant_themes("GBP")
        self.assertEqual(empty["status"], "no_data")

        self.grant("GBP", currency="GBP")
        self.grant("EUR", currency="EUR")
        mixed = await self.repo.get_grant_themes()
        unsupported = await self.repo.get_grant_themes("USD")
        self.assertEqual(mixed["status"], "currency_selection_required")
        self.assertEqual(unsupported["status"], "unsupported_currency")


class TestGrantAnalyticsAPI(GrantAnalyticsFixture):
    def setUp(self):
        super().setUp()
        self.grant(
            "API", date="2024-07-01", amount=25, source_categories=["Health"]
        )
        app.dependency_overrides[get_charity_repository] = lambda: self.repo
        self.client = TestClient(app)
        self.client.post("/api/auth/login", json={"username": "admin", "password": "password"})

    def tearDown(self):
        app.dependency_overrides.clear()
        super().tearDown()

    async def test_typed_routes_and_month_validation(self):
        trends = self.client.get("/api/charities/grants/trends?currency=GBP&months=1")
        themes = self.client.get("/api/charities/grants/themes?currency=GBP")
        invalid_months = self.client.get("/api/charities/grants/trends?months=0")

        self.assertEqual(trends.status_code, 200)
        self.assertEqual(trends.json()["period"]["from"], "2024-07")
        self.assertEqual(themes.status_code, 200)
        self.assertEqual(themes.json()["items"][0]["programme_area"], "Health")
        self.assertEqual(invalid_months.status_code, 422)


if __name__ == "__main__":
    unittest.main()
