"""Narrow, opt-in downstream proxy with fixed destination and allowlists."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bff.config import SECURITY_SETTINGS
from bff.security import Principal, Role, require_roles, reserve_idempotency
from bff.utils.logging import logger


router = APIRouter(
    prefix="/api/core",
    tags=["Downstream Core API Proxy"],
)


def _normalized_path(path: str) -> str:
    if not path or "\\" in path or "//" in path:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Proxy path is not allowed.")
    parts = PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Proxy path is not allowed.")
    return "/".join(parts)


def _path_allowed(path: str, allowlist) -> bool:
    for rule in allowlist:
        normalized_rule = rule.strip().strip("/")
        if normalized_rule.endswith("/*"):
            prefix = normalized_rule[:-2]
            if path == prefix or path.startswith(f"{prefix}/"):
                return True
        elif path == normalized_rule:
            return True
    return False


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def proxy_to_core(
    path: str,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMINISTRATOR, action="proxy.request")),
):
    settings = getattr(request.app.state, "security_settings", SECURITY_SETTINGS)
    if not settings.core_proxy_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    method = request.method.upper()
    if method not in settings.core_proxy_allowed_methods:
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Proxy method is not allowed.")
    if method not in {"GET", "HEAD", "OPTIONS"}:
        await reserve_idempotency(request, principal, "proxy.request")

    safe_path = _normalized_path(path)
    if not _path_allowed(safe_path, settings.core_proxy_allowed_paths):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Proxy path is not allowed.")

    parsed_base = urlparse(settings.core_api_url)
    if not parsed_base.hostname or parsed_base.hostname.lower() not in settings.core_api_allowed_hosts:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Downstream proxy destination is not configured safely.",
        )
    url = f"{settings.core_api_url.rstrip('/')}/{quote(safe_path, safe='/-._~')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        key.lower(): value
        for key, value in request.headers.items()
        if key.lower() in settings.core_proxy_forward_headers
        and key.lower() not in {"authorization", "cookie", "host", "content-length"}
    }
    headers["x-request-id"] = request.state.request_id
    if settings.core_api_bearer_token:
        headers["authorization"] = f"Bearer {settings.core_api_bearer_token}"

    body = await request.body()
    logger.info("Proxy request: method=%s path=%s", method, safe_path)
    try:
        async with httpx.AsyncClient(
            timeout=float(settings.request_timeout_seconds),
            follow_redirects=False,
        ) as client:
            downstream_response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body if method not in {"GET", "HEAD"} else None,
            )
    except httpx.RequestError as exc:
        logger.error("Downstream proxy request failed; class=%s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Downstream core service is currently unavailable or timed out.",
        ) from exc

    response_headers = {
        key: value
        for key, value in downstream_response.headers.items()
        if key.lower() in settings.core_proxy_response_headers
    }
    return Response(
        content=downstream_response.content,
        status_code=downstream_response.status_code,
        headers=response_headers,
        media_type=downstream_response.headers.get("content-type"),
    )
