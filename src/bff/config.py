"""Application configuration with fail-closed production security defaults."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Mapping, Optional, Tuple
from urllib.parse import urlparse


BFF_DIR = Path(__file__).resolve().parent
SRC_DIR = BFF_DIR.parent
PROJECT_ROOT = SRC_DIR.parent


def _load_local_env() -> None:
    """Load a developer .env without ever using it in staging/production."""
    app_environment = os.environ.get("APP_ENV", "development").strip().lower()
    if app_environment not in {"development", "test"}:
        return
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'\"")


_load_local_env()


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, received {value!r}")


def _as_int(value: Optional[str], default: int, *, minimum: int = 1) -> int:
    parsed = int(value) if value is not None else default
    if parsed < minimum:
        raise ValueError(f"Expected an integer greater than or equal to {minimum}")
    return parsed


def _as_csv(value: Optional[str], default: str = "") -> Tuple[str, ...]:
    raw = value if value is not None else default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class SecurityConfigurationError(RuntimeError):
    """Raised when a runtime would start with an unsafe security posture."""


@dataclass(frozen=True)
class SecuritySettings:
    app_env: str
    auth_mode: str
    oidc_issuer: Optional[str]
    oidc_audience: Optional[str]
    oidc_jwks_url: Optional[str]
    oidc_jwks_json: Optional[str]
    oidc_algorithms: Tuple[str, ...]
    oidc_role_claim: str
    oidc_jwks_cache_seconds: int
    dev_auth_enabled: bool
    dev_auth_username: Optional[str]
    dev_auth_password: Optional[str]
    dev_auth_secret: Optional[str]
    dev_auth_allowed_hosts: Tuple[str, ...]
    access_token_expire_minutes: int
    session_cookie_name: str
    session_cookie_secure: bool
    cors_origins: Tuple[str, ...]
    rate_limit_requests: int
    rate_limit_window_seconds: int
    max_request_body_bytes: int
    request_timeout_seconds: int
    core_proxy_enabled: bool
    core_api_url: str
    core_api_allowed_hosts: Tuple[str, ...]
    core_proxy_allowed_paths: Tuple[str, ...]
    core_proxy_allowed_methods: Tuple[str, ...]
    core_proxy_forward_headers: Tuple[str, ...]
    core_proxy_response_headers: Tuple[str, ...]
    core_api_bearer_token: Optional[str]

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "SecuritySettings":
        env = os.environ if environ is None else environ
        app_env = env.get("APP_ENV", "development").strip().lower()
        return cls(
            app_env=app_env,
            auth_mode=env.get("AUTH_MODE", "disabled").strip().lower(),
            oidc_issuer=env.get("OIDC_ISSUER"),
            oidc_audience=env.get("OIDC_AUDIENCE"),
            oidc_jwks_url=env.get("OIDC_JWKS_URL"),
            oidc_jwks_json=env.get("OIDC_JWKS_JSON"),
            oidc_algorithms=_as_csv(env.get("OIDC_ALGORITHMS"), "RS256"),
            oidc_role_claim=env.get("OIDC_ROLE_CLAIM", "roles").strip(),
            oidc_jwks_cache_seconds=_as_int(env.get("OIDC_JWKS_CACHE_SECONDS"), 300),
            dev_auth_enabled=_as_bool(env.get("DEV_AUTH_ENABLED")),
            dev_auth_username=env.get("DEV_AUTH_USERNAME"),
            dev_auth_password=env.get("DEV_AUTH_PASSWORD"),
            dev_auth_secret=env.get("DEV_AUTH_SECRET"),
            dev_auth_allowed_hosts=_as_csv(
                env.get("DEV_AUTH_ALLOWED_HOSTS"), "127.0.0.1,::1,localhost"
            ),
            access_token_expire_minutes=_as_int(env.get("ACCESS_TOKEN_EXPIRE_MINUTES"), 30),
            session_cookie_name=env.get("SESSION_COOKIE_NAME", "session_id").strip(),
            session_cookie_secure=_as_bool(
                env.get("SESSION_COOKIE_SECURE"), app_env in {"staging", "production"}
            ),
            cors_origins=_as_csv(
                env.get("CORS_ORIGINS"),
                "http://localhost:5173,http://127.0.0.1:5173",
            ),
            rate_limit_requests=_as_int(env.get("RATE_LIMIT_REQUESTS"), 120),
            rate_limit_window_seconds=_as_int(env.get("RATE_LIMIT_WINDOW_SECONDS"), 60),
            max_request_body_bytes=_as_int(env.get("MAX_REQUEST_BODY_BYTES"), 1_048_576),
            request_timeout_seconds=_as_int(env.get("REQUEST_TIMEOUT_SECONDS"), 30),
            core_proxy_enabled=_as_bool(env.get("CORE_PROXY_ENABLED")),
            core_api_url=env.get("CORE_API_URL", "http://127.0.0.1:8080").strip(),
            core_api_allowed_hosts=tuple(
                host.lower() for host in _as_csv(env.get("CORE_API_ALLOWED_HOSTS"))
            ),
            core_proxy_allowed_paths=_as_csv(env.get("CORE_PROXY_ALLOWED_PATHS")),
            core_proxy_allowed_methods=tuple(
                method.upper()
                for method in _as_csv(env.get("CORE_PROXY_ALLOWED_METHODS"), "GET")
            ),
            core_proxy_forward_headers=tuple(
                header.lower()
                for header in _as_csv(
                    env.get("CORE_PROXY_FORWARD_HEADERS"),
                    "accept,content-type,x-request-id",
                )
            ),
            core_proxy_response_headers=tuple(
                header.lower()
                for header in _as_csv(
                    env.get("CORE_PROXY_RESPONSE_HEADERS"),
                    "content-type,cache-control,etag,last-modified",
                )
            ),
            core_api_bearer_token=env.get("CORE_API_BEARER_TOKEN"),
        )


def validate_security_settings(settings: SecuritySettings) -> None:
    errors = []
    production = settings.app_env in {"staging", "production"}

    if settings.auth_mode not in {"disabled", "development", "oidc"}:
        errors.append("AUTH_MODE must be disabled, development or oidc")
    if production and settings.auth_mode != "oidc":
        errors.append("staging/production requires AUTH_MODE=oidc")
    if production and settings.dev_auth_enabled:
        errors.append("development authentication must be disabled outside local/test environments")

    if settings.auth_mode == "development":
        if settings.app_env not in {"development", "test"}:
            errors.append("development authentication is local/test only")
        if not settings.dev_auth_enabled:
            errors.append("AUTH_MODE=development requires DEV_AUTH_ENABLED=true")
        if not all(
            (settings.dev_auth_username, settings.dev_auth_password, settings.dev_auth_secret)
        ):
            errors.append("development authentication credentials must be configured explicitly")
        if settings.dev_auth_secret and len(settings.dev_auth_secret) < 32:
            errors.append("DEV_AUTH_SECRET must contain at least 32 characters")

    if settings.auth_mode == "oidc":
        if not settings.oidc_issuer or not settings.oidc_audience:
            errors.append("OIDC_ISSUER and OIDC_AUDIENCE are required")
        if not settings.oidc_jwks_url and not settings.oidc_jwks_json:
            errors.append("OIDC_JWKS_URL or OIDC_JWKS_JSON is required")
        if not settings.oidc_algorithms or any(
            algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
            for algorithm in settings.oidc_algorithms
        ):
            errors.append("OIDC algorithms must be an explicit asymmetric allowlist")
        if production:
            for name, value in (
                ("OIDC_ISSUER", settings.oidc_issuer),
                ("OIDC_JWKS_URL", settings.oidc_jwks_url),
            ):
                if value and urlparse(value).scheme != "https":
                    errors.append(f"{name} must use HTTPS outside development")

    if production and not settings.session_cookie_secure:
        errors.append("SESSION_COOKIE_SECURE must be true outside development")
    if production and not settings.cors_origins:
        errors.append("CORS_ORIGINS must be explicit outside development")
    if any(origin == "*" for origin in settings.cors_origins):
        errors.append("wildcard CORS origins are not permitted")
    if production and any(urlparse(origin).scheme != "https" for origin in settings.cors_origins):
        errors.append("production CORS origins must use HTTPS")

    if settings.core_proxy_enabled:
        parsed_core_url = urlparse(settings.core_api_url)
        if parsed_core_url.scheme not in {"http", "https"} or not parsed_core_url.hostname:
            errors.append("CORE_API_URL must be an absolute HTTP(S) URL")
        elif parsed_core_url.hostname.lower() not in settings.core_api_allowed_hosts:
            errors.append("CORE_API_URL host must be present in CORE_API_ALLOWED_HOSTS")
        if production and parsed_core_url.scheme != "https":
            errors.append("CORE_API_URL must use HTTPS outside development")
        if not settings.core_proxy_allowed_paths:
            errors.append("enabled proxy requires CORE_PROXY_ALLOWED_PATHS")
        if not settings.core_proxy_allowed_methods:
            errors.append("enabled proxy requires CORE_PROXY_ALLOWED_METHODS")
        if "authorization" in settings.core_proxy_forward_headers or "cookie" in settings.core_proxy_forward_headers:
            errors.append("browser Authorization and Cookie headers cannot be forwarded")

    if settings.oidc_jwks_json:
        try:
            parsed_jwks = json.loads(settings.oidc_jwks_json)
            if not isinstance(parsed_jwks, dict) or not isinstance(parsed_jwks.get("keys"), list):
                errors.append("OIDC_JWKS_JSON must contain a keys array")
        except json.JSONDecodeError:
            errors.append("OIDC_JWKS_JSON must be valid JSON")

    if errors:
        raise SecurityConfigurationError("; ".join(errors))


SECURITY_SETTINGS = SecuritySettings.from_env()

# Data configuration remains local until the PostgreSQL conversion phases.
DEFAULT_DATA_PATH = str(SRC_DIR / "data" / "raw" / "register_of_charities_results.json")
DATA_PATH = os.environ.get("DATA_PATH", DEFAULT_DATA_PATH)
DEFAULT_DB_PATH = str(SRC_DIR / "data" / "charities.db")
DB_PATH = os.environ.get("DB_PATH", DEFAULT_DB_PATH)

# Optional news summarisation. Live use remains approval-gated by the remediation contract.
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
