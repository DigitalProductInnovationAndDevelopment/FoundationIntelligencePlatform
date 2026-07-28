import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from data import db_loader
from pipelines import run_pipeline as pipeline_module


def sample_charity(charity_id=123456, name="Database Test Foundation"):
    return {
        "charity_id": charity_id,
        "name": name,
        "type": "Charity",
        "website": "",
        "email": "",
        "address": "",
        "city": "",
        "state": "",
        "country": "United Kingdom",
        "latitude": None,
        "longitude": None,
        "annual_income": None,
        "annual_expenditure": None,
        "thematic_focus": "[]",
        "geographic_focus": "{}",
        "raw_cc_data": {
            "registered_charity_number": charity_id,
            "suffix": 0,
            "all_details": {
                "organisation_number": charity_id,
                "reg_charity_number": charity_id,
                "group_subsid_suffix": 0,
                "charity_name": name,
                "reg_status": "R",
            },
        },
    }


class TestDatabaseValidation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def path(self, name):
        return os.path.join(self.temp_dir.name, name)

    def test_missing_database_is_invalid(self):
        valid, reason = db_loader.validate_database(self.path("missing.db"))
        self.assertFalse(valid)
        self.assertIn("does not exist", reason)

    def test_zero_byte_database_is_invalid(self):
        db_path = self.path("empty.db")
        open(db_path, "wb").close()
        valid, reason = db_loader.validate_database(db_path)
        self.assertFalse(valid)
        self.assertIn("empty", reason)

    def test_corrupt_database_is_invalid(self):
        db_path = self.path("corrupt.db")
        with open(db_path, "wb") as handle:
            handle.write(b"not a sqlite database")
        valid, reason = db_loader.validate_database(db_path)
        self.assertFalse(valid)
        self.assertIn("SQLite", reason)

    def test_database_missing_required_tables_is_invalid(self):
        db_path = self.path("partial.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.commit()
        conn.close()
        valid, reason = db_loader.validate_database(db_path)
        self.assertFalse(valid)
        self.assertIn("missing required tables", reason)

    def test_valid_database_is_accepted(self):
        db_path = self.path("valid.db")
        conn = sqlite3.connect(db_path)
        db_loader.create_tables(conn)
        conn.close()
        self.assertEqual(db_loader.validate_database(db_path), (True, "valid"))

    def test_overview_migration_replaces_only_incompatible_derived_table(self):
        db_path = self.path("overview-upgrade.db")
        conn = sqlite3.connect(db_path)
        db_loader.create_tables(conn)
        conn.execute("DROP TABLE grant_overview_facts")
        conn.execute(
            "CREATE TABLE grant_overview_facts (grant_id TEXT PRIMARY KEY, source_namespace TEXT)"
        )
        conn.execute(
            "INSERT INTO grant_overview_facts (grant_id, source_namespace) VALUES ('stale', 'old')"
        )

        db_loader.migrate_grant_overview_schema(conn)

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(grant_overview_facts)")
        }
        self.assertTrue(
            db_loader.REQUIRED_SCHEMA["grant_overview_facts"].issubset(columns)
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM grant_overview_facts").fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0],
            0,
        )
        conn.close()

    def test_repository_selector_falls_back_for_invalid_database(self):
        from bff import repositories

        db_path = self.path("invalid.db")
        open(db_path, "wb").close()
        fallback = object()
        with patch.object(repositories, "DB_PATH", db_path), patch.object(
            repositories, "JSONCharityRepository", return_value=fallback
        ) as json_repo, patch.object(repositories, "SQLiteCharityRepository") as sqlite_repo:
            self.assertIs(repositories.get_charity_repository(), fallback)
            json_repo.assert_called_once_with()
            sqlite_repo.assert_not_called()

    def test_repository_selector_uses_valid_database(self):
        from bff import repositories

        db_path = self.path("valid-selector.db")
        conn = sqlite3.connect(db_path)
        db_loader.create_tables(conn)
        conn.close()
        selected = object()
        with patch.object(repositories, "DB_PATH", db_path), patch.object(
            repositories, "SQLiteCharityRepository", return_value=selected
        ) as sqlite_repo, patch.object(repositories, "JSONCharityRepository") as json_repo:
            self.assertIs(repositories.get_charity_repository(), selected)
            sqlite_repo.assert_called_once_with()
            json_repo.assert_not_called()


class TestAtomicDatabaseRebuild(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "charities.db")
        self.preprocessed = os.path.join(self.temp_dir.name, "preprocessed")
        os.makedirs(self.preprocessed)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_jsonl(self, name, records):
        path = os.path.join(self.preprocessed, name)
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def test_successful_rebuild_creates_valid_database(self):
        self.write_jsonl("charities.jsonl", [sample_charity()])
        self.write_jsonl("grants.jsonl", [])
        result = db_loader.rebuild_database_atomically(self.db_path, self.preprocessed)
        self.assertEqual(result["charities_loaded"], 1)
        self.assertEqual(db_loader.validate_database(self.db_path), (True, "valid"))


class TestOptionalProfileForeignKeys(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temporary.name, "links.db")
        self.connection = sqlite3.connect(self.path)
        db_loader.create_tables(self.connection)
        db_loader.insert_charities(self.connection, [sample_charity(charity_id=11, name="Verified profile")])

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    @staticmethod
    def grant(grant_id, funding_profile, recipient_profile):
        return {
            "grant_id": grant_id,
            "funding_charity_id": funding_profile,
            "funding_name": "Observed source funder",
            "funding_org_source_id": "publisher-funder-id",
            "recipient_charity_id": recipient_profile,
            "recipient_name": "Observed source recipient",
            "recipient_org_source_id": "publisher-recipient-id",
            "amount": 12.5,
            "currency": "GBP",
            "date": "2024-01-12",
            "source": "test-publisher",
            "source_url": "https://publisher.example/grant",
            "raw_grant_data": {"evidence": "kept"},
        }

    def test_bulk_upsert_keeps_source_provenance_and_nulls_only_invalid_profile_ids(self):
        db_loader.insert_grants(self.connection, [
            self.grant("verified", 11, 11),
            self.grant("observed", 999999, 888888),
        ])
        rows = self.connection.execute(
            """
            SELECT grant_id, funding_charity_id, recipient_charity_id,
                   funding_org_source_id, recipient_org_source_id, funding_name, raw_grant_data
            FROM grants ORDER BY grant_id
            """
        ).fetchall()
        self.assertEqual(rows[0][:6], ("observed", None, None, "publisher-funder-id", "publisher-recipient-id", "Observed source funder"))
        self.assertIn("kept", rows[0][6])
        self.assertEqual(rows[1][:3], ("verified", 11, 11))
        # Re-importing is idempotent and cannot create a dangling FK.
        db_loader.insert_grants(self.connection, [self.grant("observed", 999999, 888888)])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0], 2)
        self.assertIsNone(self.connection.execute("PRAGMA foreign_key_check").fetchone())

    def test_jsonl_loader_applies_the_same_optional_profile_validation(self):
        charity_jsonl = os.path.join(self.temporary.name, "charities.jsonl")
        grants_jsonl = os.path.join(self.temporary.name, "grants.jsonl")
        with open(charity_jsonl, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(sample_charity(charity_id=12, name="JSONL profile")) + "\n")
        with open(grants_jsonl, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.grant("jsonl", 999999, 12)) + "\n")
        result = db_loader.load_jsonl_to_db(self.connection, charity_jsonl, grants_jsonl, strict=True)
        self.assertEqual(result["grants_loaded"], 1)
        row = self.connection.execute(
            "SELECT funding_charity_id, recipient_charity_id, funding_org_source_id FROM grants WHERE grant_id = 'jsonl'"
        ).fetchone()
        self.assertEqual(row, (None, 12, "publisher-funder-id"))

    def test_failed_import_preserves_last_valid_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            db_path = os.path.join(temporary, "charities.db")
            preprocessed = os.path.join(temporary, "preprocessed")
            os.makedirs(preprocessed)
            conn = sqlite3.connect(db_path)
            db_loader.create_tables(conn)
            db_loader.insert_charities(conn, [sample_charity(name="Preserved Foundation")])
            conn.close()
            with open(os.path.join(preprocessed, "charities.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("{invalid json}\n")
            with open(os.path.join(preprocessed, "grants.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("")
            with self.assertRaises(ValueError):
                db_loader.rebuild_database_atomically(db_path, preprocessed)
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT name FROM charities WHERE charity_id = 123456").fetchone()
            conn.close()
            self.assertEqual(row[0], "Preserved Foundation")
            self.assertEqual(db_loader.validate_database(db_path), (True, "valid"))


class TestFullRunInitialization(unittest.TestCase):
    def make_args(self, fresh):
        return SimpleNamespace(
            source="full_run",
            raw_cc_output=None,
            raw_ts_output=None,
            reg_numbers=[123456],
            search=None,
            org_ids=None,
            all_orgs=False,
            limit=1,
            sleep=0,
            timeout=1,
            skip_scrape=False,
            skip_contact_crawler=True,
            fresh=fresh,
        )

    def execute_full_run(self, fresh):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_cc = {"registered_charity_number": 123456}
            with patch.object(pipeline_module, "PROJECT_ROOT", temp_dir), patch.object(
                pipeline_module, "scrape_cc", return_value=[raw_cc]
            ), patch.object(pipeline_module, "scrape_ts", return_value=[]), patch.object(
                pipeline_module, "consolidate_uk_datasets", return_value=([sample_charity()], [])
            ), patch.object(pipeline_module, "save_raw_cc"), patch.object(
                pipeline_module, "save_raw_ts"
            ), patch.object(
                pipeline_module, "apply_ecb_conversion_backfill",
                return_value={"converted_grants": 0, "total_grants": 0},
            ):
                pipeline_module.run_pipeline(self.make_args(fresh))

            db_path = os.path.join(temp_dir, "data", "charities.db")
            valid, reason = db_loader.validate_database(db_path)
            self.assertTrue(valid, reason)
            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM charities").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)

    def test_first_non_fresh_run_creates_schema(self):
        self.execute_full_run(fresh=False)

    def test_first_fresh_run_creates_schema(self):
        self.execute_full_run(fresh=True)

    def test_full_run_converts_the_staging_database_before_the_only_publish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_cc = {"registered_charity_number": 123456}
            with patch.object(pipeline_module, "PROJECT_ROOT", temp_dir), patch.object(
                pipeline_module, "scrape_cc", return_value=[raw_cc]
            ), patch.object(pipeline_module, "scrape_ts", return_value=[]), patch.object(
                pipeline_module, "consolidate_uk_datasets", return_value=([sample_charity()], [])
            ), patch.object(pipeline_module, "save_raw_cc"), patch.object(
                pipeline_module, "save_raw_ts"
            ), patch.object(
                pipeline_module, "apply_ecb_conversion_backfill",
                return_value={"converted_grants": 0, "total_grants": 0},
            ) as backfill:
                pipeline_module.run_pipeline(self.make_args(fresh=True))

            converted_path, report_path, timeout = backfill.call_args.args
            self.assertNotEqual(converted_path, os.path.join(temp_dir, "data", "charities.db"))
            self.assertTrue(os.path.basename(converted_path).startswith(".charities-"))
            self.assertEqual(
                report_path,
                os.path.join(temp_dir, "data", "processed", "ecb_exchange_rate_backfill_report.json"),
            )
            self.assertEqual(timeout, 1)

    def test_conversion_failure_leaves_active_database_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "charities.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            existing = sqlite3.connect(db_path)
            db_loader.create_tables(existing)
            db_loader.insert_charities(existing, [sample_charity(name="Existing active database")])
            existing.close()
            before = open(db_path, "rb").read()
            raw_cc = {"registered_charity_number": 123456}
            with patch.object(pipeline_module, "PROJECT_ROOT", temp_dir), patch.object(
                pipeline_module, "scrape_cc", return_value=[raw_cc]
            ), patch.object(pipeline_module, "scrape_ts", return_value=[]), patch.object(
                pipeline_module, "consolidate_uk_datasets", return_value=([sample_charity(name="Replacement")], [])
            ), patch.object(pipeline_module, "save_raw_cc"), patch.object(
                pipeline_module, "save_raw_ts"
            ), patch.object(
                pipeline_module, "apply_ecb_conversion_backfill", side_effect=RuntimeError("ECB unavailable")
            ):
                with self.assertRaisesRegex(RuntimeError, "ECB unavailable"):
                    pipeline_module.run_pipeline(self.make_args(fresh=True))
            self.assertEqual(open(db_path, "rb").read(), before)


if __name__ == "__main__":
    unittest.main()
