import json
import os
import sqlite3
import hashlib
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from bff.config import DATA_PATH, DB_PATH
from bff.utils.logging import logger
from data.db_loader import validate_database
from preprocessing.enrichment import enrich_organization
from scoring.engine import load_score_configuration, score_relevance


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_list(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _stable_party_id(role, charity_id, source_id, name, country="", source="360Giving"):
    if charity_id is not None:
        return f"organization:{charity_id}"
    namespace = re.sub(r"[^a-z0-9]+", "", (source or "source").lower()) or "source"
    if source_id:
        return f"{namespace}:{role}:{source_id}"
    normalized = re.sub(r"[^a-z0-9]+", "-", (name or "unnamed").lower()).strip("-")
    digest_input = f"{source}|{role}|{normalized}|{country}".encode("utf-8")
    digest = hashlib.sha256(digest_input).hexdigest()[:16]
    return f"{namespace}:{role}:fallback:{digest}"

class CharityRepository(ABC):
    """
    Abstract Base Class for Charity data access.
    Allows swapping storage backends (e.g. JSON to SQL Database) without changing API layer.
    """
    @abstractmethod
    async def get_all(
        self, 
        search: Optional[str] = None, 
        reg_status: Optional[str] = None, 
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grants_map(
        self, currency: Optional[str] = None, min_coverage: float = 0.30
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grant_summary(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_sankey_data(
        self, charity_id: int, currency: Optional[str] = None, limit: int = 30
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_score(
        self, charity_id: int, target_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        pass


class JSONCharityRepository(CharityRepository):
    """
    JSON File-backed implementation of CharityRepository.
    Loads and caches JSON records in memory.
    """
    def __init__(self, data_path: str = DATA_PATH):
        self.data_path = data_path
        self._data: List[Dict[str, Any]] = []
        self.load_data()

    def load_data(self):
        """Loads data from the JSON file path. Handles missing/corrupted files gracefully."""
        if not os.path.exists(self.data_path):
            logger.warning(f"Data file not found at {self.data_path}. Initializing with empty list.")
            self._data = []
            return

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info(f"Loaded {len(self._data)} charity records from {self.data_path}")
        except Exception as e:
            logger.error(f"Failed to parse JSON data file at {self.data_path}: {e}")
            self._data = []

    def _get_financials(self, charity: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
        """
        Helper to extract latest income and expenditure, fallback to financial_history if needed.
        """
        all_details = charity.get("all_details", {})
        income = all_details.get("latest_income")
        expenditure = all_details.get("latest_expenditure")

        # Fallback to financial history if direct fields are missing
        if (income is None or expenditure is None) and charity.get("financial_history"):
            history = charity["financial_history"]
            sorted_history = sorted(
                history, 
                key=lambda x: x.get("financial_period_end_date", ""), 
                reverse=True
            )
            if sorted_history:
                latest_period = sorted_history[0]
                if income is None:
                    income = latest_period.get("income")
                if expenditure is None:
                    expenditure = latest_period.get("expenditure")

        return income, expenditure

    async def get_all(
        self, 
        search: Optional[str] = None, 
        reg_status: Optional[str] = None, 
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        filtered = self._data

        def enrichment(item):
            return enrich_organization(item)

        # Filter by search string (case-insensitive name search)
        if search:
            search_lower = search.lower()
            filtered = [
                c for c in filtered 
                if search_lower in c.get("all_details", {}).get("charity_name", "").lower()
            ]

        # Filter by reg_status (e.g. 'R' for active, 'RM' for removed)
        if reg_status:
            reg_status_upper = reg_status.upper()
            filtered = [
                c for c in filtered 
                if c.get("all_details", {}).get("reg_status", "").upper() == reg_status_upper
            ]

        # Filter by tags (multiple choice, match ANY)
        if tags:
            selected = {t.casefold() for t in tags}
            filtered = [
                c for c in filtered
                if selected.intersection({
                    value.casefold()
                    for value in (
                        enrichment(c)["programme_areas_source"]
                        + enrichment(c)["programme_areas_inferred"]
                    )
                })
            ]
        elif tag:
            selected = tag.casefold()
            filtered = [
                c for c in filtered
                if selected in {
                    value.casefold()
                    for value in (
                        enrichment(c)["programme_areas_source"]
                        + enrichment(c)["programme_areas_inferred"]
                    )
                }
            ]

        # Filter by foundation regions (multiple choice, match ANY)
        if foundation_regions:
            selected = {r.casefold() for r in foundation_regions}
            filtered = [
                c for c in filtered
                if selected.intersection({
                    str(enrichment(c).get("headquarters_country") or "").casefold(),
                    str(enrichment(c).get("headquarters_region") or "").casefold(),
                })
            ]

        # Filter by funding regions (multiple choice, match ANY)
        if funding_regions:
            # JSON fallback has no normalized grant transactions or destinations.
            filtered = []

        # Filter by legacy region
        if not foundation_regions and not funding_regions and region:
            region_lower = region.lower()
            filtered = [
                c for c in filtered
                if any(region_lower in r.lower() for r in c.get("geo_locations", {}).keys())
            ]

        # Filter by size
        if min_annual_giving is not None:
            filtered = [
                c for c in filtered
                if (self._get_financials(c)[1] or 0.0) >= min_annual_giving
            ]
        elif size:
            size_lower = size.lower()
            temp = []
            for c in filtered:
                income, expenditure = self._get_financials(c)
                exp = expenditure or 0.0
                if size_lower == "small" and exp < 1000000:
                    temp.append(c)
                elif size_lower == "medium" and exp >= 1000000 and exp <= 10000000:
                    temp.append(c)
                elif size_lower == "large" and exp > 10000000:
                    temp.append(c)
            filtered = temp

        # Filter by average grant size
        if min_avg_grant_size is not None and min_avg_grant_size > 0:
            # Simple heuristic for JSON repo mock filtering: if expenditure is non-zero, let's assume it matches
            filtered = [
                c for c in filtered
                if (self._get_financials(c)[1] or 0.0) >= min_avg_grant_size
            ]

        # Map to baseline models
        results = []
        for c in filtered[skip : skip + limit]:
            income, expenditure = self._get_financials(c)
            all_details = c.get("all_details", {})
            enriched = enrichment(c)
            results.append({
                "registered_charity_number": c.get("registered_charity_number"),
                "suffix": c.get("suffix", 0),
                "link": c.get("link"),
                "charity_name": all_details.get("charity_name", ""),
                "reg_status": all_details.get("reg_status", "RM"),
                "reporting_status": all_details.get("reporting_status"),
                "removal_reason": all_details.get("removal_reason"),
                "latest_income": income,
                "latest_expenditure": expenditure,
                "programme_areas_source": enriched["programme_areas_source"],
                "programme_areas_inferred": enriched["programme_areas_inferred"],
                "geographic_focus_source": enriched["geographic_focus_source"],
                "geographic_focus_inferred": enriched["geographic_focus_inferred"],
                "headquarters_country": enriched["headquarters_country"],
                "headquarters_region": enriched["headquarters_region"],
                "programme_area_review_required": enriched["programme_area_review_required"],
                "geography_review_required": enriched["geography_review_required"],
                "enrichment_rule_version": enriched["enrichment_rule_version"],
                "organization_type": all_details.get("charity_type") or "charity",
                "primary_source": "Charity Commission for England and Wales",
                "source_names": ["Charity Commission for England and Wales"],
                "source_record_id": str(c.get("registered_charity_number")),
                "source_url": c.get("link"),
                "transaction_coverage": "source_without_transactions",
            })
        return results

    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        for c in self._data:
            if c.get("registered_charity_number") == reg_charity_number:
                return {
                    **c,
                    **enrich_organization(c),
                    "organization_type": c.get("all_details", {}).get("charity_type") or "charity",
                    "primary_source": "Charity Commission for England and Wales",
                    "source_names": ["Charity Commission for England and Wales"],
                    "source_record_id": str(c.get("registered_charity_number")),
                    "source_url": c.get("link"),
                    "source_records": [],
                    "transaction_coverage": "source_without_transactions",
                }
        return None

    async def get_stats(self) -> Dict[str, Any]:
        total = len(self._data)
        active = 0
        removed = 0
        incomes = []
        expenditures = []

        for c in self._data:
            all_details = c.get("all_details", {})
            status = all_details.get("reg_status", "").upper()
            if status == "R":
                active += 1
            else:
                removed += 1

            inc, exp = self._get_financials(c)
            if inc is not None:
                incomes.append(inc)
            if exp is not None:
                expenditures.append(exp)

        avg_income = sum(incomes) / len(incomes) if incomes else 0.0
        avg_exp = sum(expenditures) / len(expenditures) if expenditures else 0.0

        return {
            "total_charities": total,
            "active_charities": active,
            "removed_charities": removed,
            "average_income": avg_income,
            "average_expenditure": avg_exp,
            "total_grants": None,
            "data_mode": "cached_source_without_transactions",
            "source": ["Charity Commission for England and Wales"],
            "source_counts": {"Charity Commission for England and Wales": total},
            "organization_type_counts": {"charity": total},
        }

    async def get_grants_map(
        self, currency: Optional[str] = None, min_coverage: float = 0.30
    ) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "geographic_dimension": "beneficiary_location",
            "items": [],
            "known_geography_count": 0,
            "unknown_geography_count": 0,
            "coverage_percentage": 0.0,
            "currencies": [],
            "minimum_coverage_threshold": min_coverage,
            "metadata": {
                "data_mode": "cached_source_without_transactions",
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "record_count": 0,
                "derivation": "No grant aggregation is available in JSON fallback mode.",
                "coverage": 0.0,
                "limitations": ["Build a valid SQLite database to enable transaction geography."],
            },
        }

    async def get_grant_summary(self) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "total_grant_count": 0,
            "currencies": [],
            "largest_donors": [],
            "largest_recipients": [],
            "metadata": {
                "data_mode": "cached_source_without_transactions",
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "record_count": 0,
                "limitations": ["The JSON fallback does not contain normalized grant transactions."],
            },
        }

    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "organization_id": charity_id,
            "role": role,
            "transaction_coverage": "not_loaded",
            "grant_count": 0,
            "currencies": [],
            "grants": [],
            "metadata": {
                "data_mode": "cached_source_without_transactions",
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "record_count": 0,
                "limitations": ["The JSON fallback does not expose 360Giving transactions."],
            },
        }

    async def get_sankey_data(
        self, charity_id: int, currency: Optional[str] = None, limit: int = 30
    ) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "nodes": [],
            "links": [],
            "metadata": {
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "grant_count": 0,
                "included_grant_count": 0,
                "excluded_grant_count": 0,
                "excluded_reasons": {},
                "included_value": 0.0,
                "currencies": [],
                "selected_currency": currency,
                "conversion_method": "none",
                "filters_applied": {"organization_id": charity_id, "limit": limit},
                "truncation_applied": False,
            },
        }

    async def get_score(
        self, charity_id: int, target_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        organization = await self.get_by_id(charity_id)
        if not organization:
            raise KeyError(charity_id)
        config = load_score_configuration()
        _, expenditure = self._get_financials(organization)
        score_input = {
            **organization,
            "annual_expenditure": expenditure,
        }
        return score_relevance(
            score_input,
            target_profile or config.example_target_profile,
            grant_statistics={},
            configuration=config,
        )


class SQLiteCharityRepository(CharityRepository):
    """
    SQLite database-backed implementation of CharityRepository.
    Loads and queries structured charity commission and grant details.
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info(f"SQLite Charity Repository initialized at: {self.db_path}")
        
    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    async def get_all(
        self, 
        search: Optional[str] = None, 
        reg_status: Optional[str] = None, 
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT charity_id, name, type, website, email, address, city, state, country, 
                   latitude, longitude, annual_income, annual_expenditure, thematic_focus, 
                   geographic_focus, raw_cc_data, programme_areas_source,
                   programme_areas_inferred, geographic_focus_source,
                   geographic_focus_inferred, headquarters_country, headquarters_region,
                   programme_area_review_required, geography_review_required,
                   enrichment_rule_version, organization_type, primary_source, source_names,
                   source_record_id, source_url, transaction_coverage
            FROM charities 
            WHERE 1=1
        """
        params = []
        
        if search:
            query += " AND name LIKE ?"
            params.append(f"%{search}%")
            
        if reg_status:
            query += " AND json_extract(raw_cc_data, '$.all_details.reg_status') = ?"
            params.append(reg_status.upper())
            
        if tags:
            tag_conds = []
            for t in tags:
                tag_conds.append("""(
                    EXISTS (SELECT 1 FROM json_each(charities.programme_areas_source) WHERE value = ?)
                    OR EXISTS (SELECT 1 FROM json_each(charities.programme_areas_inferred) WHERE value = ?)
                )""")
                params.extend([t, t])
            query += " AND (" + " OR ".join(tag_conds) + ")"
        elif tag:
            query += """ AND (
                EXISTS (SELECT 1 FROM json_each(charities.programme_areas_source) WHERE value = ?)
                OR EXISTS (SELECT 1 FROM json_each(charities.programme_areas_inferred) WHERE value = ?)
            )"""
            params.extend([tag, tag])
            
        if foundation_regions:
            fr_conds = []
            for r in foundation_regions:
                fr_conds.append("(headquarters_country = ? OR headquarters_region = ?)")
                params.extend([r, r])
            query += " AND (" + " OR ".join(fr_conds) + ")"

        if funding_regions:
            fur_conds = []
            for r in funding_regions:
                fur_conds.append("""charity_id IN (
                    SELECT funding_charity_id FROM grants
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(grants.beneficiary_geography_normalized)
                        WHERE json_extract(value, '$.name') = ?
                           OR json_extract(value, '$.macro_region') = ?
                    )
                )""")
                params.extend([r, r])
            query += " AND (" + " OR ".join(fur_conds) + ")"

        if not foundation_regions and not funding_regions and region:
            query += """ AND (
                headquarters_country = ?
                OR headquarters_region = ?
                OR EXISTS (SELECT 1 FROM json_each(charities.geographic_focus_inferred) WHERE value = ?)
                OR charity_id IN (
                    SELECT funding_charity_id FROM grants
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(grants.beneficiary_geography_normalized)
                        WHERE json_extract(value, '$.name') = ?
                           OR json_extract(value, '$.macro_region') = ?
                    )
                )
            )"""
            params.extend([region, region, region, region, region])

        if min_annual_giving is not None:
            query += " AND annual_expenditure >= ?"
            params.append(min_annual_giving)
        elif size == "small":
            query += " AND annual_expenditure < 1000000"
        elif size == "medium":
            query += " AND annual_expenditure >= 1000000 AND annual_expenditure <= 10000000"
        elif size == "large":
            query += " AND annual_expenditure > 10000000"

        if min_avg_grant_size is not None and min_avg_grant_size > 0:
            query += """ AND charity_id IN (
                SELECT funding_charity_id 
                FROM grants 
                GROUP BY funding_charity_id 
                HAVING COUNT(DISTINCT currency) = 1 AND AVG(amount) >= ?
            )"""
            params.append(min_avg_grant_size)
            
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, skip])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            raw_cc = json.loads(r[15]) if r[15] else {}
            all_details = raw_cc.get("all_details") or {}
            
            results.append({
                "registered_charity_number": r[0],
                "suffix": all_details.get("group_subsid_suffix", 0),
                "link": raw_cc.get("link", f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regid={r[0]}&subid=0"),
                "charity_name": r[1],
                "reg_status": all_details.get("reg_status", "RM"),
                "reporting_status": all_details.get("reporting_status"),
                "removal_reason": all_details.get("removal_reason"),
                "latest_income": r[11],
                "latest_expenditure": r[12],
                "programme_areas_source": _json_list(r[16]),
                "programme_areas_inferred": _json_list(r[17]),
                "geographic_focus_source": _json_list(r[18]),
                "geographic_focus_inferred": _json_list(r[19]),
                "headquarters_country": r[20],
                "headquarters_region": r[21],
                "programme_area_review_required": bool(r[22]),
                "geography_review_required": bool(r[23]),
                "enrichment_rule_version": r[24],
                "organization_type": r[25] or r[2] or "unknown",
                "primary_source": r[26],
                "source_names": _json_list(r[27]),
                "source_record_id": r[28],
                "source_url": r[29],
                "transaction_coverage": r[30] or "unknown",
            })
        return results

    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT raw_cc_data, programme_areas_source, programme_areas_inferred,
                   programme_area_scores, programme_area_method, programme_area_evidence,
                   programme_area_review_required, geographic_focus_source,
                   geographic_focus_inferred, headquarters_country, headquarters_region,
                   geography_method, geography_confidence, geography_evidence,
                   geography_review_required, enrichment_rule_version, organization_type,
                   primary_source, source_names, source_record_id, source_url,
                   source_records, ingestion_timestamp, transaction_coverage,
                   deduplication_status, deduplication_candidates
            FROM charities WHERE charity_id = ?
        """, (reg_charity_number,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            raw = json.loads(row[0])
            raw.update({
                "programme_areas_source": _json_list(row[1]),
                "programme_areas_inferred": _json_list(row[2]),
                "programme_area_scores": json.loads(row[3]) if row[3] else {},
                "programme_area_method": row[4],
                "programme_area_evidence": _json_list(row[5]),
                "programme_area_review_required": bool(row[6]),
                "geographic_focus_source": _json_list(row[7]),
                "geographic_focus_inferred": _json_list(row[8]),
                "headquarters_country": row[9],
                "headquarters_region": row[10],
                "geography_method": row[11],
                "geography_confidence": row[12],
                "geography_evidence": _json_list(row[13]),
                "geography_review_required": bool(row[14]),
                "enrichment_rule_version": row[15],
                "organization_type": row[16] or "unknown",
                "primary_source": row[17],
                "source_names": _json_list(row[18]),
                "source_record_id": row[19],
                "source_url": row[20],
                "source_records": _json_list(row[21]),
                "ingestion_timestamp": row[22],
                "transaction_coverage": row[23] or "unknown",
                "deduplication_status": row[24],
                "deduplication_candidates": _json_list(row[25]),
            })
            return raw
        return None

    async def get_stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM charities")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM charities WHERE json_extract(raw_cc_data, '$.all_details.reg_status') = 'R'")
        active = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM charities WHERE json_extract(raw_cc_data, '$.all_details.reg_status') = 'RM'")
        removed = cursor.fetchone()[0]
        
        cursor.execute("SELECT AVG(annual_income) FROM charities WHERE annual_income IS NOT NULL")
        avg_income = cursor.fetchone()[0] or 0.0
        
        cursor.execute("SELECT AVG(annual_expenditure) FROM charities WHERE annual_expenditure IS NOT NULL")
        avg_exp = cursor.fetchone()[0] or 0.0

        cursor.execute("SELECT COUNT(*) FROM grants")
        total_grants = cursor.fetchone()[0]

        cursor.execute("SELECT DISTINCT source FROM grants WHERE source IS NOT NULL AND source != ''")
        grant_sources = sorted(row[0] for row in cursor.fetchall())

        cursor.execute("""
            SELECT source.value, COUNT(*)
            FROM charities, json_each(charities.source_names) AS source
            GROUP BY source.value
        """)
        source_counts = {str(row[0]): row[1] for row in cursor.fetchall()}
        cursor.execute("""
            SELECT COALESCE(NULLIF(organization_type, ''), 'unknown'), COUNT(*)
            FROM charities GROUP BY COALESCE(NULLIF(organization_type, ''), 'unknown')
        """)
        organization_type_counts = {str(row[0]): row[1] for row in cursor.fetchall()}
        
        conn.close()
        return {
            "total_charities": total,
            "active_charities": active,
            "removed_charities": removed,
            "average_income": avg_income,
            "average_expenditure": avg_exp,
            "total_grants": total_grants,
            "data_mode": "derived_from_cached_source",
            "source": sorted(set(source_counts) | set(grant_sources)),
            "source_counts": source_counts,
            "organization_type_counts": organization_type_counts,
        }

    async def get_grants_map(
        self, currency: Optional[str] = None, min_coverage: float = 0.30
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT grant_id, amount, currency, beneficiary_geography_normalized FROM grants")
        rows = cursor.fetchall()
        conn.close()

        currencies = sorted({str(row[2]).upper() for row in rows if row[2]})
        selected_currency = currency.upper() if currency else (currencies[0] if len(currencies) == 1 else None)
        base_metadata = {
            "data_mode": "derived_from_cached_source",
            "source": ["360Giving"],
            "generated_at": _utc_now(),
            "record_count": len(rows),
            "derivation": "Aggregated source-provided beneficiary locations; no headquarters inference.",
            "limitations": [],
        }
        if rows and selected_currency is None:
            base_metadata["limitations"] = [
                "Multiple currencies are present; select one currency before aggregating amounts."
            ]
            return {
                "status": "mixed_currency_requires_filter",
                "geographic_dimension": "beneficiary_location",
                "items": [],
                "known_geography_count": 0,
                "unknown_geography_count": len(rows),
                "coverage_percentage": 0.0,
                "currencies": currencies,
                "minimum_coverage_threshold": min_coverage,
                "metadata": {**base_metadata, "coverage": 0.0},
            }

        selected_rows = [row for row in rows if not selected_currency or str(row[2]).upper() == selected_currency]
        known_count = 0
        aggregates = {}
        excluded_multiple = 0
        excluded_amount = 0
        for _, amount, row_currency, raw_locations in selected_rows:
            locations = _json_list(raw_locations)
            unique_locations = {}
            for location in locations:
                if not isinstance(location, dict):
                    continue
                name = str(location.get("name") or "").strip()
                code = str(location.get("code") or "").strip().upper() or None
                if name.lower() in {"multi", "multiple", "various"}:
                    continue
                if name.lower() in {"worldwide", "global", "international"}:
                    code, name = "GLOBAL", "Worldwide / global scope"
                if code or name:
                    unique_locations[(code or name.lower(), name or code)] = (code, name or code)
            if len(unique_locations) != 1:
                excluded_multiple += 1
                continue
            known_count += 1
            if amount is None or amount <= 0:
                excluded_amount += 1
                continue
            code, name = next(iter(unique_locations.values()))
            key = (code, name, str(row_currency).upper())
            current = aggregates.setdefault(key, {"grant_count": 0, "total_amount": 0.0})
            current["grant_count"] += 1
            current["total_amount"] += float(amount)

        total_selected = len(selected_rows)
        unknown_count = total_selected - known_count
        coverage = known_count / total_selected if total_selected else 0.0
        base_metadata["coverage"] = coverage
        if excluded_multiple:
            base_metadata["limitations"].append(
                f"{excluded_multiple} grants lacked one unambiguous source-provided beneficiary location."
            )
        if excluded_amount:
            base_metadata["limitations"].append(
                f"{excluded_amount} geographically known grants had missing or non-positive amounts."
            )

        status_value = "available"
        items = [
            {
                "region_or_country_code": code,
                "region_or_country_name": name,
                "grant_count": values["grant_count"],
                "total_amount": round(values["total_amount"], 2),
                "currency": row_currency,
            }
            for (code, name, row_currency), values in aggregates.items()
        ]
        items.sort(key=lambda item: item["total_amount"], reverse=True)
        if not total_selected:
            status_value = "no_data"
        elif coverage < min_coverage:
            status_value = "low_coverage"
            items = []
            base_metadata["limitations"].append(
                "Coverage is below the configured display threshold; aggregation is withheld."
            )

        return {
            "status": status_value,
            "geographic_dimension": "beneficiary_location",
            "items": items[:30],
            "known_geography_count": known_count,
            "unknown_geography_count": unknown_count,
            "coverage_percentage": round(coverage * 100, 2),
            "currencies": currencies,
            "minimum_coverage_threshold": min_coverage,
            "metadata": base_metadata,
        }

    async def get_grant_summary(self) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM grants")
        total_count = cursor.fetchone()[0]
        cursor.execute("SELECT DISTINCT currency FROM grants WHERE currency IS NOT NULL AND currency != ''")
        currencies = sorted(row[0].upper() for row in cursor.fetchall())
        cursor.execute("""
            SELECT funding_charity_id, COALESCE(NULLIF(funding_name, ''), 'Unknown donor'),
                   currency, SUM(amount), COUNT(*)
            FROM grants
            WHERE amount > 0 AND currency IS NOT NULL
            GROUP BY funding_charity_id, funding_name, currency
            ORDER BY SUM(amount) DESC LIMIT 10
        """)
        donors = [
            {
                "organization_id": row[0], "organization_name": row[1],
                "currency": row[2], "total_amount": round(row[3], 2), "grant_count": row[4]
            }
            for row in cursor.fetchall()
        ]
        cursor.execute("""
            SELECT recipient_charity_id, COALESCE(NULLIF(recipient_name, ''), 'Unknown recipient'),
                   currency, SUM(amount), COUNT(*)
            FROM grants
            WHERE amount > 0 AND currency IS NOT NULL
            GROUP BY recipient_charity_id, recipient_name, currency
            ORDER BY SUM(amount) DESC LIMIT 10
        """)
        recipients = [
            {
                "organization_id": row[0], "organization_name": row[1],
                "currency": row[2], "total_amount": round(row[3], 2), "grant_count": row[4]
            }
            for row in cursor.fetchall()
        ]
        conn.close()
        return {
            "status": "available" if total_count else "no_data",
            "total_grant_count": total_count,
            "currencies": currencies,
            "largest_donors": donors,
            "largest_recipients": recipients,
            "metadata": {
                "data_mode": "derived_from_cached_source",
                "source": ["360Giving"],
                "generated_at": _utc_now(),
                "record_count": total_count,
                "derivation": "Currency-separated sums of stored source grant amounts.",
                "limitations": ["Rankings do not combine values across currencies."],
            },
        }

    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transaction_coverage, source_names FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        organization_row = cursor.fetchone()
        
        query = """
            SELECT grant_id, funding_charity_id, funding_name, funding_org_source_id,
                   recipient_name, recipient_charity_id, recipient_org_source_id,
                   amount, amount_eur, currency, description, date, recipient_region,
                   beneficiary_geography, tags, source, source_record_id, source_url,
                   programme_area_source, programme_area_inferred, programme_area_scores,
                   programme_area_method, programme_area_evidence,
                   programme_area_review_required, beneficiary_geography_normalized,
                   geographic_focus_inferred, geography_method, geography_confidence,
                   geography_evidence, geography_review_required, enrichment_rule_version
            FROM grants
            WHERE 1=1
        """
        params = []
        role_lower = role.lower()
        if role_lower == "funder":
            query += " AND funding_charity_id = ?"
            params.append(charity_id)
        elif role_lower == "recipient":
            query += " AND recipient_charity_id = ?"
            params.append(charity_id)
        else:
            query += " AND (funding_charity_id = ? OR recipient_charity_id = ?)"
            params.extend([charity_id, charity_id])
            
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            results.append({
                "grant_id": r[0],
                "funding_charity_id": r[1],
                "funding_name": r[2],
                "funding_org_source_id": r[3],
                "recipient_name": r[4],
                "recipient_charity_id": r[5],
                "recipient_org_source_id": r[6],
                "amount": r[7],
                "amount_eur": r[8],
                "currency": r[9] or "UNKNOWN",
                "description": r[10] or "",
                "date": r[11] or "",
                "recipient_region": r[12],
                "beneficiary_geography": _json_list(r[13]),
                "tags": _json_list(r[14]),
                "source": r[15],
                "source_record_id": r[16],
                "source_url": r[17],
                "programme_area_source": _json_list(r[18]),
                "programme_area_inferred": _json_list(r[19]),
                "programme_area_scores": json.loads(r[20]) if r[20] else {},
                "programme_area_method": r[21],
                "programme_area_evidence": _json_list(r[22]),
                "programme_area_review_required": bool(r[23]),
                "beneficiary_geography_normalized": _json_list(r[24]),
                "geographic_focus_inferred": _json_list(r[25]),
                "geography_method": r[26],
                "geography_confidence": r[27],
                "geography_evidence": _json_list(r[28]),
                "geography_review_required": bool(r[29]),
                "enrichment_rule_version": r[30],
            })
        currencies = sorted({item["currency"] for item in results})
        stored_coverage = organization_row[0] if organization_row else "unknown"
        source_names = _json_list(organization_row[1]) if organization_row else []
        if results:
            response_status = "available"
            coverage_status = "observed_transactions"
        elif stored_coverage == "organization_level_only":
            response_status = "organization_level_only"
            coverage_status = "organization_level_only"
        else:
            response_status = "no_transactions_found"
            coverage_status = "no_transactions_found"
        return {
            "status": response_status,
            "organization_id": charity_id,
            "role": role_lower if role_lower in {"funder", "recipient"} else "all",
            "transaction_coverage": coverage_status,
            "grant_count": len(results),
            "currencies": currencies,
            "grants": results,
            "metadata": {
                "data_mode": "cached_source",
                "source": sorted({item["source"] for item in results if item["source"]}) or source_names,
                "generated_at": _utc_now(),
                "record_count": len(results),
                "limitations": ["Absence of a transaction does not prove that no grant exists."],
            },
        }

    async def get_sankey_data(
        self, charity_id: int, currency: Optional[str] = None, limit: int = 30
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transaction_coverage, source_names FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        organization_row = cursor.fetchone()
        cursor.execute("""
            SELECT grant_id, funding_charity_id, funding_name, funding_org_source_id,
                   recipient_charity_id, recipient_name, recipient_org_source_id,
                   amount, currency, source
            FROM grants
            WHERE funding_charity_id = ? OR recipient_charity_id = ?
        """, (charity_id, charity_id))
        rows = cursor.fetchall()
        conn.close()

        if not rows and organization_row and organization_row[0] == "organization_level_only":
            return {
                "status": "organization_level_only",
                "nodes": [],
                "links": [],
                "metadata": {
                    "source": _json_list(organization_row[1]), "generated_at": _utc_now(),
                    "grant_count": 0, "included_grant_count": 0,
                    "excluded_grant_count": 0, "excluded_reasons": {},
                    "included_value": 0.0, "currencies": [],
                    "selected_currency": currency, "conversion_method": "none",
                    "filters_applied": {"organization_id": charity_id, "limit": limit},
                    "truncation_applied": False,
                },
            }

        currencies = sorted({str(row[8]).upper() for row in rows if row[8]})
        transaction_sources = sorted({str(row[9]) for row in rows if row[9]})
        selected_currency = currency.upper() if currency else (currencies[0] if len(currencies) == 1 else None)
        excluded_reasons = {}
        if rows and selected_currency is None:
            return {
                "status": "mixed_currency_requires_filter",
                "nodes": [],
                "links": [],
                "metadata": {
                    "source": transaction_sources, "generated_at": _utc_now(),
                    "grant_count": len(rows), "included_grant_count": 0,
                    "excluded_grant_count": len(rows),
                    "excluded_reasons": {"mixed_currency_requires_filter": len(rows)},
                    "included_value": 0.0, "currencies": currencies,
                    "selected_currency": None, "conversion_method": "none",
                    "filters_applied": {"organization_id": charity_id, "limit": limit},
                    "truncation_applied": False,
                },
            }

        nodes_by_id = {}
        aggregated = {}
        for row in rows:
            (_, donor_id, donor_name, donor_source_id, recipient_id, recipient_name,
             recipient_source_id, amount, row_currency, row_source) = row
            normalized_currency = str(row_currency or "").upper()
            if selected_currency and normalized_currency != selected_currency:
                excluded_reasons["currency_filtered"] = excluded_reasons.get("currency_filtered", 0) + 1
                continue
            if amount is None:
                excluded_reasons["missing_amount"] = excluded_reasons.get("missing_amount", 0) + 1
                continue
            if amount <= 0:
                excluded_reasons["non_positive_amount"] = excluded_reasons.get("non_positive_amount", 0) + 1
                continue
            source_id = _stable_party_id(
                "donor", donor_id, donor_source_id, donor_name, source=row_source or "360Giving"
            )
            target_id = _stable_party_id(
                "recipient", recipient_id, recipient_source_id, recipient_name,
                source=row_source or "360Giving",
            )
            if source_id == target_id:
                excluded_reasons["self_link"] = excluded_reasons.get("self_link", 0) + 1
                continue
            nodes_by_id.setdefault(source_id, {
                "id": source_id, "label": donor_name or "Unnamed donor", "role": "donor"
            })
            nodes_by_id.setdefault(target_id, {
                "id": target_id, "label": recipient_name or "Unnamed recipient", "role": "recipient"
            })
            key = (source_id, target_id, normalized_currency)
            aggregate = aggregated.setdefault(key, {"value": 0.0, "grant_count": 0})
            aggregate["value"] += float(amount)
            aggregate["grant_count"] += 1

        links = [
            {
                "source": source, "target": target, "currency": row_currency,
                "value": round(values["value"], 2), "grant_count": values["grant_count"]
            }
            for (source, target, row_currency), values in aggregated.items()
        ]
        links.sort(key=lambda item: item["value"], reverse=True)
        truncation_applied = len(links) > limit
        if truncation_applied:
            removed = links[limit:]
            excluded_reasons["truncated"] = sum(item["grant_count"] for item in removed)
            links = links[:limit]
        retained_node_ids = {item["source"] for item in links} | {item["target"] for item in links}
        nodes = [nodes_by_id[node_id] for node_id in sorted(retained_node_ids)]
        retained_grants = sum(item["grant_count"] for item in links)
        excluded_count = len(rows) - retained_grants
        return {
            "status": "available" if links else "no_transactions_found",
            "nodes": nodes,
            "links": links,
            "metadata": {
                "source": transaction_sources,
                "generated_at": _utc_now(),
                "grant_count": len(rows),
                "included_grant_count": retained_grants,
                "excluded_grant_count": excluded_count,
                "excluded_reasons": excluded_reasons,
                "included_value": round(sum(item["value"] for item in links), 2),
                "currencies": currencies,
                "selected_currency": selected_currency,
                "conversion_method": "none",
                "filters_applied": {"organization_id": charity_id, "limit": limit},
                "truncation_applied": truncation_applied,
            },
        }

    async def get_score(
        self, charity_id: int, target_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        organization = await self.get_by_id(charity_id)
        if not organization:
            raise KeyError(charity_id)
        config = load_score_configuration()
        profile = target_profile or config.example_target_profile
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT annual_expenditure, organization_type FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        row = cursor.fetchone()
        requested_currency = str(profile.get("currency") or "").upper()
        cursor.execute("""
            SELECT UPPER(currency), AVG(amount), COUNT(*)
            FROM grants
            WHERE funding_charity_id = ? AND amount > 0 AND currency IS NOT NULL
            GROUP BY UPPER(currency)
        """, (charity_id,))
        grant_rows = cursor.fetchall()
        conn.close()
        selected = None
        if requested_currency:
            selected = next((item for item in grant_rows if item[0] == requested_currency), None)
        elif len(grant_rows) == 1:
            selected = grant_rows[0]
        score_input = {
            **organization,
            "annual_expenditure": row[0] if row else None,
            "organization_type": row[1] if row else organization.get("organization_type"),
        }
        grant_statistics = {
            "currency": selected[0],
            "average_amount": selected[1],
            "grant_count": selected[2],
        } if selected else {}
        return score_relevance(
            score_input,
            profile,
            grant_statistics=grant_statistics,
            configuration=config,
        )


def get_charity_repository() -> CharityRepository:
    """Prefer a structurally valid SQLite DB and otherwise fall back safely to JSON."""
    is_valid, reason = validate_database(DB_PATH)
    if is_valid:
        return SQLiteCharityRepository()
    if os.path.exists(DB_PATH):
        logger.warning(f"Ignoring unusable SQLite database at {DB_PATH}: {reason}")
    return JSONCharityRepository()
