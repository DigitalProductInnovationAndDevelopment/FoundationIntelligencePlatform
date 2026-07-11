import logging
import sys

def setup_logging():
    """
    Sets up a standardized logging configuration for the BFF service.
    """
    logger = logging.getLogger("bff")
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if already configured
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger

# Shared logger instance
logger = setup_logging()
