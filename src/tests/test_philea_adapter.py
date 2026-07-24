import os
import tempfile
import unittest

from bff.repositories import SQLiteCharityRepository
from data.db_loader import create_connection, create_tables, insert_charities
from preprocessing.philea_adapter import (
    FUZZY_AUTO_MERGE_THRESHOLD,
    integrate_philea_organizations,
    map_philea_organization_type,
    normalize_domain,
    normalize_organization_name,
    normalize_philea_record,
)


def philea_member(
    record_id=10,
    name="Example Foundation",
    website="https://www.example.org/about",
    country="Germany",
    type_value="foundation",
):
    return {
        "id": record_id,
        "name": name,
        "website": website,
        "link": f"https://philea.eu/members/{record_id}/",
        "type": {"value": type_value, "label": "Source label"},
        "position": {"country": country, "city": "Berlin", "state": "Berlin"},
        "philea_info": {"About": "We support education and climate programmes."},
    }


def existing_organization(name="Existing Foundation", website="https://existing.org", country="Germany"):
    return {
        "charity_id": 123456,
        "name": name,
        "type": "Charity",
        "website": website,
        "country": country,
        "raw_cc_data": {
            "registered_charity_number": 123456,
            "link": "https://register.example/123456",
            "all_details": {"charity_name": name, "reg_status": "R"},
        },
    }


class TestPhileaAdapter(unittest.TestCase):
    def test_exact_domain_match_preserves_both_source_records(self):
        existing = existing_organization(website="https://www.example.org/home")
        organizations, report = integrate_philea_organizations(
            [existing], [philea_member()], "2026-01-01T00:00:00Z"
        )
        self.assertEqual(len(organizations), 1)
        self.assertEqual(report["match_methods"], {"exact_domain": 1})
        self.assertEqual(set(organizations[0]["source_names"]), {
            "Charity Commission for England and Wales", "Philea"
        })
        self.assertEqual(len(organizations[0]["source_records"]), 2)
        self.assertEqual(organizations[0]["organization_type"], "Charity")

    def test_exact_normalized_name_match_without_website(self):
        organizations, report = integrate_philea_organizations(
            [existing_organization(name="The Example Foundation", website="")],
            [philea_member(name="Example Foundation", website="")],
        )
        self.assertEqual(len(organizations), 1)
        self.assertEqual(report["match_methods"], {"exact_normalized_name": 1})

    def test_clear_non_match_is_added_with_source_type(self):
        organizations, report = integrate_philea_organizations(
            [existing_organization()],
            [philea_member(name="Completely Different Network", type_value="affiliate")],
        )
        self.assertEqual(len(organizations), 2)
        self.assertEqual(report["philea_added_count"], 1)
        added = next(item for item in organizations if item["primary_source"] == "Philea")
        self.assertEqual(added["organization_type"], "philanthropy infrastructure organization")
        self.assertEqual(added["transaction_coverage"], "organization_level_only")

    def test_ambiguous_fuzzy_match_is_not_merged_without_country_support(self):
        existing = existing_organization(name="Alpha Community Foundation", country="France")
        member = philea_member(name="Alfa Community Foundation", country="Germany", website="")
        organizations, report = integrate_philea_organizations([existing], [member])
        self.assertEqual(len(organizations), 2)
        self.assertEqual(report["ambiguous_candidate_count"], 1)
        added = next(item for item in organizations if item["primary_source"] == "Philea")
        self.assertEqual(added["deduplication_status"], "review_required")
        self.assertGreaterEqual(added["deduplication_candidates"][0]["name_similarity"], 0.82)

    def test_supported_high_confidence_fuzzy_match_uses_explicit_threshold(self):
        existing = existing_organization(name="Alpha Community Foundation", country="Germany")
        member = philea_member(name="Alfa Community Foundation", country="Germany", website="")
        organizations, report = integrate_philea_organizations([existing], [member])
        self.assertEqual(len(organizations), 1)
        self.assertEqual(report["fuzzy_auto_merge_threshold"], FUZZY_AUTO_MERGE_THRESHOLD)
        self.assertEqual(report["match_methods"], {"conservative_fuzzy_name_and_country": 1})

    def test_weak_fuzzy_match_remains_separate(self):
        organizations, report = integrate_philea_organizations(
            [existing_organization(name="Oak Trust")],
            [philea_member(name="River Philanthropy", website="")],
        )
        self.assertEqual(len(organizations), 2)
        self.assertEqual(report["philea_merged_count"], 0)

    def test_similar_records_from_same_source_are_not_merged(self):
        organizations, report = integrate_philea_organizations([], [
            philea_member(record_id=1, name="Alpha Foundation", website=""),
            philea_member(record_id=2, name="Alfa Foundation", website=""),
        ])
        self.assertEqual(len(organizations), 2)
        self.assertEqual(report["philea_merged_count"], 0)

    def test_organization_without_website_and_numeric_name_handling(self):
        record = normalize_philea_record(philea_member(website=""))
        self.assertEqual(record["normalized_domain"], "")
        with self.assertRaisesRegex(ValueError, "non-numeric"):
            normalize_philea_record(philea_member(name="136193034"))
        self.assertEqual(normalize_organization_name("136193034"), "")

    def test_domain_and_type_normalization(self):
        self.assertEqual(normalize_domain("HTTP://WWW.Example.org/path"), "example.org")
        self.assertEqual(map_philea_organization_type({"value": "foundation"}), "foundation")
        self.assertEqual(map_philea_organization_type({"value": "member"}), "membership organization")
        self.assertEqual(map_philea_organization_type({"value": "affiliate"}), "philanthropy infrastructure organization")


class TestPhileaStorage(unittest.IsolatedAsyncioTestCase):
    async def test_philea_record_has_no_fake_grants(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "philea.db")
            connection = create_connection(db_path)
            create_tables(connection)
            record = normalize_philea_record(philea_member(record_id=777))
            insert_charities(connection, [record])
            connection.close()

            repo = SQLiteCharityRepository(db_path)
            directory_records = await repo.get_all(search="Example Foundation")
            grants = await repo.get_grants_for_charity(-777)
            sankey = await repo.get_sankey_data(-777)

            self.assertEqual(len(directory_records), 1)
            self.assertEqual(directory_records[0]["primary_source"], "Philea")
            self.assertEqual(directory_records[0]["organization_type"], "foundation")
            self.assertEqual(grants["status"], "organization_level_only")
            self.assertEqual(grants["grants"], [])
            self.assertEqual(sankey["status"], "organization_level_only")
            self.assertEqual(sankey["links"], [])


if __name__ == "__main__":
    unittest.main()
