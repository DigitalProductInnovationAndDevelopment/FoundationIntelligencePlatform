import logging
import sys

from governance.exposure import redact_text as governance_redact_text

def redact_text(value: object) -> str:
    """Remove credential-shaped values before logs or admin output expose them."""
    return governance_redact_text(value)


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
