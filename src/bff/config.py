import os

# JWT Configuration
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "bff_super_secret_signing_key_for_session_tokens_2026")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# Downstream Service Configuration
CORE_API_URL = os.environ.get("CORE_API_URL", "http://localhost:8080")

# Data File Configuration
# By default, look for the results relative to the project root
BFF_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(BFF_DIR)
DEFAULT_DATA_PATH = os.path.join(SRC_DIR, "data", "raw", "register_of_charities_results.json")
DATA_PATH = os.environ.get("DATA_PATH", DEFAULT_DATA_PATH)

# Basic Mock Authentication credentials
BFF_ADMIN_USER = os.environ.get("BFF_ADMIN_USER", "admin")
BFF_ADMIN_PASSWORD = os.environ.get("BFF_ADMIN_PASSWORD", "password")
