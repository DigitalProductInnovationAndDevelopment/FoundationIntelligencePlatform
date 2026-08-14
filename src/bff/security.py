"""OIDC authentication, role authorization and request abuse controls."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import inspect
import json
import threading
import time
from typing import Any, Deque, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

import httpx
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from bff.config import SECURITY_SETTINGS, SecuritySettings
from bff.utils.logging import logger


class Role(str, Enum):
    """Application roles, ordered so a higher role satisfies lower-role reads."""
    VIEWER = "viewer"
    ANALYST = "analyst"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"


_ROLE_RANK = {
    Role.VIEWER: 10,
    Role.ANALYST: 20,
    Role.OPERATOR: 30,
    Role.ADMINISTRATOR: 40,
}


@dataclass(frozen=True)
class Principal:
    """The authenticated actor, its roles and its verified token claims."""
    actor_id: str
    roles: FrozenSet[Role]
    claims: Mapping[str, Any]

    @property
    def primary_role(self) -> str:
        """Return the highest role held by this principal."""
        if not self.roles:
            return "unassigned"
        return max(self.roles, key=lambda role: _ROLE_RANK[role]).value

    def permits_any(self, required_roles: Iterable[Role]) -> bool:
        """Report whether this principal satisfies any of the required roles."""
        required = tuple(required_roles)
        if not required:
            return True
        highest_grant = max((_ROLE_RANK[role] for role in self.roles), default=0)
        return any(highest_grant >= _ROLE_RANK[role] for role in required)


class SlidingWindowRateLimiter:
    """Process-local protection; the AWS edge layer supplies distributed limiting."""

    def __init__(self, limit: int, window_seconds: int):
        """Create a per-key sliding-window limiter with the configured bounds."""
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, now: Optional[float] = None) -> Optional[int]:
        """Return seconds to wait when a key is over its limit, otherwise None."""
        timestamp = time.monotonic() if now is None else now
        oldest_allowed = timestamp - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (timestamp - events[0])))
                return retry_after
            events.append(timestamp)
        return None

    def clear(self) -> None:
        """Discard all recorded request timestamps."""
        with self._lock:
            self._events.clear()


@dataclass
class _IdempotencyRecord:
    """One reserved idempotency key with its request fingerprint and state."""
    fingerprint: str
    state: str
    created_at: float


class IdempotencyStore:
    """Thread-safe local/test at-most-once guard."""

    def __init__(self, ttl_seconds: int = 86_400):
        """Create an in-process idempotency store for single-instance runtimes."""
        self.ttl_seconds = ttl_seconds
        self._records: Dict[Tuple[str, str, str], _IdempotencyRecord] = {}
        self._lock = threading.Lock()

    def reserve(self, actor_id: str, action: str, key: str, fingerprint: str) -> Tuple[str, str, str]:
        """Reserve a key, raising IdempotencyConflict on a differing replay."""
        record_key = (actor_id, action, key)
        now = time.monotonic()
        with self._lock:
            expired = [
                candidate
                for candidate, record in self._records.items()
                if now - record.created_at > self.ttl_seconds
            ]
            for candidate in expired:
                del self._records[candidate]
            existing = self._records.get(record_key)
            if existing:
                raise IdempotencyConflict(
                    different_request=existing.fingerprint != fingerprint
                )
            self._records[record_key] = _IdempotencyRecord(fingerprint, "reserved", now)
        return record_key

    def complete(self, record_key: Tuple[str, str, str]) -> None:
        """Mark a reservation complete and retain its outcome."""
        with self._lock:
            record = self._records.get(record_key)
            if record:
                record.state = "completed"

    def release(self, record_key: Tuple[str, str, str]) -> None:
        """Release a reservation so a failed request can be retried."""
        with self._lock:
            record = self._records.get(record_key)
            if record and record.state == "reserved":
                del self._records[record_key]

    def clear(self) -> None:
        """Discard all reservations."""
        with self._lock:
            self._records.clear()


class IdempotencyConflict(RuntimeError):
    """A durable or local key already exists for the same actor/action."""

    def __init__(self, *, different_request: bool):
        """Record the conflicting key and its existing reservation state."""
        self.different_request = different_request
        super().__init__(
            "Idempotency-Key was already used with a different request."
            if different_request
            else "Idempotency-Key has already been processed."
        )


class OIDCVerifier:
    """Validates OIDC bearer tokens against a cached JWKS."""
    def __init__(self):
        """Create a verifier bound to the configured issuer, audience and key source."""
        self._jwks: Optional[Mapping[str, Any]] = None
        self._loaded_at = 0.0
        self._cache_source: Optional[str] = None
        self._lock = threading.Lock()

    async def _load_jwks(self, settings: SecuritySettings) -> Mapping[str, Any]:
        """Fetch or reuse the cached JWKS, honouring the configured cache lifetime."""
        now = time.monotonic()
        cache_source = settings.oidc_jwks_json or str(settings.oidc_jwks_url)
        with self._lock:
            if (
                self._jwks
                and self._cache_source == cache_source
                and now - self._loaded_at < settings.oidc_jwks_cache_seconds
            ):
                return self._jwks

        if settings.oidc_jwks_json:
            payload = json.loads(settings.oidc_jwks_json)
        else:
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                    response = await client.get(str(settings.oidc_jwks_url))
                    response.raise_for_status()
                    if len(response.content) > 256_000:
                        raise ValueError("OIDC JWKS response exceeds the configured safety bound")
                    payload = response.json()
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("OIDC key retrieval failed: %s", exc.__class__.__name__)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Identity provider keys are temporarily unavailable.",
                ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity provider key set is invalid.",
            )
        with self._lock:
            self._jwks = payload
            self._loaded_at = now
            self._cache_source = cache_source
        return payload

    async def decode(self, token: str, settings: SecuritySettings) -> Mapping[str, Any]:
        """Verify signature, issuer, audience and expiry, returning the claims."""
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise _invalid_token() from exc
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in settings.oidc_algorithms or not key_id:
            raise _invalid_token()
        jwks = await self._load_jwks(settings)
        matching_keys = [key for key in jwks["keys"] if key.get("kid") == key_id]
        if len(matching_keys) != 1:
            raise _invalid_token()
        try:
            return jwt.decode(
                token,
                matching_keys[0],
                algorithms=list(settings.oidc_algorithms),
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                options={"require_exp": True, "require_iat": True, "require_sub": True},
            )
        except JWTError as exc:
            logger.warning("OIDC token validation failed: %s", exc.__class__.__name__)
            raise _invalid_token() from exc


_oidc_verifier = OIDCVerifier()


def _invalid_token() -> HTTPException:
    """Build the standard 401 response for an unusable token."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Access token is invalid or expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _request_host(request: Request) -> str:
    """Return the request's host, used for development-auth host allowlisting."""
    return request.client.host.lower() if request.client and request.client.host else "unknown"


def _extract_token(request: Request, settings: SecuritySettings) -> Optional[str]:
    """Take the bearer token or session cookie from the request, if present."""
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credentials.strip():
            raise _invalid_token()
        return credentials.strip()
    if settings.auth_mode == "development":
        return request.cookies.get(settings.session_cookie_name)
    return None


def _roles_from_claims(claims: Mapping[str, Any], claim_name: str) -> FrozenSet[Role]:
    """Map the configured role claim onto known application roles."""
    raw_roles = claims.get(claim_name, [])
    if isinstance(raw_roles, str):
        candidates = raw_roles.replace(",", " ").split()
    elif isinstance(raw_roles, (list, tuple, set)):
        candidates = [str(role) for role in raw_roles]
    else:
        candidates = []
    normalized = set()
    aliases = {"admin": Role.ADMINISTRATOR, "administrator": Role.ADMINISTRATOR}
    for candidate in candidates:
        value = candidate.strip().lower()
        try:
            normalized.add(Role(value))
        except ValueError:
            if value in aliases:
                normalized.add(aliases[value])
    return frozenset(normalized)


def create_development_access_token(
    subject: str,
    roles: Iterable[Role] = (Role.ADMINISTRATOR,),
    *,
    settings: SecuritySettings = SECURITY_SETTINGS,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Sign a local-only development session token."""
    if settings.auth_mode != "development" or not settings.dev_auth_enabled or not settings.dev_auth_secret:
        raise RuntimeError("Development authentication is not explicitly enabled")
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "roles": [role.value for role in roles],
        "iss": "foundation-intelligence-development",
        "aud": "foundation-intelligence-api",
        "iat": now,
        "exp": now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes)),
        "token_use": "development",
    }
    return jwt.encode(claims, settings.dev_auth_secret, algorithm="HS256")


async def _decode_token(token: str, request: Request, settings: SecuritySettings) -> Mapping[str, Any]:
    """Decode a token using OIDC verification or the development signing key."""
    if settings.auth_mode == "development":
        if _request_host(request) not in {host.lower() for host in settings.dev_auth_allowed_hosts}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Development authentication is restricted to explicitly allowed local hosts.",
            )
        try:
            return jwt.decode(
                token,
                str(settings.dev_auth_secret),
                algorithms=["HS256"],
                issuer="foundation-intelligence-development",
                audience="foundation-intelligence-api",
                options={"require_exp": True, "require_iat": True, "require_sub": True},
            )
        except JWTError as exc:
            raise _invalid_token() from exc
    if settings.auth_mode == "oidc":
        return await _oidc_verifier.decode(token, settings)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is not configured.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _limiter_for(request: Request, settings: SecuritySettings) -> SlidingWindowRateLimiter:
    """Return the rate limiter bound to this application instance."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = SlidingWindowRateLimiter(
            settings.rate_limit_requests,
            settings.rate_limit_window_seconds,
        )
        request.app.state.rate_limiter = limiter
    return limiter


def _idempotency_store_for(request: Request) -> IdempotencyStore:
    """Return the idempotency store bound to this application instance."""
    store = getattr(request.app.state, "idempotency_store", None)
    if store is None:
        store = IdempotencyStore()
        request.app.state.idempotency_store = store
    return store


async def authenticate_request(request: Request) -> Principal:
    """Resolve the request's principal, or reject it. There is no anonymous path."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    settings = getattr(request.app.state, "security_settings", SECURITY_SETTINGS)
    token = _extract_token(request, settings)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A bearer token or secure session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = await _decode_token(token, request, settings)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise _invalid_token()
    principal = Principal(
        actor_id=subject.strip(),
        roles=_roles_from_claims(claims, settings.oidc_role_claim),
        claims=claims,
    )
    retry_after = _limiter_for(request, settings).check(f"actor:{principal.actor_id}")
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Request rate limit exceeded.",
            headers={"Retry-After": str(retry_after)},
        )
    request.state.principal = principal
    return principal


async def reserve_idempotency(request: Request, principal: Principal, action: str) -> None:
    """Require and durably reserve an Idempotency-Key for a mutating request."""
    key = request.headers.get("idempotency-key", "").strip()
    if not key or len(key) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid Idempotency-Key header is required for this action.",
        )
    body = await request.body()
    fingerprint = hashlib.sha256(
        request.method.encode("ascii") + b"\0" + request.url.path.encode("utf-8") + b"\0" + body
    ).hexdigest()
    try:
        record_key = _idempotency_store_for(request).reserve(
            principal.actor_id,
            action,
            key,
            fingerprint,
        )
        if inspect.isawaitable(record_key):
            record_key = await record_key
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    request.state.idempotency_record_key = record_key


def require_roles(*roles: Role, action: str = "authenticated.read", idempotent: bool = False):
    """Create a dependency that classifies and authorizes one API action."""

    async def dependency(request: Request) -> Principal:
        """Authenticate, authorize and optionally reserve idempotency for one action."""
        request.state.audit_action = action
        request.state.audit_target = request.url.path
        principal = await authenticate_request(request)
        if not principal.permits_any(roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The authenticated role is not permitted to perform this action.",
            )
        if idempotent:
            await reserve_idempotency(request, principal, action)
        return principal

    return dependency


async def enforce_login_rate_limit(request: Request) -> None:
    """Apply the per-host rate limit to development login attempts."""
    settings = getattr(request.app.state, "security_settings", SECURITY_SETTINGS)
    retry_after = _limiter_for(request, settings).check(f"login:{_request_host(request)}")
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Authentication rate limit exceeded.",
            headers={"Retry-After": str(retry_after)},
        )


def validate_development_credentials(
    request: Request,
    username: str,
    password: str,
) -> SecuritySettings:
    """Validate development credentials, returning 404 when the mode is disabled."""
    settings = getattr(request.app.state, "security_settings", SECURITY_SETTINGS)
    allowed_hosts = {host.lower() for host in settings.dev_auth_allowed_hosts}
    if (
        settings.auth_mode != "development"
        or not settings.dev_auth_enabled
        or _request_host(request) not in allowed_hosts
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    valid = hmac.compare_digest(username, settings.dev_auth_username or "") and hmac.compare_digest(
        password,
        settings.dev_auth_password or "",
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect credentials")
    return settings
