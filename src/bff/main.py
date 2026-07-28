import asyncio
from contextlib import asynccontextmanager
import re
import time
import uuid
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from bff.auth import router as auth_router
from bff.proxy import router as proxy_router
from bff.admin import router as admin_router
from bff.news import router as news_router
from bff.audit import StructuredLogAuditSink, event_from_request
from bff.config import SECURITY_SETTINGS, validate_security_settings
from bff.database import DatabaseManager, DatabaseSettings
from bff.security import IdempotencyStore, SlidingWindowRateLimiter
from bff.utils.logging import logger


POSTGRESQL_ONLY_RUNTIME = SECURITY_SETTINGS.app_env in {"staging", "production"}
if POSTGRESQL_ONLY_RUNTIME:
    from bff.postgres.routes import router as charity_router
else:
    # The legacy SQLite repository is restricted to development/test while the
    # remaining domain journeys are ported in Phase 5.
    from bff.charity import router as charity_router
    from bff.repositories import get_charity_repository


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Validate security before accepting traffic, then initialize repository state."""
    validate_security_settings(application.state.security_settings)
    application.state.database = DatabaseManager(DatabaseSettings.from_env())
    if POSTGRESQL_ONLY_RUNTIME:
        # Constructing the pool remains lazy; readiness owns the first bounded
        # connection and all production repositories use the same manager.
        application.state.database.sessions()
        logger.info("PostgreSQL repository runtime initialized.")
    else:
        get_charity_repository()
        logger.info(
            "Development/test legacy repository initialized; expensive Overview "
            "aggregation is request-driven."
        )
    try:
        yield
    finally:
        await application.state.database.close()


app = FastAPI(
    title="Foundation Intelligence Platform BFF API",
    description=(
        "Backend for the Foundation Intelligence Platform. It serves normalized organization, "
        "grant, provenance, enrichment, and experimental relevance-score data to the dashboard."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.state.security_settings = SECURITY_SETTINGS
app.state.rate_limiter = SlidingWindowRateLimiter(
    SECURITY_SETTINGS.rate_limit_requests,
    SECURITY_SETTINGS.rate_limit_window_seconds,
)
app.state.idempotency_store = IdempotencyStore()
app.state.audit_sink = StructuredLogAuditSink()
app.state.database = DatabaseManager(DatabaseSettings.from_env())

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(SECURITY_SETTINGS.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Action-Reason",
        "X-Request-ID",
    ],
)


# Request safety, audit and request-response logging middleware.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    supplied_request_id = request.headers.get("x-request-id", "")
    request.state.request_id = (
        supplied_request_id
        if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
    settings = app.state.security_settings

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        content_length = request.headers.get("content-length")
        try:
            declared_size = int(content_length) if content_length else 0
        except ValueError:
            declared_size = settings.max_request_body_bytes + 1
        if declared_size > settings.max_request_body_bytes:
            response = JSONResponse(
                status_code=413,
                content={"detail": "Request body exceeds the configured limit."},
            )
        else:
            body = await request.body()
            if len(body) > settings.max_request_body_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body exceeds the configured limit."},
                )
            else:
                try:
                    response = await asyncio.wait_for(
                        call_next(request),
                        timeout=settings.request_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    response = JSONResponse(
                        status_code=504,
                        content={"detail": "Request processing timed out."},
                    )
    else:
        try:
            response = await asyncio.wait_for(
                call_next(request),
                timeout=settings.request_timeout_seconds,
            )
        except asyncio.TimeoutError:
            response = JSONResponse(
                status_code=504,
                content={"detail": "Request processing timed out."},
            )

    response.headers["X-Request-ID"] = request.state.request_id
    record_key = getattr(request.state, "idempotency_record_key", None)
    if record_key:
        if response.status_code < 400 or response.status_code >= 500:
            app.state.idempotency_store.complete(record_key)
        else:
            app.state.idempotency_store.release(record_key)
    if hasattr(request.state, "audit_action"):
        error_class = None if response.status_code < 400 else f"http_{response.status_code}"
        app.state.audit_sink.record(event_from_request(request, response.status_code, error_class))

    duration = time.time() - start_time
    logger.info(
        "Request: %s %s | Request-ID: %s | Status: %s | Duration: %.4fs",
        request.method,
        request.url.path,
        request.state.request_id,
        response.status_code,
        duration,
    )
    return response

# Custom centralized exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on path %s; class=%s",
        request.url.path,
        exc.__class__.__name__,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please try again later."}
    )

# Include API routers
app.include_router(auth_router)
app.include_router(charity_router)
app.include_router(proxy_router)
app.include_router(admin_router)
app.include_router(news_router)

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root access to Swagger interactive documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Health Check"])
async def health_check():
    """Backward-compatible liveness endpoint."""
    return {"status": "healthy", "service": "bff"}


@app.get("/health/live", tags=["Health Check"])
async def liveness_check():
    """Report process liveness without checking external dependencies."""
    return {"status": "healthy", "service": "bff"}


@app.get("/health/ready", tags=["Health Check"])
async def readiness_check():
    """Accept traffic only while the configured PostgreSQL database responds."""
    if await app.state.database.check():
        return {"status": "ready", "checks": {"postgresql": "healthy"}}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "checks": {"postgresql": "unavailable"}},
    )
