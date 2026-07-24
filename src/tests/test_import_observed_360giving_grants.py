import json
import os
import tempfile
import unittest

from data.db_loader import create_connection, create_tables, insert_grants
from pipelines.import_observed_360giving_grants import (
    append_new_grants,
    prepare_grants,
    select_random_new_grants,
)


def source_grant(identifier, country="DE"):
    return {
        "grant_id": identifier,
        "publisher": {"self": "https://api.example.test/publisher"},
        "data": {
            "id": identifier,
            "title": "Digital skills grant",
            "description": "Digital skills and software access.",
            "amountAwarded": 1200,
            "currency": "EUR",
            "awardDate": "2025-01-02",
            "beneficiaryLocation": [{"name": "Germany", "countryCode": country}],
            "fundingOrganization": [{"id": "GB-CHC-1", "name": "Observed funder"}],
            "recipientOrganization": [{"id": "recipient-1", "name": "Observed recipient"}],
        },
    }


class TestObservedGrantImport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, "test.db")
        self.connection = create_connection(self.database_path)
        create_tables(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_prepare_grants_freezes_publisher_prefix_and_deduplicates(self):
        payload = {"records": [
            {"org_id": "one", "summary": {}, "grants_made": [source_grant("one"), source_grant("one")]},
            {"org_id": "two", "summary": {}, "grants_made": [source_grant("two", "AT")]},
        ]}
        grants, raw_count = prepare_grants(payload, publisher_record_limit=1)
        self.assertEqual(raw_count, 2)
        self.assertEqual([grant["grant_id"] for grant in grants], ["one"])
        self.assertEqual(grants[0]["amount_eur"], 1200)
        self.assertEqual(grants[0]["beneficiary_geography_normalized"][0]["code"], "DE")

    def test_existing_grants_are_preserved_and_only_new_ids_are_inserted(self):
        existing = {
            "grant_id": "same",
            "funding_charity_id": None,
            "recipient_name": "Original recipient",
            "description": "Original description",
        }
        insert_grants(self.connection, [existing])
        grants, _ = prepare_grants({"records": [{
            "org_id": "publisher", "summary": {},
            "grants_made": [source_grant("same"), source_grant("new")],
        }]})
        result = append_new_grants(self.connection, grants)
        self.assertEqual(result, {"already_present": 1, "inserted": 1})
        rows = self.connection.execute(
            "SELECT grant_id, recipient_name, description FROM grants ORDER BY grant_id"
        ).fetchall()
        self.assertEqual(rows, [("new", "Observed recipient", "Digital skills and software access."), ("same", "Original recipient", "Original description")])

    def test_exact_random_selection_excludes_existing_ids_and_is_reproducible(self):
        grants, _ = prepare_grants({"records": [{
            "org_id": "publisher", "summary": {},
            "grants_made": [
                source_grant("existing"), source_grant("one"), source_grant("two"), source_grant("three"),
            ],
        }]})
        insert_grants(self.connection, [next(grant for grant in grants if grant["grant_id"] == "existing")])

        first, metadata = select_random_new_grants(self.connection, grants, 2, seed=42)
        second, _ = select_random_new_grants(self.connection, grants, 2, seed=42)

        self.assertEqual(metadata, {"eligible_new_grants": 3, "already_present_in_input": 1})
        self.assertEqual([item["grant_id"] for item in first], [item["grant_id"] for item in second])
        self.assertEqual(len(first), 2)
        self.assertNotIn("existing", [item["grant_id"] for item in first])


if __name__ == "__main__":
    unittest.main()
