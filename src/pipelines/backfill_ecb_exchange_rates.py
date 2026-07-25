"""Backfill reproducible EUR grant values from official ECB reference rates.

The source ``amount`` and ``currency`` columns are immutable source facts. This
pipeline stores a separate EUR display/aggregation amount and enough rate
provenance to explain it later. It uses the ECB EXR daily series, where one EUR
equals ``OBS_VALUE`` units of the quoted currency; converting a source amount
to EUR therefore divides by the published rate.

The update is performed in a cloned SQLite database and atomically published
only after all rate rows and grant conversions have been written successfully.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import csv
import io
import json
import os
import sqlite3
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from data import db_loader


ECB_DATA_API = "https://data-api.ecb.europa.eu/service/data/EXR"
ECB_RATE_SOURCE = "European Central Bank EXR daily reference rate"
ECB_RATE_URL = "https://data.ecb.europa.eu/data/datasets/EXR"
MONEY_QUANTUM = Decimal("0.01")
ECB_FIRST_RATE_DATE = "1999-01-04"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_award_date(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _series_key(currency: str) -> str:
    return f"EXR.D.{currency}.EUR.SP00.A"


def _series_url(currency: str, start_date: str, end_date: str) -> str:
    query = urllib.parse.urlencode({
        "startPeriod": start_date,
        "endPeriod": end_date,
        "format": "csvdata",
    })
    return f"{ECB_DATA_API}/D.{currency}.EUR.SP00.A?{query}"


def fetch_ecb_daily_rates(
    currency: str,
    start_date: str,
    end_date: str,
    *,
    timeout: int = 90,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Decimal]:
    """Return ECB daily rate rows keyed by ECB publication date.

    ECB CSV responses are deliberately parsed using the named ``TIME_PERIOD``
    and ``OBS_VALUE`` columns, rather than relying on column order.
    """
    currency = currency.strip().upper()
    request = urllib.request.Request(
        _series_url(currency, start_date, end_date),
        headers={"Accept": "text/csv", "User-Agent": "FoundationIntelligencePlatform/1.0"},
    )
    with opener(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8-sig")
    return _parse_ecb_csv_rates(currency, payload)


def _parse_ecb_csv_rates(currency: str, payload: str) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {}
    for row in csv.DictReader(io.StringIO(payload)):
        rate_date = _parse_award_date(row.get("TIME_PERIOD"))
        rate = _decimal(row.get("OBS_VALUE"))
        if rate_date and rate is not None and rate > 0:
            rates[rate_date] = rate
    if not rates:
        raise ValueError(f"ECB returned no usable {currency}/EUR reference rates.")
    return rates


def load_cached_ecb_daily_rates(currency: str, directory: Path) -> dict[str, Decimal]:
    """Load one or more previously downloaded ECB CSV chunks for a currency."""
    currency = currency.strip().upper()
    files = sorted(directory.glob(f"ecb-{currency.lower()}*.csv"))
    if not files:
        raise ValueError(f"No cached ECB CSV files found for {currency} in {directory}.")
    rates: dict[str, Decimal] = {}
    for path in files:
        rates.update(_parse_ecb_csv_rates(currency, path.read_text(encoding="utf-8-sig")))
    return rates


def _grant_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT grant_id, amount, currency, date FROM grants ORDER BY grant_id"
    ).fetchall()
    return [dict(row) for row in rows]


def _foreign_rate_windows(rows: Iterable[Mapping[str, Any]], as_of: str) -> dict[str, tuple[str, str]]:
    windows: dict[str, list[str]] = {}
    for row in rows:
        currency = str(row.get("currency") or "").strip().upper()
        award_date = _parse_award_date(row.get("date"))
        amount = _decimal(row.get("amount"))
        if currency == "EUR" or len(currency) != 3 or not currency.isalpha() or not award_date or award_date > as_of:
            continue
        if amount is None:
            continue
        windows.setdefault(currency, []).append(award_date)
    return {
        # ECB does not publish on weekends and market holidays. Fetch a short
        # preceding window so the policy can select the last published business
        # day without ever reaching forward in time.
        currency: ((date.fromisoformat(min(dates)) - timedelta(days=10)).isoformat(), max(dates))
        for currency, dates in windows.items()
    }


def _stored_ecb_daily_rates(connection: sqlite3.Connection) -> dict[str, dict[str, Decimal]]:
    """Reuse previously stored official rates before requesting the ECB again."""
    rows = connection.execute(
        "SELECT currency, rate_date, eur_reference_rate FROM exchange_rates"
    ).fetchall()
    rates: dict[str, dict[str, Decimal]] = {}
    for currency, rate_date, value in rows:
        normalized_currency = str(currency or "").strip().upper()
        normalized_date = _parse_award_date(rate_date)
        rate = _decimal(value)
        if normalized_currency and normalized_date and rate is not None and rate > 0:
            rates.setdefault(normalized_currency, {})[normalized_date] = rate
    return rates


def _missing_ecb_fetch_windows(
    rows: Iterable[Mapping[str, Any]],
    base_windows: Mapping[str, tuple[str, str]],
    stored_rates: Mapping[str, Mapping[str, Decimal]],
    as_of: str,
) -> dict[str, list[tuple[str, str]]]:
    """Fetch only source-date ranges not covered by locally stored ECB rates.

    A rate from the preceding business day remains valid for a short trailing
    holiday/weekend window. Older historical gaps and clearly newer dates are
    requested as separate compact windows instead of redownloading each series.
    """
    award_dates: dict[str, set[str]] = {}
    for row in rows:
        currency = str(row.get("currency") or "").strip().upper()
        award_date = _parse_award_date(row.get("date"))
        amount = _decimal(row.get("amount"))
        if (
            currency in base_windows and award_date and award_date <= as_of
            and amount is not None
        ):
            award_dates.setdefault(currency, set()).add(award_date)

    earliest_ecb_date = date.fromisoformat(ECB_FIRST_RATE_DATE)
    fetch_windows: dict[str, list[tuple[str, str]]] = {}
    for currency, (window_start, window_end) in base_windows.items():
        rates = stored_rates.get(currency, {})
        if not rates:
            start = max(date.fromisoformat(window_start), earliest_ecb_date).isoformat()
            if start <= window_end:
                fetch_windows[currency] = [(start, window_end)]
            continue

        rate_dates = sorted(rates)
        first_rate = date.fromisoformat(rate_dates[0])
        last_rate = date.fromisoformat(rate_dates[-1])
        relevant_dates = sorted(date.fromisoformat(value) for value in award_dates.get(currency, set()))
        early_dates = [value for value in relevant_dates if earliest_ecb_date <= value < first_rate]
        late_dates = [value for value in relevant_dates if value > last_rate + timedelta(days=10)]
        windows: list[tuple[str, str]] = []
        if early_dates:
            start = max(early_dates[0] - timedelta(days=10), earliest_ecb_date)
            windows.append((start.isoformat(), (first_rate - timedelta(days=1)).isoformat()))
        if late_dates:
            start = max(last_rate - timedelta(days=10), late_dates[0] - timedelta(days=10))
            windows.append((start.isoformat(), late_dates[-1].isoformat()))
        if windows:
            fetch_windows[currency] = windows
    return fetch_windows


def _resolve_conversion(
    row: Mapping[str, Any],
    rates_by_currency: Mapping[str, Mapping[str, Decimal]],
    rate_dates_by_currency: Mapping[str, list[str]],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Return data for one grant without ever altering its source amount."""
    amount = _decimal(row.get("amount"))
    currency = str(row.get("currency") or "").strip().upper()
    award_date = _parse_award_date(row.get("date"))
    if amount is None:
        return {"amount_eur": None, "exchange_rate": None, "exchange_rate_date": None,
                "exchange_rate_source": None, "conversion_status": "invalid_source_amount"}
    if len(currency) != 3 or not currency.isalpha():
        return {"amount_eur": None, "exchange_rate": None, "exchange_rate_date": None,
                "exchange_rate_source": None, "conversion_status": "unsupported_source_currency"}
    if currency == "EUR":
        return {
            "amount_eur": float(amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)),
            "exchange_rate": 1.0,
            "exchange_rate_date": None,
            "exchange_rate_source": "Source amount already denominated in EUR",
            "conversion_status": "native_eur",
        }
    if not award_date:
        return {"amount_eur": None, "exchange_rate": None, "exchange_rate_date": None,
                "exchange_rate_source": None, "conversion_status": "missing_award_date"}
    if award_date > as_of:
        return {"amount_eur": None, "exchange_rate": None, "exchange_rate_date": None,
                "exchange_rate_source": None, "conversion_status": "award_date_in_future"}
    dates = rate_dates_by_currency.get(currency, [])
    position = bisect.bisect_right(dates, award_date) - 1
    if position < 0:
        return {"amount_eur": None, "exchange_rate": None, "exchange_rate_date": None,
                "exchange_rate_source": None, "conversion_status": "ecb_rate_unavailable"}
    rate_date = dates[position]
    rate = rates_by_currency[currency][rate_date]
    status = "ecb_award_date" if rate_date == award_date else "ecb_previous_business_day"
    return {
        "amount_eur": float((amount / rate).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)),
        "exchange_rate": float(rate),
        "exchange_rate_date": rate_date,
        "exchange_rate_source": f"{ECB_RATE_SOURCE} ({_series_key(currency)})",
        "conversion_status": status,
    }


def _store_rates(
    connection: sqlite3.Connection,
    rates_by_currency: Mapping[str, Mapping[str, Decimal]],
    retrieved_at: str,
) -> int:
    rows = [
        (currency, rate_date, float(rate), _series_key(currency), ECB_RATE_URL, retrieved_at)
        for currency, rates in rates_by_currency.items()
        for rate_date, rate in rates.items()
    ]
    connection.executemany(
        """
        INSERT OR REPLACE INTO exchange_rates (
            currency, rate_date, eur_reference_rate, source_series, source_url, retrieved_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _prewarm_default_overview(database_path: Path) -> int:
    """Populate the presentation cache after an atomic conversion publish."""
    from bff.repositories import SQLiteCharityRepository

    repository = SQLiteCharityRepository(str(database_path))
    payload = asyncio.run(repository.get_grant_overview(
        sources=["360Giving", "Charity Commission for England and Wales", "Philea"]
    ))
    return int(payload.get("kpis", {}).get("grants_monitored") or 0)


def backfill_database(
    database_path: Path,
    report_path: Path,
    *,
    as_of: str | None = None,
    timeout: int = 90,
    opener: Callable[..., Any] = urllib.request.urlopen,
    rate_cache_directory: Path | None = None,
) -> dict[str, Any]:
    """Fetch needed ECB rates, backfill a staging DB, then publish atomically."""
    if not database_path.exists():
        raise ValueError(f"Database does not exist: {database_path}")
    resolved_as_of = _parse_award_date(as_of) if as_of else date.today().isoformat()
    if not resolved_as_of:
        raise ValueError("as_of must be an ISO date (YYYY-MM-DD)")

    source_uri = database_path.resolve().as_uri() + "?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True)
    try:
        source_rows = _grant_rows(source_connection)
        stored_rates = _stored_ecb_daily_rates(source_connection)
    finally:
        source_connection.close()
    windows = _foreign_rate_windows(source_rows, resolved_as_of)
    fetch_windows: dict[str, list[tuple[str, str]]] = {}
    fetch_errors: dict[str, str] = {}
    if rate_cache_directory is not None:
        rates_by_currency = {
            currency: load_cached_ecb_daily_rates(currency, rate_cache_directory)
            for currency in windows
        }
    else:
        rates_by_currency = {
            currency: dict(stored_rates.get(currency, {}))
            for currency in windows
        }
        fetch_windows = _missing_ecb_fetch_windows(
            source_rows, windows, rates_by_currency, resolved_as_of
        )
        for currency, intervals in fetch_windows.items():
            for start_date, end_date in intervals:
                try:
                    rates_by_currency[currency].update(
                        fetch_ecb_daily_rates(
                            currency, start_date, end_date, timeout=timeout, opener=opener
                        )
                    )
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    # ECB does not publish a reference series for every valid
                    # ISO currency. Leave those grants explicitly unconverted.
                    fetch_errors[currency] = str(exc)
    rate_dates_by_currency = {
        currency: sorted(rates)
        for currency, rates in rates_by_currency.items()
    }

    staging_path: str | None = None
    connection: sqlite3.Connection | None = None
    retrieved_at = _utc_now()
    try:
        staging_path, connection = db_loader.create_staging_database(
            str(database_path), preserve_existing=True
        )
        _store_rates(connection, rates_by_currency, retrieved_at)
        rows = _grant_rows(connection)
        updates = []
        statuses: Counter[str] = Counter()
        for row in rows:
            conversion = _resolve_conversion(
                row, rates_by_currency, rate_dates_by_currency, as_of=resolved_as_of
            )
            statuses[conversion["conversion_status"]] += 1
            updates.append((
                conversion["amount_eur"], conversion["exchange_rate"],
                conversion["exchange_rate_date"], conversion["exchange_rate_source"],
                conversion["conversion_status"], row["grant_id"],
            ))
        connection.executemany(
            """
            UPDATE grants
            SET amount_eur = ?, exchange_rate = ?, exchange_rate_date = ?,
                exchange_rate_source = ?, conversion_status = ?
            WHERE grant_id = ?
            """,
            updates,
        )
        # Currency conversions change every Auto/EUR aggregate; source facts
        # and country/programme indexes remain valid, but cached payloads do not.
        connection.execute("DELETE FROM grant_overview_cache")
        connection.execute(
            "DELETE FROM metadata WHERE key = 'grant_overview_index_revision'"
        )
        connection.commit()
        total_grants = connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
        converted_grants = connection.execute(
            "SELECT COUNT(*) FROM grants WHERE amount_eur IS NOT NULL"
        ).fetchone()[0]
        connection.close()
        connection = None
        db_loader.publish_staging_database(staging_path, str(database_path))
    except Exception:
        if connection is not None:
            connection.close()
        if staging_path and os.path.exists(staging_path):
            os.unlink(staging_path)
        raise

    prewarmed_grants = _prewarm_default_overview(database_path)

    report = {
        "dataset_profile": "ecb-eur-reference-rate-backfill-v1",
        "created_at": retrieved_at,
        "database_path": str(database_path),
        "as_of": resolved_as_of,
        "rate_source": ECB_RATE_SOURCE,
        "rate_source_url": ECB_RATE_URL,
        "rate_windows": {
            currency: {"from": start_date, "to": end_date, "rate_rows": len(rates_by_currency[currency])}
            for currency, (start_date, end_date) in windows.items()
        },
        "rate_fetch_windows": {
            currency: [{"from": start_date, "to": end_date} for start_date, end_date in intervals]
            for currency, intervals in fetch_windows.items()
        },
        "rate_fetch_errors": fetch_errors,
        "rate_rows_stored": sum(len(rates) for rates in rates_by_currency.values()),
        "grants_total": total_grants,
        "grants_with_eur_amount": converted_grants,
        "overview_cache_prewarmed_grants": prewarmed_grants,
        "conversion_status_counts": dict(sorted(statuses.items())),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill grant EUR values from ECB historic reference rates.")
    parser.add_argument("--database", type=Path, default=Path("src/data/charities.db"))
    parser.add_argument(
        "--report", type=Path,
        default=Path("src/data/processed/ecb_exchange_rate_backfill_report.json"),
    )
    parser.add_argument("--as-of", default=None, help="Maximum award/rate date, in YYYY-MM-DD form (defaults to today).")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--rate-cache-directory", type=Path, default=None,
        help="Optional directory containing official ECB CSV chunks named ecb-<currency>*.csv.",
    )
    args = parser.parse_args()
    print(json.dumps(backfill_database(
        args.database, args.report, as_of=args.as_of, timeout=args.timeout,
        rate_cache_directory=args.rate_cache_directory,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
