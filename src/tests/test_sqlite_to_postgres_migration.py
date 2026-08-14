import asyncio
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bff.database import DatabaseSettings
from migration.sqlite_to_postgres import (
    MigrationError,
    PreflightError,
    convert_value,
    migrate,
    open_sqlite_read_only,
    rollback_dataset,
    source_preflight,
)


CODE_REVISION = "a" * 40


def _fixture_database(path: Path, *, invalid_currency: bool = False) -> str:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE charities (
            charity_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            primary_source TEXT, source_record_id TEXT
        );
        CREATE TABLE charity_registry_organizations (
            registry_id TEXT PRIMARY KEY, charity_number TEXT NOT NULL,
            registered_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
            country_code TEXT, source_name TEXT NOT NULL,
            imported_at TEXT NOT NULL, is_current_source_record INTEGER NOT NULL
        );
        CREATE TABLE grants (
            grant_id TEXT PRIMARY KEY, funding_charity_id INTEGER,
            funding_name TEXT, recipient_name TEXT NOT NULL,
            recipient_charity_id INTEGER, amount REAL, amount_eur REAL,
            currency TEXT, date TEXT, source TEXT, source_record_id TEXT,
            programme_area_method TEXT, geography_method TEXT,
            conversion_status TEXT
        );
        CREATE TABLE grant_beneficiary_countries (
            grant_id TEXT NOT NULL, country_code TEXT NOT NULL,
            country_name TEXT NOT NULL, PRIMARY KEY (grant_id, country_code)
        );
        CREATE TABLE grant_beneficiary_terms (
            grant_id TEXT NOT NULL, term TEXT NOT NULL,
            PRIMARY KEY (grant_id, term)
        );
        CREATE TABLE grant_programme_categories (
            grant_id TEXT NOT NULL, programme_area TEXT NOT NULL,
            PRIMARY KEY (grant_id, programme_area)
        );
        CREATE TABLE grant_overview_facts (
            grant_id TEXT PRIMARY KEY, source_namespace TEXT NOT NULL,
            award_date TEXT, award_date_status TEXT NOT NULL,
            currency TEXT, original_amount_minor INTEGER,
            original_amount_status TEXT NOT NULL, eur_amount_minor INTEGER,
            eur_amount_status TEXT NOT NULL, conversion_status TEXT,
            funding_name TEXT NOT NULL, funding_name_normalized TEXT NOT NULL,
            recipient_name TEXT NOT NULL, recipient_name_normalized TEXT NOT NULL,
            country_count INTEGER NOT NULL, programme_category_count INTEGER NOT NULL,
            programme_provenance TEXT NOT NULL, invalid_source_label INTEGER NOT NULL,
            low_confidence_inference INTEGER NOT NULL, origin_country_code TEXT,
            origin_country_name TEXT, origin_source TEXT, data_revision TEXT NOT NULL
        );
        CREATE TABLE grant_source_funder_facts (
            grant_id TEXT NOT NULL, country_code TEXT NOT NULL,
            country_name TEXT NOT NULL, source_namespace TEXT NOT NULL,
            source_funder_key TEXT NOT NULL, identity_method TEXT NOT NULL,
            source_organization_id TEXT, display_name TEXT NOT NULL,
            recipient_key TEXT NOT NULL, recipient_name TEXT NOT NULL,
            award_date TEXT, currency TEXT, original_amount_minor INTEGER,
            original_amount_status TEXT NOT NULL, eur_amount_minor INTEGER,
            eur_amount_status TEXT NOT NULL, conversion_status TEXT,
            country_count INTEGER NOT NULL, linked_profile_id INTEGER,
            data_revision TEXT NOT NULL, PRIMARY KEY (grant_id, country_code)
        );
        CREATE TABLE organization_registry_links (
            registry_id TEXT NOT NULL, enriched_organization_id INTEGER NOT NULL,
            match_status TEXT NOT NULL, match_method TEXT NOT NULL,
            match_confidence REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (registry_id, enriched_organization_id)
        );
        CREATE TABLE source_funder_link_overrides (
            source_namespace TEXT NOT NULL, source_organization_id TEXT NOT NULL,
            link_mode TEXT NOT NULL, updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL, PRIMARY KEY (source_namespace, source_organization_id)
        );
        CREATE TABLE source_funder_profile_cache (
            source_funder_key TEXT PRIMARY KEY, profile_id INTEGER NOT NULL,
            status TEXT NOT NULL, payload TEXT, updated_at TEXT NOT NULL,
            link_revision INTEGER NOT NULL
        );
        CREATE TABLE exchange_rates (
            currency TEXT NOT NULL, rate_date TEXT NOT NULL,
            eur_reference_rate REAL NOT NULL, source_series TEXT NOT NULL,
            source_url TEXT NOT NULL, retrieved_at TEXT NOT NULL,
            PRIMARY KEY (currency, rate_date)
        );
        """
    )
    currency = "BADX" if invalid_currency else "GBP"
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [
            ("schema_version", "7"),
            ("grant_overview_schema_version", "fixture-v1"),
            ("registry_schema_version", "1"),
        ],
    )
    connection.execute(
        "INSERT INTO charities VALUES (1, 'Fixture Foundation', 'fixture', 'charity-1')"
    )
    connection.execute(
        """
        INSERT INTO charity_registry_organizations VALUES (
            'registry-1', '100', 'Fixture Foundation', 'fixture foundation',
            'GB', 'fixture', '2026-01-01T00:00:00Z', 1
        )
        """
    )
    connection.execute(
        """
        INSERT INTO grants VALUES (
            'grant-1', 1, 'Fixture Foundation', 'Fixture Recipient', 1,
            100.0, 100.0, ?, '2025-01-01', 'fixture', 'source-grant-1',
            'source_normalization', 'source_normalization', 'ecb_monthly_average'
        )
        """,
        (currency,),
    )
    connection.execute(
        "INSERT INTO grant_beneficiary_countries VALUES ('grant-1', 'GB', 'United Kingdom')"
    )
    connection.execute(
        "INSERT INTO grant_beneficiary_terms VALUES ('grant-1', 'education')"
    )
    connection.execute(
        "INSERT INTO grant_programme_categories VALUES ('grant-1', 'Education')"
    )
    connection.execute(
        """
        INSERT INTO grant_overview_facts VALUES (
            'grant-1', 'fixture', '2025-01-01', 'valid', 'GBP', 10000,
            'valid', 10000, 'valid', 'ecb_monthly_average',
            'Fixture Foundation', 'fixture foundation', 'Fixture Recipient',
            'fixture recipient', 1, 1, 'source', 0, 0, 'GB',
            'United Kingdom', 'fixture', 'fixture-revision'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO grant_source_funder_facts VALUES (
            'grant-1', 'GB', 'United Kingdom', 'fixture', 'fixture:foundation',
            'source_id', 'foundation-1', 'Fixture Foundation',
            'fixture:recipient', 'Fixture Recipient', '2025-01-01', 'GBP',
            10000, 'valid', 10000, 'valid', 'ecb_monthly_average', 1, 1,
            'fixture-revision'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO organization_registry_links VALUES (
            'registry-1', 1, 'accepted', 'exact_identifier', 1.0,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO source_funder_link_overrides VALUES (
            'fixture', 'foundation-1', 'observed_only',
            '2026-01-01T00:00:00Z', 0
        )
        """
    )
    connection.execute(
        """
        INSERT INTO source_funder_profile_cache VALUES (
            'fixture:foundation', 1, 'ready', '{}',
            '2026-01-01T00:00:00Z', 0
        )
        """
    )
    connection.execute(
        """
        INSERT INTO exchange_rates VALUES (
            'GBP', '2025-01-01', 0.8, 'fixture', 'https://example.invalid/rate',
            '2026-01-01T00:00:00Z'
        )
        """
    )
    connection.commit()
    connection.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestMigrationSourceSafety(unittest.TestCase):
    def test_exchange_rate_month_is_preserved(self):
        self.assertEqual(
            convert_value("grants", "exchange_rate_date", "2025-07"),
            "2025-07",
        )

    def test_grant_award_timestamp_is_preserved(self):
        value = "2016-03-11T16:57:17.743000+00:00"
        self.assertEqual(convert_value("grants", "date", value), value)

    def test_source_is_checksum_verified_and_opened_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.db"
            checksum = _fixture_database(path)
            result = source_preflight(
                path, checksum, "7", enforce_capacity=False
            )
            self.assertEqual(result.checksum, checksum)
            self.assertEqual(result.counts["grants"], 1)
            with open_sqlite_read_only(path) as connection:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE forbidden (id INTEGER)")

    def test_checksum_mismatch_fails_before_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.db"
            _fixture_database(path)
            with self.assertRaises(PreflightError):
                source_preflight(path, "0" * 64, "7", enforce_capacity=False)

    def test_remote_postgres_capacity_does_not_count_rds_storage_as_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.db"
            checksum = _fixture_database(path)
            result = source_preflight(
                path,
                checksum,
                "7",
                enforce_capacity=False,
                remote_postgres=True,
            )
            self.assertEqual(result.capacity.estimated_postgres_and_indexes_bytes, 0)
            self.assertEqual(result.capacity.estimated_wal_and_temp_bytes, 0)
            self.assertEqual(result.capacity.safety_margin_bytes, 4 * 1024**3)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestMigrationPostgreSQLIntegration(unittest.TestCase):
    def test_fixture_activation_idempotence_failure_isolation_and_rollback(self):
        asyncio.run(self._exercise())

    async def _exercise(self):
        configured_url = os.getenv("TEST_DATABASE_URL") or DatabaseSettings.from_env().sqlalchemy_url()
        engine = create_async_engine(configured_url)
        async with engine.connect() as connection:
            prior_active = await connection.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
        suffix = uuid.uuid4().hex
        first = f"fixture-first-{suffix}"
        second = f"fixture-second-{suffix}"
        invalid = f"fixture-invalid-{suffix}"
        try:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source = directory / "valid.db"
                invalid_source = directory / "invalid.db"
                checksum = _fixture_database(source)
                invalid_checksum = _fixture_database(
                    invalid_source, invalid_currency=True
                )
                with sqlite3.connect(invalid_source) as invalid_connection:
                    invalid_connection.execute(
                        "UPDATE exchange_rates SET source_series='must-not-activate'"
                    )
                invalid_checksum = hashlib.sha256(invalid_source.read_bytes()).hexdigest()
                report = await migrate(
                    source, checksum, "7", first, CODE_REVISION,
                    "integration-test", "ci", directory,
                    batch_size=2, enforce_baseline=False, enforce_capacity=False,
                )
                self.assertEqual(report["activation_status"], "active")
                self.assertTrue(all(
                    item["status"] == "pass"
                    for item in report["reconciliation_results"].values()
                ))
                self.assertTrue((directory / f"migration-{first}.json").is_file())

                noop = await migrate(
                    source, checksum, "7", first, CODE_REVISION,
                    "integration-test", "ci", directory,
                    batch_size=2, enforce_baseline=False, enforce_capacity=False,
                )
                self.assertTrue(noop["idempotent_noop"])

                await migrate(
                    source, checksum, "7", second, CODE_REVISION,
                    "integration-test", "ci", directory,
                    batch_size=2, enforce_baseline=False, enforce_capacity=False,
                )
                rolled_back = await rollback_dataset(first)
                self.assertEqual(rolled_back, {"from": second, "to": first})

                with self.assertRaises(MigrationError):
                    await migrate(
                        invalid_source, invalid_checksum, "7", invalid,
                        CODE_REVISION, "integration-test", "ci", directory,
                        batch_size=2, enforce_baseline=False,
                        enforce_capacity=False,
                    )
                async with engine.connect() as connection:
                    active = await connection.scalar(
                        text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                    )
                    self.assertEqual(active, first)
                    quality_count = await connection.scalar(
                        text(
                            """
                            SELECT COUNT(*) FROM data_quality_issues
                            WHERE dataset_version=:version AND status='quarantined'
                            """
                        ),
                        {"version": invalid},
                    )
                    self.assertEqual(quality_count, 1)
                    exchange_rate_source = await connection.scalar(
                        text(
                            """
                            SELECT source_series FROM exchange_rates
                            WHERE currency='GBP' AND rate_date=DATE '2025-01-01'
                            """
                        )
                    )
                    self.assertEqual(exchange_rate_source, "fixture")
                with sqlite3.connect(invalid_source) as invalid_connection:
                    invalid_connection.execute(
                        "UPDATE grants SET currency='GBP'"
                    )
                    invalid_connection.execute(
                        "UPDATE source_funder_link_overrides SET link_mode='blocked'"
                    )
                conflicting_checksum = hashlib.sha256(
                    invalid_source.read_bytes()
                ).hexdigest()
                with self.assertRaises(MigrationError):
                    await migrate(
                        invalid_source, conflicting_checksum, "7", invalid,
                        CODE_REVISION, "integration-test", "ci", directory,
                        batch_size=2, enforce_baseline=False,
                        enforce_capacity=False,
                    )
                async with engine.connect() as connection:
                    active = await connection.scalar(
                        text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                    )
                    self.assertEqual(active, first)
                    override_mode = await connection.scalar(
                        text(
                            """
                            SELECT link_mode FROM source_funder_link_overrides
                            WHERE source_namespace='fixture'
                              AND source_organization_id='foundation-1'
                            """
                        )
                    )
                    self.assertEqual(override_mode, "observed_only")
                recovered = await migrate(
                    source, checksum, "7", invalid, CODE_REVISION,
                    "integration-test", "ci", directory,
                    batch_size=2, enforce_baseline=False, enforce_capacity=False,
                )
                self.assertEqual(recovered["activation_status"], "active")
                recovered_rollback = await rollback_dataset(first)
                self.assertEqual(recovered_rollback, {"from": invalid, "to": first})
        finally:
            if prior_active:
                await rollback_dataset(prior_active)
            async with engine.begin() as connection:
                for table in (
                    "source_funder_profile_cache",
                    "organization_registry_links",
                    "grant_source_funder_facts",
                    "grant_overview_facts",
                    "grant_programme_categories",
                    "grant_beneficiary_terms",
                    "grant_beneficiary_countries",
                    "grants",
                    "charity_registry_organizations",
                    "charities",
                ):
                    await connection.execute(
                        text(
                            f'DELETE FROM "{table}" '
                            "WHERE dataset_version IN (:first, :second, :invalid)"
                        ),
                        {"first": first, "second": second, "invalid": invalid},
                    )
                await connection.execute(
                    text(
                        """
                        DELETE FROM data_quality_issues
                        WHERE dataset_version IN (:first, :second, :invalid)
                        """
                    ),
                    {"first": first, "second": second, "invalid": invalid},
                )
                await connection.execute(
                    text(
                        """
                        DELETE FROM migration_runs
                        WHERE target_dataset_version IN (:first, :second, :invalid)
                        """
                    ),
                    {"first": first, "second": second, "invalid": invalid},
                )
                await connection.execute(
                    text(
                        """
                        DELETE FROM dataset_versions
                        WHERE dataset_version IN (:first, :second, :invalid)
                        """
                    ),
                    {"first": first, "second": second, "invalid": invalid},
                )
                await connection.execute(
                    text("DELETE FROM source_funder_link_overrides WHERE source_namespace='fixture'")
                )
                await connection.execute(
                    text("DELETE FROM exchange_rates WHERE source_series='fixture'")
                )
                active_after_cleanup = await connection.scalar(
                    text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                )
                self.assertEqual(active_after_cleanup, prior_active)
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
