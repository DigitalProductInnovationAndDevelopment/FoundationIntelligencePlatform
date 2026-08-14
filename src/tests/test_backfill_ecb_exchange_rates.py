import io
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from data.db_loader import create_tables
from pipelines.backfill_ecb_exchange_rates import (
    _missing_ecb_fetch_windows,
    _resolve_conversion,
    backfill_database,
    fetch_ecb_daily_rates,
)


ECB_CSV = """KEY,TIME_PERIOD,OBS_VALUE
EXR.D.GBP.EUR.SP00.A,2024-01-05,0.8
EXR.D.GBP.EUR.SP00.A,2024-01-08,0.81
"""


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload.encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def fake_opener(_request, timeout):
    assert timeout == 30
    return FakeResponse(ECB_CSV)


class TestEcbExchangeRateBackfill(unittest.TestCase):
    def test_fetches_named_ecb_csv_fields(self):
        rates = fetch_ecb_daily_rates(
            "GBP", "2024-01-01", "2024-01-10", timeout=30, opener=fake_opener
        )
        self.assertEqual(str(rates["2024-01-05"]), "0.8")
        self.assertEqual(str(rates["2024-01-08"]), "0.81")

    def test_reuses_stored_rates_and_fetches_only_uncovered_dates(self):
        rows = [
            {"currency": "GBP", "date": "2024-01-06", "amount": 100},
            {"currency": "USD", "date": "2025-03-01", "amount": 100},
        ]
        windows = {"GBP": ("2024-01-01", "2024-01-06"), "USD": ("2025-03-01", "2025-03-01")}
        stored = {"GBP": {"2024-01-05": 0.8}, "USD": {"2024-01-01": 1.1}}

        fetch_windows = _missing_ecb_fetch_windows(rows, windows, stored, "2025-03-02")

        self.assertNotIn("GBP", fetch_windows)
        self.assertEqual(fetch_windows["USD"], [("2025-03-01", "2025-03-31")])

    def test_backfill_uses_award_month_ecb_average_without_overwriting_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "charities.db"
            report = Path(temp_dir) / "report.json"
            connection = sqlite3.connect(database)
            create_tables(connection)
            connection.execute(
                """
                INSERT INTO grants (
                    grant_id, recipient_name, amount, currency, date, source
                ) VALUES ('weekend-gbp', 'Recipient', 100, 'GBP', '2024-01-06', '360Giving')
                """
            )
            connection.execute(
                """
                INSERT INTO grants (
                    grant_id, recipient_name, amount, currency, date, source
                ) VALUES ('native-eur', 'Recipient', 30, 'EUR', '2024-01-06', '360Giving')
                """
            )
            connection.execute(
                """
                INSERT INTO grants (
                    grant_id, recipient_name, amount, currency, date, source
                ) VALUES ('pre-euro-reference', 'Recipient', 100, 'GBP', '1995-01-03', '360Giving')
                """
            )
            connection.commit()
            connection.close()

            result = backfill_database(
                database, report, as_of="2024-01-10", timeout=30, opener=fake_opener
            )

            self.assertEqual(result["conversion_status_counts"]["ecb_monthly_average"], 1)
            self.assertEqual(result["overview_cache_prewarmed_grants"], 3)
            self.assertEqual(result["conversion_status_counts"]["native_eur"], 1)
            self.assertEqual(result["conversion_status_counts"]["unavailable_missing_rate"], 1)
            check = sqlite3.connect(database)
            rows = check.execute(
                "SELECT grant_id, amount, amount_eur, exchange_rate_date, conversion_status FROM grants ORDER BY grant_id"
            ).fetchall()
            rates = check.execute("SELECT currency, rate_date, eur_reference_rate FROM exchange_rates").fetchall()
            check.close()
            self.assertEqual(rows, [
                ("native-eur", 30.0, 30.0, None, "native_eur"),
                ("pre-euro-reference", 100.0, None, None, "unavailable_missing_rate"),
                ("weekend-gbp", 100.0, 124.22, "2024-01", "ecb_monthly_average"),
            ])
            self.assertEqual(rates, [("GBP", "2024-01-05", 0.8), ("GBP", "2024-01-08", 0.81)])
            self.assertTrue(os.path.exists(report))

    def test_monthly_average_is_independent_of_award_day_within_the_month(self):
        rates = {"GBP": {"2024-01-02": Decimal("0.8"), "2024-01-31": Decimal("0.82")}}
        dates = {"GBP": sorted(rates["GBP"])}
        first = _resolve_conversion(
            {"amount": 81, "currency": "GBP", "date": "2024-01-01"}, rates, dates, as_of="2024-02-01"
        )
        last = _resolve_conversion(
            {"amount": 81, "currency": "GBP", "date": "2024-01-31"}, rates, dates, as_of="2024-02-01"
        )
        self.assertEqual(first["conversion_status"], "ecb_monthly_average")
        self.assertEqual(last["conversion_status"], "ecb_monthly_average")
        self.assertEqual(first["exchange_rate"], 0.81)
        self.assertEqual(last["exchange_rate"], 0.81)
        self.assertEqual(first["amount_eur"], last["amount_eur"])


if __name__ == "__main__":
    unittest.main()
