"""Pure organization identity normalization shared by SQLite and PostgreSQL."""

from __future__ import annotations

import re
from typing import Any
import unicodedata


def normalize_organization_name(value: Any) -> str:
    """Return a conservative search-safe organization-name representation."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[\u2010-\u2015/_.,;:()\[\]{}'\"`]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for suffix in (
        "charitable incorporated organisation",
        "charitable incorporated organization",
        "community interest company",
        "limited liability partnership",
        "company limited by guarantee",
        "limited",
        "ltd",
        "plc",
        "cio",
        "cic",
    ):
        if normalized.endswith(f" {suffix}"):
            return normalized[: -len(suffix)].strip()
    return normalized
