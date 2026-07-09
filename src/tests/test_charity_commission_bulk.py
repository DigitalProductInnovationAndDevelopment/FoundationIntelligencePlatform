import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.dashboard.charity_commission import (
    charity_commission_bulk_summary,
    find_charity_commission_bulk_match,
    get_charity_commission_bulk_record,
    query_charity_commission_bulk,
)
from src.pipelines.build_charity_commission_index import iter_json_array


def create_test_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE charity_enrichment (
            organisation_number TEXT,
            registered_charity_number TEXT,
            linked_charity_number TEXT,
            charity_name TEXT,
            charity_type TEXT,
            charity_registration_status TEXT,
            charity_reporting_status TEXT,
            date_of_registration TEXT,
            date_of_removal TEXT,
            latest_acc_fin_period_start_date TEXT,
            latest_acc_fin_period_end_date TEXT,
            latest_income REAL,
            latest_expenditure REAL,
            charity_contact_address1 TEXT,
            charity_contact_address2 TEXT,
            charity_contact_address3 TEXT,
            charity_contact_address4 TEXT,
            charity_contact_address5 TEXT,
            charity_contact_postcode TEXT,
            charity_contact_phone TEXT,
            charity_contact_email TEXT,
            charity_contact_web TEXT,
            charity_company_registration_number TEXT,
            charity_activities TEXT,
            charity_gift_aid INTEGER,
            charity_has_land INTEGER,
            charity_insolvent INTEGER,
            charity_in_administration INTEGER,
            primary_purpose_grant_making INTEGER,
            expenditure_grants_institution REAL,
            expenditure_charitable_expenditure REAL,
            count_volunteers INTEGER,
            count_employees INTEGER,
            assets REAL,
            liabilities REAL,
            countries TEXT,
            regions TEXT,
            areas_of_operation TEXT,
            who_classifications TEXT,
            what_classifications TEXT,
            how_classifications TEXT,
            all_classifications TEXT,
            has_financial_history INTEGER,
            has_governing_document INTEGER,
            has_event_history INTEGER,
            has_published_report INTEGER
        );
        CREATE TABLE charity_area_of_operation (
            organisation_number TEXT,
            geographic_area_description TEXT
        );
        CREATE TABLE charity_annual_return_history (
            organisation_number TEXT,
            fin_period_end_date TEXT,
            total_gross_income REAL,
            total_gross_expenditure REAL
        );
        CREATE TABLE charity_annual_return_partb (
            organisation_number TEXT,
            fin_period_end_date TEXT,
            assets_total_assets_and_liabilities REAL,
            assets_total_liabilities REAL
        );
        CREATE TABLE charity_other_names (
            organisation_number TEXT,
            charity_name TEXT,
            charity_name_type TEXT
        );
        CREATE TABLE charity_other_regulators (
            organisation_number TEXT,
            regulator_name TEXT,
            regulator_web_url TEXT
        );
        CREATE TABLE charity_policy (organisation_number TEXT, policy_name TEXT);
        CREATE TABLE charity_published_report (
            organisation_number TEXT,
            report_name TEXT,
            report_location TEXT,
            date_published TEXT
        );
        CREATE TABLE charity_governing_document (
            organisation_number TEXT,
            governing_document_description TEXT,
            charitable_objects TEXT,
            area_of_benefit TEXT
        );
        CREATE TABLE charity_event_history (
            organisation_number TEXT,
            event_type TEXT,
            date_of_event TEXT,
            reason TEXT,
            assoc_charity_name TEXT
        );
        """
    )
    values = {
        "organisation_number": "9001",
        "registered_charity_number": "123456",
        "linked_charity_number": "0",
        "charity_name": "Official Example Foundation",
        "charity_type": "Trust",
        "charity_registration_status": "Registered",
        "charity_reporting_status": "Submission Received",
        "date_of_registration": "2020-01-01",
        "latest_acc_fin_period_end_date": "2025-03-31",
        "latest_income": 1_000_000,
        "latest_expenditure": 700_000,
        "charity_contact_address1": "1 Example Street",
        "charity_contact_postcode": "SW1A 1AA",
        "charity_contact_phone": "020 0000 0000",
        "charity_contact_email": "hello@example.test",
        "charity_contact_web": "https://example.test",
        "charity_activities": "Supports education",
        "primary_purpose_grant_making": 1,
        "expenditure_grants_institution": 400_000,
        "count_volunteers": 12,
        "count_employees": 3,
        "assets": 2_000_000,
        "liabilities": 100_000,
        "countries": '["Kenya"]',
        "regions": '["Throughout England"]',
        "areas_of_operation": '["Kenya","Throughout England"]',
        "who_classifications": '["Children"]',
        "what_classifications": '["Education/training"]',
        "how_classifications": '["Makes Grants To Organisations"]',
        "all_classifications": '["Children","Education/training"]',
        "has_financial_history": 1,
        "has_governing_document": 1,
        "has_event_history": 1,
        "has_published_report": 0,
    }
    columns = list(values)
    connection.execute(
        f"INSERT INTO charity_enrichment ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        [values[column] for column in columns],
    )
    connection.execute(
        "INSERT INTO charity_area_of_operation VALUES (?, ?)",
        ("9001", "Kenya"),
    )
    connection.executemany(
        "INSERT INTO charity_annual_return_history VALUES (?, ?, ?, ?)",
        [
            ("9001", "2025-03-31", 1_000_000, 700_000),
            ("9001", "2024-03-31", 900_000, 650_000),
        ],
    )
    connection.execute(
        "INSERT INTO charity_annual_return_partb VALUES (?, ?, ?, ?)",
        ("9001", "2025-03-31", 2_000_000, 100_000),
    )
    connection.execute(
        "INSERT INTO charity_other_names VALUES (?, ?, ?)",
        ("9001", "Example Foundation", "Working name"),
    )
    connection.execute(
        "INSERT INTO charity_policy VALUES (?, ?)",
        ("9001", "Safeguarding vulnerable beneficiaries"),
    )
    connection.execute(
        "INSERT INTO charity_governing_document VALUES (?, ?, ?, ?)",
        ("9001", "Trust deed", "Advance education", "International"),
    )
    connection.execute(
        "INSERT INTO charity_event_history VALUES (?, ?, ?, ?, ?)",
        ("9001", "Standard registration", "2020-01-01", None, None),
    )
    connection.commit()
    connection.close()


class TestCharityCommissionBulkData(unittest.TestCase):
    def test_streaming_array_parser_handles_small_chunks(self):
        source = io.StringIO('\ufeff[{"id":1},\n{"id":2},{"id":3}]')
        records = list(iter_json_array(source, chunk_size=5))
        self.assertEqual(records, [{"id": 1}, {"id": 2}, {"id": 3}])

    def test_summary_query_and_detail_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "register.sqlite3"
            create_test_database(database)
            summary = charity_commission_bulk_summary(database)
            records, total = query_charity_commission_bulk(
                status="Active",
                has_grant_maker_flag="Yes",
                geography="Kenya",
                search="123456",
                path=database,
            )
            detail = get_charity_commission_bulk_record("9001", database)

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["with_financial_history"], 1)
        self.assertEqual(total, 1)
        self.assertEqual(records[0]["charity_name"], "Official Example Foundation")
        self.assertTrue(records[0]["primary_purpose_grant_making"])
        self.assertEqual(detail["countries"], ["Kenya"])
        self.assertEqual(len(detail["financial_history"]), 2)
        self.assertEqual(detail["policies"], ["Safeguarding vulnerable beneficiaries"])

    def test_existing_organization_matches_by_registered_number(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "register.sqlite3"
            create_test_database(database)
            connection = sqlite3.connect(database)
            connection.execute(
                """
                INSERT INTO charity_enrichment (
                    organisation_number,
                    registered_charity_number,
                    linked_charity_number,
                    charity_name,
                    charity_registration_status
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("123456", "654321", "0", "Identifier Collision", "Registered"),
            )
            connection.commit()
            connection.close()
            match = find_charity_commission_bulk_match(
                {"funding_info": {"charity_number": "123456"}},
                database,
            )
        self.assertIsNotNone(match)
        self.assertEqual(match["organisation_number"], "9001")

    def test_internal_alphanumeric_identifier_does_not_create_false_match(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "register.sqlite3"
            create_test_database(database)
            match = find_charity_commission_bulk_match(
                {"funding_info": {"charity_number": "CUSTOM_123456"}},
                database,
            )
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
