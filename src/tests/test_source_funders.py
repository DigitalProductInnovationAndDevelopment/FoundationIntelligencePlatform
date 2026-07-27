import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from bff.main import app
from bff.repositories import SQLiteCharityRepository, get_charity_repository
from data.db_loader import create_tables, insert_charities, link_existing_grants_to_charities


class TestSourceFunders(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "source-funders.db")
        self.conn = sqlite3.connect(self.db_path)
        create_tables(self.conn)
        self.conn.execute("INSERT INTO charities (charity_id, name) VALUES (1, 'Verified Alpha Foundation')")
        self.conn.commit()
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def grant(
        self,
        grant_id,
        *,
        funder_name,
        funder_source_id=None,
        funder_charity_id=None,
        recipient="Recipient",
        amount=100,
        geography=None,
        programme="Health",
        currency="EUR",
        date="2025-01-15",
        conversion_status="native_eur",
        raw_grant_data=None,
    ):
        geography = geography or [{"name": "Ghana", "countryCode": "GH"}]
        normalized = [
            {"name": item["name"], "code": item["countryCode"], "scope": "country"}
            for item in geography
        ]
        self.conn.execute(
            """
            INSERT INTO grants (
                grant_id, funding_charity_id, funding_name, funding_org_source_id,
                recipient_name, recipient_org_source_id, amount, amount_eur,
                conversion_status, currency, date, beneficiary_geography,
                beneficiary_geography_normalized, programme_area_source,
                programme_area_inferred, programme_area_scores, source, source_url,
                source_record_id, ingestion_timestamp, raw_grant_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', '360Giving', ?, ?, '2026-07-01T00:00:00Z', ?)
            """,
            (
                grant_id,
                funder_charity_id,
                funder_name,
                funder_source_id,
                recipient,
                f"recipient-{recipient}",
                amount,
                amount if conversion_status in {"native_eur", "ecb_award_date", "ecb_previous_business_day"} else None,
                conversion_status,
                currency,
                date,
                json.dumps(geography),
                json.dumps(normalized),
                json.dumps([programme]),
                f"https://example.test/grants/{grant_id}",
                grant_id,
                json.dumps(raw_grant_data) if raw_grant_data is not None else None,
            ),
        )
        self.conn.commit()

    async def test_country_results_use_source_identity_and_exclude_multi_country_amounts(self):
        self.grant("A-1", funder_name="Alpha Foundation", funder_source_id="alpha-source", funder_charity_id=1, amount=100)
        self.grant("A-2", funder_name="Alpha Foundation renamed", funder_source_id="alpha-source", amount=200,
                   geography=[{"name": "Ghana", "countryCode": "GH"}, {"name": "Kenya", "countryCode": "KE"}])
        self.grant("A-3", funder_name="Alpha Foundation", funder_source_id="a-different-source", amount=50)
        self.grant("B-1", funder_name="Beta Fund", amount=75, recipient="Second recipient")

        response = await self.repo.get_source_funders(beneficiary_country="GH", page_size=25)

        self.assertEqual(response["country"], {"code": "GH", "name": "Ghana"})
        self.assertEqual(response["summary"]["matching_grant_count"], 4)
        self.assertEqual(response["summary"]["matching_funder_count"], 3)
        self.assertEqual(response["summary"]["distinct_recipient_count"], 2)
        alpha = next(item for item in response["items"] if item["display_name"] == "Alpha Foundation")
        self.assertEqual(alpha["activity"]["grant_count"], 2)
        self.assertEqual(alpha["observed_funding"]["amount"], 100.0)
        self.assertEqual(alpha["observed_funding"]["excluded_multi_country_grant_count"], 1)
        self.assertEqual(alpha["linked_directory_profile"], {"charity_id": 1, "name": "Verified Alpha Foundation"})
        self.assertFalse(alpha["source_only"])

        overview = await self.repo.get_grant_overview()
        gh_item = next(item for item in overview["map"]["items"] if item["region_or_country_code"] == "GH")
        self.assertEqual(gh_item["distinct_funders"], response["summary"]["matching_funder_count"])

    async def test_currency_and_programme_filters_keep_the_shared_grant_scope(self):
        self.grant("TECH", funder_name="Tech Fund", funder_source_id="tech", amount=100, programme="tech-enablement")
        self.grant("HEALTH", funder_name="Health Fund", funder_source_id="health", amount=100, programme="Health")
        self.grant("GBP", funder_name="Sterling Fund", funder_source_id="gbp", amount=100, currency="GBP", conversion_status="ecb_award_date")

        response = await self.repo.get_source_funders(
            beneficiary_country="GH",
            currency="EUR",
            programme_areas=["tech-enablement"],
        )

        self.assertEqual(response["summary"]["matching_grant_count"], 1)
        self.assertEqual([item["display_name"] for item in response["items"]], ["Tech Fund"])
        self.assertEqual(response["items"][0]["observed_funding"]["currency"], "EUR")

    async def test_auto_eur_returns_single_original_currency_when_conversion_is_missing(self):
        self.grant(
            "GBP-NO-EUR", funder_name="Sterling Fund", funder_source_id="sterling",
            amount=125, currency="GBP", conversion_status="missing",
        )

        response = await self.repo.get_source_funders(beneficiary_country="GH")
        funding = response["items"][0]["observed_funding"]

        self.assertIsNone(funding["amount"])
        self.assertEqual(funding["fallback_original_amount"], 125.0)
        self.assertEqual(funding["fallback_original_currency"], "GBP")
        self.assertEqual(funding["fallback_original_grant_count"], 1)

    async def test_detail_does_not_present_missing_eur_conversion_as_zero_euros(self):
        self.grant(
            "GBP-NO-EUR", funder_name="Sterling Fund", funder_source_id="sterling",
            recipient="Unconverted recipient", amount=125, currency="GBP",
            conversion_status="missing",
        )
        listing = await self.repo.get_source_funders(beneficiary_country="GH")
        detail = await self.repo.get_source_funder_detail(
            listing["items"][0]["source_funder_key"], beneficiary_country="GH"
        )

        self.assertEqual(detail["top_recipients"], [])
        self.assertEqual(detail["relationships"]["status"], "no_monetary_transactions")
        self.assertEqual(detail["relationship_summary"]["recipient_count"], 1)

    async def test_sorting_uses_country_attributable_amount_but_recency_keeps_multi_country_activity(self):
        self.grant("RECENT-MULTI", funder_name="Recent multi-country", funder_source_id="recent", amount=500,
                   date="2025-12-01", geography=[{"name": "Ghana", "countryCode": "GH"}, {"name": "Kenya", "countryCode": "KE"}])
        self.grant("OLDER-SINGLE", funder_name="Older single-country", funder_source_id="older", amount=100,
                   date="2025-01-01")

        by_funding = await self.repo.get_source_funders(beneficiary_country="GH", sort="largest_observed_funding")
        by_recency = await self.repo.get_source_funders(beneficiary_country="GH", sort="most_recently_active")

        self.assertEqual(by_funding["items"][0]["display_name"], "Older single-country")
        self.assertEqual(by_recency["items"][0]["display_name"], "Recent multi-country")
        self.assertEqual(by_recency["items"][0]["observed_funding"]["excluded_multi_country_grant_count"], 1)

    async def test_detail_is_source_funder_detail_not_a_synthetic_profile(self):
        self.grant("A-1", funder_name="Source-only Fund", funder_source_id="source-only", recipient="Recipient A")
        response = await self.repo.get_source_funders(beneficiary_country="GH")

        detail = await self.repo.get_source_funder_detail(
            response["items"][0]["source_funder_key"], beneficiary_country="GH"
        )

        self.assertEqual(detail["metadata"]["detail_type"], "source_funder")
        self.assertTrue(detail["funder"]["source_only"])
        self.assertEqual(detail["grant_sample"][0]["recipient_name"], "Recipient A")

    async def test_detail_relationships_use_source_identity_and_do_not_allocate_multi_country_awards(self):
        self.grant("A-1", funder_name="Source-only Fund", funder_source_id="source-only", recipient="Recipient A", amount=200)
        self.grant("A-2", funder_name="Source-only Fund renamed", funder_source_id="source-only", recipient="Recipient B", amount=100)
        self.grant(
            "A-3", funder_name="Source-only Fund", funder_source_id="source-only", recipient="Recipient C", amount=500,
            geography=[{"name": "Ghana", "countryCode": "GH"}, {"name": "Kenya", "countryCode": "KE"}],
        )
        listing = await self.repo.get_source_funders(beneficiary_country="GH")
        detail = await self.repo.get_source_funder_detail(
            listing["items"][0]["source_funder_key"], beneficiary_country="GH"
        )

        relationships = detail["relationships"]
        self.assertEqual(relationships["status"], "available")
        self.assertEqual(len(relationships["nodes"]), 3)
        self.assertEqual(len(relationships["links"]), 2)
        self.assertEqual(relationships["metadata"]["included_grant_count"], 2)
        self.assertEqual(relationships["metadata"]["included_value"], 300.0)
        self.assertEqual(relationships["metadata"]["excluded_reasons"], {"multi_country_award": 1})
        self.assertEqual(relationships["nodes"][0]["label"], "Source-only Fund")

    async def test_search_and_status_filters_remain_within_observed_funder_population(self):
        self.grant(
            "LINKED", funder_name="Linked Alpha", funder_source_id="linked-alpha",
            funder_charity_id=1,
        )
        self.grant("OBSERVED", funder_name="Observed Beta", funder_source_id="observed-beta")

        linked = await self.repo.get_source_funders(
            beneficiary_country="GH", search="verified alpha", profile_status="linked",
        )
        observed = await self.repo.get_source_funders(
            beneficiary_country="GH", profile_status="observed_only",
        )

        self.assertEqual([item["display_name"] for item in linked["items"]], ["Linked Alpha"])
        self.assertEqual(linked["items"][0]["profile_link"]["status"], "single")
        self.assertEqual([item["display_name"] for item in observed["items"]], ["Observed Beta"])
        self.assertEqual(linked["summary"]["status_counts"], {
            "all": 1, "linked": 1, "observed_only": 0,
        })

    async def test_new_profile_exactly_relinks_prior_source_grants_and_rebuilds_facts(self):
        self.grant(
            "PRIOR-CHC-GRANT",
            funder_name="Confirmed Funder",
            funder_source_id="GB-CHC-1075920",
            funder_charity_id=None,
        )
        before = await self.repo.get_source_funders(beneficiary_country="GH")
        self.assertEqual(before["items"][0]["profile_link"]["status"], "none")

        insert_charities(self.conn, [{
            "charity_id": 1075920,
            "name": "Confirmed Funder",
            "type": "Charity",
        }])

        grant_row = self.conn.execute(
            "SELECT funding_charity_id FROM grants WHERE grant_id = 'PRIOR-CHC-GRANT'"
        ).fetchone()
        self.assertEqual(grant_row[0], 1075920)
        after = await self.repo.get_source_funders(beneficiary_country="GH")
        self.assertEqual(after["items"][0]["profile_link"]["status"], "single")
        self.assertEqual(after["items"][0]["profile_link"]["profile_id"], 1075920)

    async def test_reset_source_funder_keeps_grants_and_removes_only_its_profile_link(self):
        self.grant(
            "FOOTHOLD-MAPPED", funder_name="Foothold", funder_source_id="GB-CHC-1",
            funder_charity_id=1,
        )
        self.grant(
            "FOOTHOLD-UNMAPPED", funder_name="Foothold", funder_source_id="GB-CHC-1",
            funder_charity_id=1, geography=[],
        )
        self.grant(
            "KEEP", funder_name="Keep Fund", funder_source_id="keep-id",
            funder_charity_id=1,
        )

        before = await self.repo.get_source_funders(beneficiary_country="GH")
        foothold_key = next(
            item["source_funder_key"]
            for item in before["items"] if item["display_name"] == "Foothold"
        )
        reset = await self.repo.reset_source_funder_to_observed(foothold_key)

        self.assertEqual(reset["display_name"], "Foothold")
        self.assertEqual(reset["updated_grant_count"], 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM grants WHERE funding_org_source_id = 'GB-CHC-1'").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM grants WHERE funding_org_source_id = 'GB-CHC-1' AND funding_charity_id IS NULL").fetchone()[0],
            2,
        )
        self.assertEqual(link_existing_grants_to_charities(self.conn, [1]), 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT link_mode FROM source_funder_link_overrides WHERE source_namespace = '360Giving' AND source_organization_id = 'GB-CHC-1'"
            ).fetchone()[0],
            "observed_only",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM grants WHERE funding_org_source_id = 'keep-id'").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM charities WHERE charity_id = 1").fetchone()[0],
            1,
        )
        after = await self.repo.get_source_funders(beneficiary_country="GH")
        foothold = next(item for item in after["items"] if item["display_name"] == "Foothold")
        self.assertEqual(foothold["profile_link"]["status"], "none")

    async def test_source_profile_cache_runs_independently_and_reset_clears_it(self):
        self.grant(
            "PROFILE-CACHE", funder_name="Foothold", funder_source_id="GB-CHC-1",
            funder_charity_id=1,
        )
        donors = await self.repo.get_source_funders(beneficiary_country="GH")
        source_funder_key = donors["items"][0]["source_funder_key"]
        fixture_path = os.path.join(self.temp_dir.name, "profile-source.json")
        with open(fixture_path, "w", encoding="utf-8") as fixture:
            json.dump([{
                "registered_charity_number": 1,
                "all_details": {"charity_name": "Foothold", "latest_income": 10},
                "financial_history": [{"financial_period_end_date": "2025-03-31", "income": 10, "expenditure": 8}],
            }], fixture)

        with patch("bff.repositories.DATA_PATH", fixture_path):
            queued = self.repo.queue_source_funder_profile_hydration(source_funder_key)
            self.assertEqual(queued["status"], "pending")
            hydrated = self.repo.hydrate_source_funder_profile(source_funder_key)

        self.assertEqual(hydrated["status"], "ready")
        cached = self.repo.get_source_funder_profile_cache(source_funder_key)
        self.assertEqual(cached["status"], "ready")
        self.assertEqual(len(cached["payload"]["financial_history"]), 1)

        await self.repo.reset_source_funder_to_observed(source_funder_key)
        self.assertIsNone(self.repo.get_source_funder_profile_cache(source_funder_key))

    async def test_reset_generation_blocks_stale_hydration_and_explicit_relink_restores_only_that_key(self):
        self.grant(
            "FOOTHOLD-GENERATION", funder_name="Foothold", funder_source_id="GB-CHC-1",
            funder_charity_id=1,
        )
        self.grant(
            "OTHER-GENERATION", funder_name="Other", funder_source_id="other-id",
            funder_charity_id=1,
        )
        donors = await self.repo.get_source_funders(beneficiary_country="GH")
        foothold_key = next(item["source_funder_key"] for item in donors["items"] if item["display_name"] == "Foothold")
        self.assertEqual(self.repo.queue_source_funder_profile_hydration(foothold_key)["status"], "pending")

        first = await self.repo.reset_source_funder_to_observed(foothold_key)
        second = await self.repo.reset_source_funder_to_observed(foothold_key)
        self.assertGreaterEqual(first["link_revision"], 1)
        self.assertGreater(second["link_revision"], first["link_revision"])
        # A worker that started before the reset cannot put its stale payload
        # back because the pending cache row was deleted and profile link cleared.
        self.assertIsNone(self.repo.hydrate_source_funder_profile(foothold_key))
        self.assertIsNone(self.repo.queue_source_funder_profile_hydration(foothold_key))
        self.assertEqual(
            self.conn.execute("SELECT funding_charity_id FROM grants WHERE grant_id = 'OTHER-GENERATION'").fetchone()[0],
            1,
        )

        relinked = await self.repo.relink_source_funder_profile(foothold_key, 1)
        self.assertEqual(relinked["profile_id"], 1)
        self.assertEqual(
            self.conn.execute("SELECT funding_charity_id FROM grants WHERE grant_id = 'FOOTHOLD-GENERATION'").fetchone()[0],
            1,
        )
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM source_funder_link_overrides WHERE source_namespace = '360Giving' AND source_organization_id = 'GB-CHC-1'"
        ).fetchone())

    async def test_multiple_profile_links_are_not_auto_selected(self):
        self.conn.execute("INSERT INTO charities (charity_id, name) VALUES (2, 'Second candidate')")
        self.conn.commit()
        self.grant("A", funder_name="Ambiguous", funder_source_id="ambiguous", funder_charity_id=1)
        self.grant("B", funder_name="Ambiguous", funder_source_id="ambiguous", funder_charity_id=2)

        response = await self.repo.get_source_funders(beneficiary_country="GH")
        item = response["items"][0]

        self.assertEqual(item["profile_link"]["status"], "multiple")
        self.assertIsNone(item["linked_directory_profile"])
        self.assertTrue(item["source_only"])

    async def test_summary_detail_is_lazy_and_full_detail_returns_safe_typed_evidence(self):
        raw = {
            "data": {
                "dataSource": "https://publisher.example/grants",
                "fundingOrganization": [{"name": "Evidence Fund", "url": "https://fund.example/"}],
                "recipientOrganization": [{"name": "Recipient", "url": "javascript:alert(1)"}],
            },
            "funders": [{"self": "https://api.threesixtygiving.org/api/v1/org/FUND/"}],
            "recipients": [
                {"self": "https://user:secret@api.threesixtygiving.org/api/v1/org/RECIPIENT/"},
            ],
        }
        self.grant(
            "EVIDENCE", funder_name="Evidence Fund", funder_source_id="evidence",
            raw_grant_data=raw,
        )
        listing = await self.repo.get_source_funders(beneficiary_country="GH")
        key = listing["items"][0]["source_funder_key"]

        summary = await self.repo.get_source_funder_detail(
            key, beneficiary_country="GH", detail_level="summary",
        )
        full = await self.repo.get_source_funder_detail(
            key, beneficiary_country="GH", detail_level="full",
        )

        self.assertEqual(summary["relationships"]["status"], "lazy")
        self.assertEqual(summary["top_recipients"], [])
        self.assertEqual(summary["grant_sample"], [])
        self.assertEqual(summary["source_evidence"], [])
        kinds = {item["kind"] for item in full["source_evidence"]}
        self.assertIn("360giving_funder_record", kinds)
        self.assertIn("observed_funder_website", kinds)
        self.assertIn("publisher_grant_data", kinds)
        self.assertNotIn("360giving_recipient_record", kinds)
        self.assertNotIn("observed_recipient_website", kinds)
        self.assertTrue(all("@" not in item["url"] for item in full["source_evidence"]))
        funder_record = next(item for item in full["source_evidence"] if item["kind"] == "360giving_funder_record")
        self.assertEqual(funder_record["organization_name"], "Evidence Fund")
        self.assertEqual(funder_record["role"], "funder")
        self.assertEqual(funder_record["link_type"], "json")

    async def test_endpoint_validates_country_and_returns_paginated_source_funders(self):
        self.grant("A-1", funder_name="Alpha", funder_source_id="alpha")
        app.dependency_overrides[get_charity_repository] = lambda: self.repo
        try:
            with TestClient(app) as client:
                login = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
                invalid = client.get("/api/charities/grants/funders?beneficiary_country=GHA", cookies=login.cookies)
                self.assertEqual(invalid.status_code, 422)
                response = client.get("/api/charities/grants/funders?beneficiary_country=GH&page_size=25", cookies=login.cookies)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["items"][0]["display_name"], "Alpha")
                self.assertTrue(payload["items"][0]["source_only"])
                self.assertEqual(payload["items"][0]["kind"], "source_funder")
                self.assertEqual(payload["items"][0]["identity"]["method"], "source_id")
                self.assertEqual(payload["items"][0]["profile_link"]["status"], "none")
                summary_detail = client.get(
                    f"/api/charities/grants/funders/{payload['items'][0]['source_funder_key']}?beneficiary_country=GH&detail_level=summary",
                    cookies=login.cookies,
                )
                self.assertEqual(summary_detail.status_code, 200)
                self.assertEqual(summary_detail.json()["metadata"]["detail_level"], "summary")
                self.assertEqual(summary_detail.json()["relationships"]["status"], "lazy")
                self.assertEqual(summary_detail.json()["grant_sample"], [])
                detail = client.get(
                    f"/api/charities/grants/funders/{payload['items'][0]['source_funder_key']}?beneficiary_country=GH",
                    cookies=login.cookies,
                )
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["relationships"]["status"], "available")
                self.assertEqual(detail.json()["relationships"]["links"][0]["grant_count"], 1)
                reset = client.post(
                    f"/api/charities/grants/funders/{payload['items'][0]['source_funder_key']}/reset-to-observed",
                    cookies=login.cookies,
                )
                self.assertEqual(reset.status_code, 200)
                self.assertEqual(reset.json()["status"], "observed_only")
                self.assertEqual(reset.json()["updated_grant_count"], 0)
                after_reset = client.get(
                    "/api/charities/grants/funders?beneficiary_country=GH&page_size=25",
                    cookies=login.cookies,
                )
                self.assertEqual(after_reset.status_code, 200)
                self.assertEqual(len(after_reset.json()["items"]), 1)
                self.assertEqual(after_reset.json()["items"][0]["profile_link"]["status"], "none")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
