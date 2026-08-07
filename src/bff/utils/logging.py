from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import sys

from governance.exposure import redact_for_logs, redact_text as governance_redact_text
from governance.retention import load_governance_configuration


GOVERNANCE_CONFIGURATION = load_governance_configuration()
SERVICE_NAME = "foundation-intelligence-api"
STRUCTURED_FIELDS = (
    "request_id",
    "trace_id",
    "job_id",
    "migration_run_id",
    "dataset_version",
    "schema_version",
    "source",
    "actor_id",
    "role",
    "operation",
    "duration_ms",
    "status",
    "error_class",
    "record_count",
    "accepted_count",
    "rejected_count",
    "quarantined_count",
    "retry_count",
)

def redact_text(value: object) -> str:
    """Remove credential-shaped values before logs or admin output expose them."""
    return governance_redact_text(value)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Compatibility formatter for callers that still need plain-text output."""

    def formatException(self, exception_info) -> str:
        return redact_text(super().formatException(exception_info))


def pseudonymous_actor_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "unknown"
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": SERVICE_NAME,
            "environment": os.environ.get("APP_ENV", "development").strip().lower(),
            "message": record.getMessage(),
        }
        for field in STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and "error_class" not in payload:
            payload["error_class"] = record.exc_info[0].__name__
        redacted = redact_for_logs(payload, GOVERNANCE_CONFIGURATION)
        return json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)


def setup_logging():
    """
    Sets up a standardized logging configuration for the BFF service.
    """
    logger = logging.getLogger("bff")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    
    # Avoid duplicate handlers if already configured
    if not logger.handlers:
        formatter = JsonFormatter()
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
        
    return logger

# Shared logger instance
logger = setup_logging()
