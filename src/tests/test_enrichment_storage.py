import json
import os
import tempfile
import unittest

from bff.repositories import SQLiteCharityRepository
from data.db_loader import create_connection, create_tables, insert_charities, insert_grants
from preprocessing.enrichment import RULE_VERSION, enrich_grant, enrich_organization


class TestEnrichmentStorage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "enrichment.db")
        self.conn = create_connection(self.db_path)
        create_tables(self.conn)

        raw = {
            "registered_charity_number": 11,
            "suffix": 0,
            "all_details": {
                "organisation_number": 11,
                "reg_charity_number": 11,
                "group_subsid_suffix": 0,
                "charity_name": "Source Foundation",
                "reg_status": "R",
                "who_what_where": [{"classification_desc": "Education/training"}],
                "CharityAoOCountryContinent": [{"name": "Ghana"}],
            },
            "who_what_how": [],
            "assets_liabilities": [],
            "financial_history": [],
        }
        enrichment = enrich_organization({
            "country": "United Kingdom",
            "state": "London",
            "raw_cc_data": raw,
        })
        insert_charities(self.conn, [{
            "charity_id": 11,
            "name": "Source Foundation",
            "type": "Charity",
            "country": "United Kingdom",
            "state": "London",
            "raw_cc_data": raw,
            **enrichment,
        }])

        raw_grant = {"id": "SOURCE-GRANT", "description": "Health support in Ghana"}
        base_grant = {
            "grant_id": "SOURCE-GRANT",
            "funding_charity_id": 11,
            "funding_name": "Source Foundation",
            "recipient_name": "Recipient",
            "amount": 500,
            "currency": "GBP",
            "description": "Health support in Ghana",
            "programme_area_source": json.dumps(["Health"]),
            "beneficiary_geography": json.dumps([{"name": "Ghana", "countryCode": "GH"}]),
            "source": "360Giving",
            "source_record_id": "SOURCE-GRANT",
            "source_url": "https://example.test/SOURCE-GRANT",
            "raw_grant_data": raw_grant,
        }
        insert_grants(self.conn, [{**base_grant, **enrich_grant(base_grant)}])
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    async def test_programme_filter_uses_normalized_columns(self):
        results = await self.repo.get_all(tags=["Education"])
        self.assertEqual([item["registered_charity_number"] for item in results], [11])
        self.assertEqual(results[0]["programme_areas_source"], ["Education"])

    async def test_headquarters_filter_is_distinct_from_beneficiary_filter(self):
        headquarters = await self.repo.get_all(foundation_regions=["United Kingdom"])
        beneficiary = await self.repo.get_all(funding_regions=["Ghana"])
        wrong_headquarters = await self.repo.get_all(foundation_regions=["Ghana"])
        self.assertEqual(len(headquarters), 1)
        self.assertEqual(len(beneficiary), 1)
        self.assertEqual(wrong_headquarters, [])

    async def test_detail_returns_evidence_version_and_preserves_raw_source(self):
        detail = await self.repo.get_by_id(11)
        self.assertEqual(detail["programme_areas_source"], ["Education"])
        self.assertEqual(detail["headquarters_country"], "United Kingdom")
        self.assertEqual(detail["enrichment_rule_version"], RULE_VERSION)
        self.assertEqual(
            detail["all_details"]["who_what_where"][0]["classification_desc"],
            "Education/training",
        )
        self.assertTrue(detail["programme_area_evidence"])

    async def test_grant_returns_normalized_beneficiary_and_raw_provenance(self):
        response = await self.repo.get_grants_for_charity(11, "funder")
        grant = response["grants"][0]
        self.assertEqual(grant["beneficiary_geography_normalized"][0]["code"], "GH")
        self.assertEqual(grant["source_record_id"], "SOURCE-GRANT")
        self.assertEqual(grant["programme_area_source"], ["Health"])
        self.assertEqual(grant["enrichment_rule_version"], RULE_VERSION)


if __name__ == "__main__":
    unittest.main()
