"""Explicit serializer allowlists and recursive sensitive-data redaction."""

from __future__ import annotations

import re
from typing import Any, Mapping

from governance.retention import GovernanceConfiguration


_CREDENTIAL_PATTERNS = (
    re.compile(r'''(?i)(["']?authorization["']?\s*[:=]\s*["']?(?:bearer\s+)?)[^"'\s,;]+'''),
    re.compile(
        r'''(?i)(["']?(?:password|passwd|token|secret|api[_-]?key)["']?\s*[:=]\s*["']?)[^"'\s,;]+'''
    ),
    re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)
_EMAIL_PATTERN = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])")


def redact_text(value: object, replacement: str = "[REDACTED]") -> str:
    redacted = str(value)
    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(rf"\1{replacement}\2", redacted)
        elif pattern.groups == 1:
            redacted = pattern.sub(rf"\1{replacement}", redacted)
        else:
            redacted = pattern.sub(replacement, redacted)
    return _EMAIL_PATTERN.sub(replacement, redacted)


def redact_data(
    value: Any,
    *,
    sensitive_keys: set[str],
    replacement: str = "[REDACTED]",
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                replacement
                if str(key).casefold() in sensitive_keys
                else redact_data(
                    item,
                    sensitive_keys=sensitive_keys,
                    replacement=replacement,
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            redact_data(item, sensitive_keys=sensitive_keys, replacement=replacement)
            for item in value
        ]
    if isinstance(value, str):
        return redact_text(value, replacement)
    return value


def redact_for_logs(value: Any, configuration: GovernanceConfiguration) -> Any:
    keys = {
        str(key).casefold()
        for key in configuration.log_redaction.get("sensitive_keys", ())
    }
    replacement = str(configuration.log_redaction.get("replacement", "[REDACTED]"))
    return redact_data(value, sensitive_keys=keys, replacement=replacement)


def serialize_exposed_fields(
    record: Mapping[str, Any],
    *,
    policy_name: str,
    configuration: GovernanceConfiguration,
) -> dict[str, Any]:
    fields = configuration.field_exposure_policies.get(policy_name)
    if fields is None:
        raise ValueError(f"Unknown field exposure policy: {policy_name}")
    return {
        field: record[field]
        for field in fields
        if field in record
    }
