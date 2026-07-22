import json
import os
import sqlite3
import tempfile
import unittest

from bff.repositories import SQLiteCharityRepository, _stable_party_id
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
            INSERT INTO charities (charity_id, name, type, raw_cc_data)
            VALUES (?, ?, 'Charity', ?)
            """,
            [
                (1, "Alpha Foundation", self._raw_charity(1, "Alpha Foundation")),
                (2, "Beta Charity", self._raw_charity(2, "Beta Charity")),
                (3, "Gamma Charity", self._raw_charity(3, "Gamma Charity")),
                (4, "Organization-level only", self._raw_charity(4, "Organization-level only")),
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
                amount, currency, description, date, beneficiary_geography,
                beneficiary_geography_normalized, source, source_record_id, source_url,
                ingestion_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    async def test_sankey_requires_currency_filter_and_reports_filtering(self):
        self._grant("GBP", amount=100, currency="GBP")
        self._grant("EUR", amount=200, currency="EUR")

        mixed = await self.repo.get_sankey_data(1)
        selected = await self.repo.get_sankey_data(1, currency="GBP")

        self.assertEqual(mixed["status"], "mixed_currency_requires_filter")
        self.assertEqual(mixed["links"], [])
        self.assertEqual(mixed["metadata"]["currencies"], ["EUR", "GBP"])
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

    async def test_map_uses_only_one_unambiguous_beneficiary_location(self):
        self._grant("KNOWN", amount=300, locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("MULTI", amount=200, locations=[
            {"name": "Ghana", "countryCode": "GH"},
            {"name": "Kenya", "countryCode": "KE"},
        ])
        self._grant("UNKNOWN", amount=100)

        result = await self.repo.get_grants_map(min_coverage=0.30)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["known_geography_count"], 1)
        self.assertEqual(result["unknown_geography_count"], 2)
        self.assertEqual(result["coverage_percentage"], 33.33)
        self.assertEqual(result["items"][0]["region_or_country_code"], "GH")
        self.assertEqual(result["items"][0]["total_amount"], 300.0)

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

        self.assertEqual(result["items"][0]["region_or_country_code"], "GLOBAL")
        self.assertEqual(result["items"][0]["region_or_country_name"], "Worldwide / global scope")

    async def test_map_requires_currency_filter_and_supports_no_data(self):
        empty = await self.repo.get_grants_map()
        self.assertEqual(empty["status"], "no_data")

        self._grant("GBP", currency="GBP", locations=[{"name": "Ghana", "countryCode": "GH"}])
        self._grant("EUR", currency="EUR", locations=[{"name": "Kenya", "countryCode": "KE"}])
        mixed = await self.repo.get_grants_map()
        selected = await self.repo.get_grants_map(currency="EUR")

        self.assertEqual(mixed["status"], "mixed_currency_requires_filter")
        self.assertEqual(selected["status"], "available")
        self.assertEqual(selected["items"][0]["currency"], "EUR")

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
