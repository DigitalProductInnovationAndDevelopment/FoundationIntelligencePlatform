"""Predicates distinguishing informative source values from placeholders.

Source records frequently carry filler text — ``n/a``, ``unknown``, ``-``, empty
markup — where a value is absent. Treating those strings as data silently manufactures
facts, so consolidation and enrichment route candidate values through these checks
first.

The distinction the callers depend on: a placeholder means *the source told us nothing*,
which must stay explicitly absent rather than becoming an empty-but-present value.
"""

import re


_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:"
    r"n/?a|"
    r"unknown|"
    r"not\s+available|"
    r"not\s+publicly\s+available|"
    r"not\s+published|"
    r"not\s+specified|"
    r"not\s+publicly\s+specified|"
    r"not\s+disclosed|"
    r"not\s+publicly\s+disclosed|"
    r"not\s+applicable|"
    r"data\s+not\s+available|"
    r"data\s+not\s+publicly\s+available|"
    r"information\s+not\s+available|"
    r"information\s+not\s+publicly\s+available"
    r")(?:\s|$|[().,:;-])",
    re.IGNORECASE,
)


def has_technical_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def is_placeholder_value(value):
    if not isinstance(value, str):
        return False
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def is_informative_value(value):
    return has_technical_value(value) and not is_placeholder_value(value)
