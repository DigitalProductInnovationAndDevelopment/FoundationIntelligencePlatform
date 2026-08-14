import json
import os
import sqlite3
import tempfile
import unittest

from bff.repositories import SQLiteCharityRepository, _stable_party_id
from bff.schemas import CharityDetail
from data.db_loader import create_tables
from preprocessing.enrichment import normalize_geography_sources


class TestGrantTransactions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "transactions.db")
        self.conn = sqlite3.connect(self.db_path)
        create_tables(self.conn)
        self.conn.executemany(
            """
            INSERT INTO charities (
                charity_id, name, type, raw_cc_data, headquarters_country,
                headquarters_region, annual_expenditure, programme_areas_source,
                programme_areas_inferred
            ) VALUES (?, ?, 'Charity', ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1, "Alpha Foundation", self._raw_charity(1, "Alpha Foundation"),
                    "United Kingdom", "London", 2_000_000,
                    json.dumps(["Health"]), json.dumps([]),
                ),
                (
                    2, "Beta Charity", self._raw_charity(2, "Beta Charity"),
                    "Ghana", None, 500_000, json.dumps([]), json.dumps([]),
                ),
                (
                    3, "Gamma Charity", self._raw_charity(3, "Gamma Charity"),
                    "Kenya", None, 100_000, json.dumps([]), json.dumps([]),
                ),
                (
                    4, "Organization-level only",
                    self._raw_charity(4, "Organization-level only"),
                    None, None, None, json.dumps([]), json.dumps([]),
                ),
            ],
        )
        self.conn.commit()
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _raw_charity(charity_id, name):
        return json.dumps({
            "registered_charity_number": charity_id,
            "suffix": 0,
            "all_details": {
                "organisation_number": charity_id,
                "reg_charity_number": charity_id,
                "group_subsid_suffix": 0,
                "charity_name": name,
                "reg_status": "R",
            },
            "assets_liabilities": [],
            "financial_history": [],
        })

    def _grant(
        self,
        grant_id,
        donor_id=1,
        donor_name="Alpha Foundation",
        recipient_id=2,
        recipient_name="Beta Charity",
        amount=100.0,
        currency="GBP",
        locations=None,
        donor_source_id=None,
        recipient_source_id=None,
    ):
        self.conn.execute(
            """
            INSERT INTO grants (
                grant_id, funding_charity_id, funding_name, funding_org_source_id,
                recipient_name, recipient_charity_id, recipient_org_source_id,
                amount, amount_eur, conversion_status, currency, description, date, beneficiary_geography,
                beneficiary_geography_normalized, source, source_record_id, source_url,
                ingestion_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant_id,
                donor_id,
                donor_name,
                donor_source_id,
                recipient_name,
                recipient_id,
                recipient_source_id,
                amount,
                amount,
                "native_eur" if currency == "EUR" and amount is not None else "ecb_award_date" if amount is not None else "invalid_source_amount",
                currency,
                f"Description for {grant_id}",
                "2025-01-01",
                json.dumps(locations or []),
                json.dumps(normalize_geography_sources(locations or [], "beneficiary_geography")[0]),
                "360Giving",
                grant_id,
                f"https://example.test/grants/{grant_id}",
                "2026-01-01T00:00:00Z",
            ),
        )
        self.conn.commit()

    async def test_grants_made_received_provenance_and_no_transactions(self):
        self._grant("G1")

        made = await self.repo.get_grants_for_charity(1, "funder")
        received = await self.repo.get_grants_for_charity(2, "recipient")
        none = await self.repo.get_grants_for_charity(4, "all")

        self.assertEqual(made["grant_count"], 1)
        self.assertEqual(received["grants"][0]["amount"], 100.0)
        self.assertEqual(received["grants"][0]["currency"], "GBP")
        self.assertEqual(received["grants"][0]["source"], "360Giving")
        self.assertEqual(received["grants"][0]["source_record_id"], "G1")
        self.assertEqual(received["grants"][0]["source_url"], "https://example.test/grants/G1")
        self.assertEqual(none["status"], "no_transactions_found")
        self.assertEqual(none["transaction_coverage"], "no_transactions_found")

    async def test_sankey_aggregates_repeated_relationships(self):
        self._grant("G1", amount=100)
        self._grant("G2", amount=250)

        result = await self.repo.get_sankey_data(1)

        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["links"]), 1)
        self.assertEqual(result["links"][0]["value"], 350.0)
        self.assertEqual(result["links"][0]["grant_count"], 2)
        self.assertEqual(result["metadata"]["included_grant_count"], 2)

    async def test_sankey_excludes_missing_zero_and_self_link_amounts(self):
        self._grant("MISSING", amount=None)
        self._grant("ZERO", amount=0)
        self._grant("SELF", recipient_id=1, recipient_name="Alpha Foundation", amount=50)

        result = await self.repo.get_sankey_data(1)

        self.assertEqual(result["status"], "no_transactions_found")
        self.assertEqual(result["metadata"]["excluded_grant_count"], 3)
        self.assertEqual(result["metadata"]["excluded_reasons"], {
            "missing_amount": 1,
            "non_positive_amount": 1,
            "self_link": 1,
        })

    async def test_sankey_auto_uses_eur_and_raw_currency_filter_remains_available(self):
        self._grant("GBP", amount=100, currency="GBP")
        self._grant("EUR", amount=200, currency="EUR")

        mixed = await self.repo.get_sankey_data(1)
        selected = await self.repo.get_sankey_data(1, currency="GBP")

        self.assertEqual(mixed["status"], "available")
        self.assertEqual(mixed["links"][0]["value"], 300.0)
        self.assertEqual(mixed["metadata"]["currencies"], ["EUR", "GBP"])
        self.assertEqual(mixed["metadata"]["selected_currency"], "EUR")
        self.assertEqual(selected["links"][0]["value"], 100.0)
        self.assertEqual(selected["metadata"]["excluded_reasons"]["currency_filtered"], 1)

    async def test_sankey_fallback_node_ids_are_stable_and_role_namespaced(self):
        self._grant(
            "NO-IDS",
            donor_id=None,
            donor_name="",
            recipient_id=None,
            recipient_name="",
            donor_source_id=None,
            recipient_source_id=None,
        )

        # Source-only organizations cannot be queried by a local numeric charity ID,
        # but their namespaced fallback identities remain deterministic.
        first = await self.repo.get_sankey_data(1)
        self.assertEqual(first["status"], "no_transactions_found")
        self.assertEqual(
            _stable_party_id("recipient", None, None, "", ""),
            _stable_party_id("recipient", None, None, "", ""),
        )
        self.assertNotEqual(
            _stable_party_id("recipient", None, None, "Same name", ""),
            _stable_party_id("donor", None, None, "Same name", ""),
        )

        # Query the unlinked record directly through the deterministic helper path by
        # linking its donor to the selected organization while leaving the recipient unnamed.
        self._grant("ONE-ID", recipient_id=None, recipient_name="")
        result = await self.repo.get_sankey_data(1)
        recipient_node = next(node for node in result["nodes"] if node["role"] == "recipient")
        again = await self.repo.get_sankey_data(1)
        self.assertEqual(recipient_node["id"], next(
            node["id"] for node in again["nodes"] if node["role"] == "recipient"
        ))
        self.assertTrue(recipient_node["id"].startswith("360giving:recipient:fallback:"))

    async def test_sankey_truncates_links_and_reports_excluded_records(self):
        self._grant("G1", recipient_id=2, recipient_name="Beta Charity", amount=300)
        self._grant("G2", recipient_id=3, recipient_name="Gamma Charity", amount=200)

        result = await self.repo.get_sankey_data(1, limit=1)

        self.assertTrue(result["metadata"]["truncation_applied"])
        self.assertEqual(len(result["links"]), 1)
        self.assertEqual(result["metadata"]["excluded_reasons"]["truncated"], 1)
        self.assertEqual(result["metadata"]["included_grant_count"], 1)

    async def test_map_counts_multi_country_associations_without_allocating_amount(self):
        self._grant("KNOWN", amount=300, locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("MULTI", amount=200, locations=[
            {"name": "Ghana", "countryCode": "GH"},
            {"name": "Kenya", "countryCode": "KE"},
        ])
        self._grant("UNKNOWN", amount=100)

        result = await self.repo.get_grants_map(min_coverage=0.30)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["known_geography_count"], 2)
        self.assertEqual(result["unknown_geography_count"], 1)
        self.assertEqual(result["coverage_percentage"], 66.67)
        self.assertEqual(result["grant_country_association_count"], 3)
        self.assertEqual(result["multi_country_grant_count"], 1)
        self.assertEqual(result["funding_excluded_multi_country_count"], 1)
        self.assertEqual(result["funding_excluded_multi_country_amount"], 200.0)
        by_code = {item["region_or_country_code"]: item for item in result["items"]}
        self.assertEqual(by_code["GH"]["grant_count"], 2)
        self.assertEqual(by_code["GH"]["total_amount"], 300.0)
        self.assertEqual(by_code["KE"]["grant_count"], 1)
        self.assertIsNone(by_code["KE"]["total_amount"])

    async def test_map_withholds_aggregation_below_configured_coverage(self):
        self._grant("KNOWN", locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("UNKNOWN")

        result = await self.repo.get_grants_map(min_coverage=0.75)

        self.assertEqual(result["status"], "low_coverage")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["coverage_percentage"], 50.0)

    async def test_map_normalizes_explicit_global_scope(self):
        self._grant("GLOBAL", locations=[{"name": "Worldwide"}])

        result = await self.repo.get_grants_map()

        self.assertEqual(result["status"], "no_geography")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["known_geography_count"], 0)
        self.assertEqual(result["unknown_geography_count"], 1)

    async def test_map_keeps_counts_currency_neutral_and_separates_funding(self):
        empty = await self.repo.get_grants_map()
        self.assertEqual(empty["status"], "no_data")

        self._grant("GBP", currency="GBP", locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("EUR", currency="EUR", locations=[{"name": "Kenya", "countryCode": "KE"}])
        mixed = await self.repo.get_grants_map()
        selected = await self.repo.get_grants_map(currency="EUR")

        self.assertEqual(mixed["status"], "available")
        self.assertEqual(mixed["funding_status"], "currency_selection_required")
        self.assertFalse(mixed["funding_mode_available"])
        self.assertEqual(sum(item["grant_count"] for item in mixed["items"]), 2)
        self.assertTrue(all(item["total_amount"] is None for item in mixed["items"]))
        self.assertEqual(selected["status"], "available")
        self.assertTrue(selected["funding_mode_available"])
        by_code = {item["region_or_country_code"]: item for item in selected["items"]}
        self.assertEqual(by_code["KE"]["currency"], "EUR")
        self.assertEqual(by_code["KE"]["total_amount"], 100.0)
        self.assertIsNone(by_code["GH"]["total_amount"])
        self.assertEqual(selected["funding_excluded_currency_count"], 1)

    async def test_map_rolls_up_uk_constituents_and_preserves_source_label(self):
        self._grant(
            "ENGLAND",
            locations=[{"name": "England", "geoCode": "E92000001", "geoCodeType": "CTRY"}],
        )

        result = await self.repo.get_grants_map()

        self.assertEqual(result["known_geography_count"], 1)
        self.assertEqual(result["items"][0]["region_or_country_code"], "GB")
        self.assertEqual(result["items"][0]["region_or_country_name"], "United Kingdom")
        self.assertIn("England", result["items"][0]["original_geographies"])

    async def test_map_supports_alpha_three_codes_and_rejects_invalid_country_codes(self):
        self._grant("ALPHA3", locations=[{"name": "Zambia", "countryCode": "ZMB"}])
        self._grant("INVALID", locations=[{"name": "Neverland", "countryCode": "ZZ"}])

        result = await self.repo.get_grants_map()

        self.assertEqual(result["known_geography_count"], 1)
        self.assertEqual(result["unknown_geography_count"], 1)
        self.assertEqual(result["items"][0]["region_or_country_code"], "ZM")
        self.assertIn("Zambia", result["items"][0]["original_geographies"])

    async def test_map_excludes_missing_and_negative_amounts_but_keeps_counts(self):
        self._grant("MISSING", amount=None, locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("NEGATIVE", amount=-20, locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("ZERO", amount=0, locations=[{"name": "Ghana", "countryCode": "GH"}])

        result = await self.repo.get_grants_map()

        self.assertEqual(result["items"][0]["grant_count"], 3)
        self.assertEqual(result["items"][0]["funding_grant_count"], 1)
        self.assertEqual(result["items"][0]["total_amount"], 0.0)
        self.assertEqual(result["funding_excluded_invalid_amount_count"], 2)

    async def test_map_filters_funders_and_returns_disclosed_hq_connections(self):
        self._grant(
            "ALPHA-GH",
            donor_id=1,
            donor_name="Alpha Foundation",
            amount=2_000,
            locations=[{"name": "Ghana", "countryCode": "GH"}],
        )
        self._grant(
            "BETA-KE",
            donor_id=2,
            donor_name="Beta Charity",
            amount=100,
            locations=[{"name": "Kenya", "countryCode": "KE"}],
        )

        result = await self.repo.get_grants_map(
            search="Alpha",
            tags=["Health"],
            foundation_regions=["United Kingdom"],
            funding_regions=["Ghana"],
            min_annual_giving=1_000_000,
            min_avg_grant_size=1_000,
        )

        self.assertEqual(result["known_geography_count"], 1)
        self.assertEqual(result["items"][0]["region_or_country_code"], "GH")
        self.assertEqual(result["connection_grant_count"], 1)
        self.assertEqual(len(result["connections"]), 1)
        self.assertEqual(result["connections"][0]["origin_country_code"], "GB")
        self.assertEqual(result["connections"][0]["destination_country_code"], "GH")
        self.assertIn(
            "Organization-directory registered location",
            result["connections"][0]["origin_sources"],
        )

    async def test_map_reports_no_data_when_organization_search_has_no_match(self):
        self._grant("KNOWN", locations=[{"name": "Ghana", "countryCode": "GH"}])

        result = await self.repo.get_grants_map(search="Missing funder")

        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["known_geography_count"], 0)
        self.assertEqual(result["connections"], [])

    async def test_directory_search_matches_observed_funder_alias(self):
        self._grant(
            "ALIAS",
            donor_id=1,
            donor_name="Alpha Public Funder Brand",
            locations=[{"name": "Ghana", "countryCode": "GH"}],
        )

        result = await self.repo.get_all(search="Public Funder Brand")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["registered_charity_number"], 1)

    async def test_directory_grant_size_filter_uses_ecb_eur_values_across_source_currencies(self):
        self._grant("GBP", donor_id=1, donor_name="Alpha Foundation", amount=100, currency="GBP")
        self._grant("USD", donor_id=1, donor_name="Alpha Foundation", amount=200, currency="USD")
        self.conn.execute(
            "UPDATE grants SET amount_eur = ? WHERE grant_id = ?",
            (150.0, "USD"),
        )
        self.conn.commit()

        result = await self.repo.get_all(
            min_avg_grant_size=120,
            max_avg_grant_size=130,
            sort="name_asc",
            limit=50,
        )

        self.assertEqual([item["registered_charity_number"] for item in result], [1])

    async def test_directory_geography_matches_map_iso_fallback_and_uk_rollup(self):
        self._grant(
            "RAW-MALAWI",
            donor_id=1,
            donor_name="Alpha Foundation",
            locations=[{"name": "Malawi", "countryCode": "MW"}],
        )
        self._grant(
            "RAW-ENGLAND",
            donor_id=2,
            donor_name="Beta Charity",
            locations=[{
                "name": "England",
                "countryCode": "GB",
                "geoCode": "E92000001",
                "geoCodeType": "CTRY",
            }],
        )

        malawi = await self.repo.get_all(funding_regions=["Malawi"])
        united_kingdom = await self.repo.get_all(
            funding_regions=["United Kingdom"]
        )

        self.assertEqual(
            [item["registered_charity_number"] for item in malawi], [1]
        )
        self.assertEqual(
            [item["registered_charity_number"] for item in united_kingdom], [2]
        )

    async def test_directory_geography_stays_empty_without_linked_funder(self):
        self._grant(
            "SOURCE-ONLY",
            donor_id=None,
            donor_name="Source-only funder",
            locations=[{"name": "Libya", "countryCode": "LY"}],
        )

        result = await self.repo.get_all(funding_regions=["Libya"])

        self.assertEqual(result, [])

    async def test_partial_directory_profile_has_schema_valid_detail(self):
        self.conn.execute("""
            UPDATE charities
            SET raw_cc_data = '{}', website = 'https://partial.example',
                email = 'hello@partial.example', address = '1 Example Street',
                annual_income = 125000, annual_expenditure = 100000
            WHERE charity_id = 4
        """)
        self.conn.commit()

        listing = await self.repo.get_all(search="Organization-level only")
        detail = await self.repo.get_by_id(4)
        validated = CharityDetail.model_validate(detail)

        self.assertEqual(listing[0]["reg_status"], "UNKNOWN")
        self.assertEqual(validated.registered_charity_number, 4)
        self.assertEqual(validated.all_details.charity_name, "Organization-level only")
        self.assertEqual(validated.all_details.reg_status, "UNKNOWN")
        self.assertEqual(validated.all_details.web, "https://partial.example")
        self.assertEqual(validated.all_details.latest_expenditure, 100000)
        self.assertEqual(validated.financial_history, [])

    async def test_network_summary_is_currency_separated(self):
        self._grant("GBP", amount=100, currency="GBP")
        self._grant("EUR", amount=200, currency="EUR")

        result = await self.repo.get_grant_summary()

        self.assertEqual(result["total_grant_count"], 2)
        self.assertEqual(result["currencies"], ["EUR", "GBP"])
        self.assertEqual({item["currency"] for item in result["largest_donors"]}, {"EUR", "GBP"})
        self.assertIn("do not combine", result["metadata"]["limitations"][0])


if __name__ == "__main__":
    unittest.main()
