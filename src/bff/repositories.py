import json
import os
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from bff.config import DATA_PATH
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
            # Sort history by date descending (latest first)
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


# Global repository instance
_repo_instance: Optional[CharityRepository] = None

def get_charity_repository() -> CharityRepository:
    """Dependency provider for the CharityRepository."""
    global _repo_instance
    if _repo_instance is None:
        _repo_instance = JSONCharityRepository()
    return _repo_instance
