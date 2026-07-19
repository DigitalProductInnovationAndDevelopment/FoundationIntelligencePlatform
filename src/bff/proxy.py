import httpx
from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from bff.config import CORE_API_URL
from bff.auth import get_current_user_token
from bff.utils.logging import logger

router = APIRouter(
    prefix="/api/core",
    tags=["Downstream Core API Proxy"],
    dependencies=[Depends(get_current_user_token)]
)

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_to_core(
    path: str, 
    request: Request, 
    token: str = Depends(get_current_user_token)
):
    """
    Reverse proxy that forwards requests to downstream core APIs.
    Translates browser cookie session into an Authorization Bearer JWT header.
    """
    # Build downstream URL preserving query string
    query_params = request.url.query
    url = f"{CORE_API_URL.rstrip('/')}/{path}"
    if query_params:
        url = f"{url}?{query_params}"

    # Extract method and body
    method = request.method
    body = await request.body()

    # Construct request headers
    # Copy request headers, ignoring Host and Cookie (so session cookie doesn't leak downstream)
    headers = {}
    for key, value in request.headers.items():
        if key.lower() not in ["host", "cookie", "content-length"]:
            headers[key] = value

    # Translate Cookie into Bearer Token for internal services
    headers["Authorization"] = f"Bearer {token}"

    logger.info(f"BFF Proxying {method} request to downstream target: {url}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            downstream_response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body if method not in ["GET", "HEAD"] else None,
                follow_redirects=True
            )
            
        # Return downstream response to frontend, including status code and headers
        # Exclude transfer-encoding headers that might interfere with HTTP protocols
        response_headers = {
            k: v for k, v in downstream_response.headers.items()
            if k.lower() not in ["content-length", "content-encoding", "transfer-encoding"]
        }
        
        return Response(
            content=downstream_response.content,
            status_code=downstream_response.status_code,
            headers=response_headers,
            media_type=downstream_response.headers.get("content-type")
        )
    except httpx.RequestError as e:
        logger.error(f"Downstream service proxy request failed for {url}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Downstream core service is currently unavailable or timed out."
        )
