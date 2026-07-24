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

    def test_failed_import_preserves_last_valid_database(self):
        conn = sqlite3.connect(self.db_path)
        db_loader.create_tables(conn)
        db_loader.insert_charities(conn, [sample_charity(name="Preserved Foundation")])
        conn.close()

        with open(os.path.join(self.preprocessed, "charities.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("{invalid json}\n")
        self.write_jsonl("grants.jsonl", [])

        with self.assertRaises(ValueError):
            db_loader.rebuild_database_atomically(self.db_path, self.preprocessed)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT name FROM charities WHERE charity_id = 123456").fetchone()
        conn.close()
        self.assertEqual(row[0], "Preserved Foundation")
        self.assertEqual(db_loader.validate_database(self.db_path), (True, "valid"))


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


if __name__ == "__main__":
    unittest.main()
