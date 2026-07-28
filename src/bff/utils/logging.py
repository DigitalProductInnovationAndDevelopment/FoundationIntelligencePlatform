import logging
import re
import sys


_REDACTION_PATTERNS = (
    re.compile(r'''(?i)(["']?authorization["']?\s*[:=]\s*["']?(?:bearer\s+)?)[^"'\s,;]+'''),
    re.compile(
        r'''(?i)(["']?(?:password|passwd|token|secret|api[_-]?key)["']?\s*[:=]\s*["']?)[^"'\s,;]+'''
    ),
    re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def redact_text(value: object) -> str:
    """Remove credential-shaped values before logs or admin output expose them."""
    redacted = str(value)
    for pattern in _REDACTION_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(r"\1[REDACTED]\2", redacted)
        elif pattern.groups == 1:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    def formatException(self, exception_info) -> str:
        return redact_text(super().formatException(exception_info))


def setup_logging():
    """
    Sets up a standardized logging configuration for the BFF service.
    """
    logger = logging.getLogger("bff")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    
    # Avoid duplicate handlers if already configured
    if not logger.handlers:
        formatter = RedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
        
    return logger

# Shared logger instance
logger = setup_logging()
