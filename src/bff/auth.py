"""Authentication endpoints.

Production identities are validated from OIDC bearer tokens. The password endpoint
exists only when the local/test development mode is explicitly enabled.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from bff.config import SECURITY_SETTINGS
from bff.schemas import UserLogin
from bff.security import (
    Principal,
    Role,
    create_development_access_token,
    enforce_login_rate_limit,
    require_roles,
    validate_development_credentials,
)


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    credentials: UserLogin,
    _: None = Depends(enforce_login_rate_limit),
):
    """Issue a local-only development session; absent from the production auth path."""
    request.state.audit_action = "auth.development_login"
    request.state.audit_target = "/api/auth/login"
    settings = validate_development_credentials(
        request,
        credentials.username,
        credentials.password,
    )
    token = create_development_access_token(credentials.username, settings=settings)
    request.state.principal = Principal(
        actor_id=credentials.username,
        roles=frozenset({Role.ADMINISTRATOR}),
        claims={"sub": credentials.username, "roles": [Role.ADMINISTRATOR.value]},
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        max_age=settings.access_token_expire_minutes * 60,
        samesite="strict",
        secure=settings.session_cookie_secure,
        path="/api",
    )
    return {"message": "Successfully logged in"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(
        require_roles(Role.VIEWER, action="auth.logout")
    ),
):
    """Clear the development session cookie; OIDC logout is owned by the IdP."""
    del principal
    settings = getattr(request.app.state, "security_settings", SECURITY_SETTINGS)
    response.delete_cookie(
        key=settings.session_cookie_name,
        samesite="strict",
        secure=settings.session_cookie_secure,
        path="/api",
    )
    return {"message": "Successfully logged out"}
__all__ = ["router"]
