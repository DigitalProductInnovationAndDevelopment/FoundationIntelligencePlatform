import json
import os
import sqlite3
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from bff.config import DATA_PATH, DB_PATH
from bff.utils.logging import logger

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
    async def get_grants_map(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_sankey_data(self, charity_id: int) -> Dict[str, Any]:
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
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        filtered = self._data

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

        # Filter by tag (thematic focus)
        if tag:
            tag_lower = tag.lower()
            filtered = [
                c for c in filtered
                if any(tag_lower in t.get("tag", "").lower() for t in c.get("tags_focus", []))
            ]

        # Filter by region
        if region:
            region_lower = region.lower()
            filtered = [
                c for c in filtered
                if any(region_lower in r.lower() for r in c.get("geo_locations", {}).keys())
            ]

        # Filter by size
        if size:
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

        # Map to baseline models
        results = []
        for c in filtered[skip : skip + limit]:
            income, expenditure = self._get_financials(c)
            all_details = c.get("all_details", {})
            results.append({
                "registered_charity_number": c.get("registered_charity_number"),
                "suffix": c.get("suffix", 0),
                "link": c.get("link"),
                "charity_name": all_details.get("charity_name", ""),
                "reg_status": all_details.get("reg_status", "RM"),
                "reporting_status": all_details.get("reporting_status"),
                "removal_reason": all_details.get("removal_reason"),
                "latest_income": income,
                "latest_expenditure": expenditure
            })
        return results

    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        for c in self._data:
            if c.get("registered_charity_number") == reg_charity_number:
                return c
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
            "average_expenditure": avg_exp
        }

    async def get_grants_map(self) -> List[Dict[str, Any]]:
        # Default mock for JSON format
        return [
            {"region": "London", "total_amount_eur": 1200000.0, "grants_count": 45},
            {"region": "North West", "total_amount_eur": 450000.0, "grants_count": 18}
        ]

    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> List[Dict[str, Any]]:
        # Return a simple mock grant structure for testing JSON repo fallback
        return [
            {
                "grant_id": "MOCK-G1",
                "funding_charity_id": charity_id,
                "recipient_name": "Test Recipient",
                "recipient_charity_id": 1002,
                "amount_eur": 15000.0,
                "currency": "GBP",
                "description": "Mock grant for testing JSON fallback",
                "date": "2025-06-01",
                "recipient_region": "London",
                "tags": ["Education"]
            }
        ]

    async def get_sankey_data(self, charity_id: int) -> Dict[str, Any]:
        # Return a simple mock Sankey structure for JSON repo fallback
        return {
            "nodes": [
                {"id": "Grants Received", "label": "Received Grants (360Giving)"},
                {"id": "Other Income", "label": "Other Income & Public Donations"},
                {"id": "Charity", "label": "Mock Charity"},
                {"id": "Expenditure", "label": "Total Expenditure"},
                {"id": "Surplus", "label": "Added to Reserves"},
                {"id": "Grants Awarded", "label": "Grants Made (360Giving)"},
                {"id": "Operating Expenses", "label": "Operating & Other Expenses"}
            ],
            "links": [
                {"source": "Grants Received", "target": "Charity", "value": 15000.0},
                {"source": "Other Income", "target": "Charity", "value": 85000.0},
                {"source": "Charity", "target": "Expenditure", "value": 70000.0},
                {"source": "Charity", "target": "Surplus", "value": 30000.0},
                {"source": "Expenditure", "target": "Grants Awarded", "value": 20000.0},
                {"source": "Expenditure", "target": "Operating Expenses", "value": 50000.0}
            ]
        }


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
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        
        query = """
            SELECT charity_id, name, type, website, email, address, city, state, country, 
                   latitude, longitude, annual_income, annual_expenditure, thematic_focus, 
                   geographic_focus, raw_cc_data 
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
            
        if tag:
            query += " AND thematic_focus LIKE ?"
            params.append(f'%"{tag}"%')
            
        if region:
            query += """ AND (
                state LIKE ? 
                OR city LIKE ? 
                OR address LIKE ? 
                OR geographic_focus LIKE ? 
                OR charity_id IN (
                    SELECT funding_charity_id FROM grants WHERE recipient_region LIKE ?
                )
                OR charity_id IN (
                    SELECT recipient_charity_id FROM grants WHERE recipient_region LIKE ?
                )
            )"""
            region_pat = f"%{region}%"
            params.extend([region_pat, region_pat, region_pat, region_pat, region_pat, region_pat])

        if size == "small":
            query += " AND annual_expenditure < 1000000"
        elif size == "medium":
            query += " AND annual_expenditure >= 1000000 AND annual_expenditure <= 10000000"
        elif size == "large":
            query += " AND annual_expenditure > 10000000"
            
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
                "latest_expenditure": r[12]
            })
        return results

    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT raw_cc_data FROM charities WHERE charity_id = ?", (reg_charity_number,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            return json.loads(row[0])
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
        
        conn.close()
        return {
            "total_charities": total,
            "active_charities": active,
            "removed_charities": removed,
            "average_income": avg_income,
            "average_expenditure": avg_exp
        }

    async def get_grants_map(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT recipient_region, SUM(amount_eur), COUNT(*)
            FROM grants
            WHERE recipient_region IS NOT NULL AND recipient_region != ''
            GROUP BY recipient_region
        """)
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            results.append({
                "region": r[0],
                "total_amount_eur": r[1] or 0.0,
                "grants_count": r[2]
            })
        return results

    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        
        query = """
            SELECT grant_id, funding_charity_id, recipient_name, recipient_charity_id,
                   amount_eur, currency, description, date, recipient_region, tags
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
            tags_list = []
            if r[9]:
                try:
                    tags_list = json.loads(r[9])
                except Exception:
                    tags_list = []
            results.append({
                "grant_id": r[0],
                "funding_charity_id": r[1],
                "recipient_name": r[2],
                "recipient_charity_id": r[3],
                "amount_eur": r[4],
                "currency": r[5],
                "description": r[6],
                "date": r[7],
                "recipient_region": r[8],
                "tags": tags_list
            })
        return results

    async def get_sankey_data(self, charity_id: int) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT name, annual_income, annual_expenditure FROM charities WHERE charity_id = ?", (charity_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"nodes": [], "links": []}
            
        charity_name, annual_income, annual_expenditure = row
        annual_income = annual_income or 0.0
        annual_expenditure = annual_expenditure or 0.0
        
        cursor.execute("SELECT SUM(amount_eur) FROM grants WHERE recipient_charity_id = ?", (charity_id,))
        sum_received = cursor.fetchone()[0] or 0.0
        
        cursor.execute("SELECT SUM(amount_eur) FROM grants WHERE funding_charity_id = ?", (charity_id,))
        sum_made = cursor.fetchone()[0] or 0.0
        
        conn.close()
        
        nodes = [
            {"id": "Grants Received", "label": "Received Grants (360Giving)"},
            {"id": "Other Income", "label": "Other Income & Public Donations"},
            {"id": "Charity", "label": charity_name},
            {"id": "Expenditure", "label": "Total Expenditure"},
            {"id": "Grants Awarded", "label": "Grants Made (360Giving)"},
            {"id": "Operating Expenses", "label": "Operating & Other Expenses"}
        ]
        
        links = []
        
        actual_rec = min(sum_received, annual_income)
        other_inc = max(0.0, annual_income - actual_rec)
        
        if actual_rec > 0:
            links.append({"source": "Grants Received", "target": "Charity", "value": round(actual_rec, 2)})
        if other_inc > 0:
            links.append({"source": "Other Income", "target": "Charity", "value": round(other_inc, 2)})
            
        if annual_income < annual_expenditure:
            drawdown = annual_expenditure - annual_income
            nodes.append({"id": "Reserves Drawdown", "label": "Drawdown from Reserves"})
            links.append({"source": "Reserves Drawdown", "target": "Charity", "value": round(drawdown, 2)})
            links.append({"source": "Charity", "target": "Expenditure", "value": round(annual_expenditure, 2)})
        else:
            surplus = annual_income - annual_expenditure
            if surplus > 0:
                nodes.append({"id": "Reserves Surplus", "label": "Added to Reserves"})
                links.append({"source": "Charity", "target": "Reserves Surplus", "value": round(surplus, 2)})
            if annual_expenditure > 0:
                links.append({"source": "Charity", "target": "Expenditure", "value": round(annual_expenditure, 2)})
                
        actual_made = min(sum_made, annual_expenditure)
        operating_exp = max(0.0, annual_expenditure - actual_made)
        
        if actual_made > 0:
            links.append({"source": "Expenditure", "target": "Grants Awarded", "value": round(actual_made, 2)})
        if operating_exp > 0:
            links.append({"source": "Expenditure", "target": "Operating Expenses", "value": round(operating_exp, 2)})
            
        return {"nodes": nodes, "links": links}


# Global repository instance
_repo_instance: Optional[CharityRepository] = None

def get_charity_repository() -> CharityRepository:
    """Dependency provider for the CharityRepository. Prefers SQLite if DB exists, falls back to JSON."""
    global _repo_instance
    if _repo_instance is None:
        if os.path.exists(DB_PATH):
            _repo_instance = SQLiteCharityRepository()
        else:
            _repo_instance = JSONCharityRepository()
    return _repo_instance
