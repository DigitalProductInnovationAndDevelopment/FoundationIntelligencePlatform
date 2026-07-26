import os
import sqlite3
import sys
import tempfile
import unittest


SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from bff import charity as charity_router
from data.db_loader import create_connection, create_tables
from data.registry import migrate_registry_schema


class FastConfirmedProfileLinkTests(unittest.TestCase):
    """The interactive profile action must remain local and exact."""

    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = handle.name
        handle.close()
        self.original_db_path = charity_router.DB_PATH
        charity_router.DB_PATH = self.db_path

        conn = create_connection(self.db_path)
        try:
            create_tables(conn)
            migrate_registry_schema(conn)
            conn.execute(
                """
                INSERT INTO charity_registry_organizations (
                    registry_id, charity_number, registered_name, normalized_name,
                    registration_status, income, expenditure, city,
                    administrative_region, country_code, activity_text,
                    source_name, imported_at, is_current_source_record
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    "cc:208925", "208925", "Foothold Test Trust", "foothold test trust",
                    "Registered", 120000.0, 90000.0, "London", "London", "GB",
                    "Digital skills and open data programmes.",
                    "Charity Commission for England and Wales", "2026-07-26T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO grants (
                    grant_id, funding_name, funding_org_source_id, recipient_name,
                    source, currency, amount, amount_eur, conversion_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "grant-1", "Foothold Test Trust", "GB-CHC-208925", "Recipient",
                    "360Giving", "GBP", 100.0, 116.0, "ecb_award_date",
                ),
            )
            conn.execute(
                """
                INSERT INTO grant_source_funder_facts (
                    grant_id, country_code, country_name, source_namespace,
                    source_funder_key, identity_method, source_organization_id,
                    display_name, recipient_key, recipient_name, currency,
                    original_amount_status, eur_amount_status, country_count,
                    data_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "grant-1", "DE", "Germany", "360Giving", "source:GB-CHC-208925",
                    "source_id", "GB-CHC-208925", "Foothold Test Trust", "recipient:1",
                    "Recipient", "GBP", "valid", "valid", 1, "test",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        charity_router.DB_PATH = self.original_db_path
        os.unlink(self.db_path)

    def test_creates_profile_and_links_only_exact_source_identifier(self):
        profiles = charity_router._fast_link_confirmed_profiles([208925])

        self.assertEqual(profiles[0]["charity_id"], 208925)
        self.assertEqual(profiles[0]["name"], "Foothold Test Trust")
        self.assertEqual(profiles[0]["linked_grants"], 1)

        conn = sqlite3.connect(self.db_path)
        try:
            profile = conn.execute(
                "SELECT name, transaction_coverage FROM charities WHERE charity_id = 208925"
            ).fetchone()
            self.assertEqual(profile, ("Foothold Test Trust", "observed_grants_linked"))
            self.assertEqual(
                conn.execute(
                    "SELECT funding_charity_id FROM grants WHERE grant_id = 'grant-1'"
                ).fetchone()[0],
                208925,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT linked_profile_id FROM grant_source_funder_facts WHERE grant_id = 'grant-1'"
                ).fetchone()[0],
                208925,
            )
            self.assertEqual(
                conn.execute(
                    """
                    SELECT match_method FROM organization_registry_links
                    WHERE registry_id = 'cc:208925' AND enriched_organization_id = 208925
                    """
                ).fetchone()[0],
                "exact_identifier",
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
