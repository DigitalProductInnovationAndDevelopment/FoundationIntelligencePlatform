import asyncio
from contextlib import asynccontextmanager
import inspect
import re
import time
import uuid
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from bff.auth import router as auth_router
from bff.proxy import router as proxy_router
from bff.news import router as news_router
from bff.user_management import router as user_management_router
from bff.audit import StructuredLogAuditSink, event_from_request
from bff.config import SECURITY_SETTINGS, validate_security_settings
from bff.database import DatabaseManager, DatabaseSettings
from bff.security import IdempotencyStore, SlidingWindowRateLimiter
from bff.utils.logging import logger, pseudonymous_actor_id
from observability.metrics import MetricsRegistry, load_observability_configuration
from transition.runtime import load_transition_settings
from transition.shadow import (
    ComparisonPolicy,
    ShadowComparisonCoordinator,
    ShadowComparisonMiddleware,
    StructuredLogEvidenceSink,
)


TRANSITION_SETTINGS = load_transition_settings()
POSTGRESQL_ONLY_RUNTIME = TRANSITION_SETTINGS.postgresql_authoritative
if POSTGRESQL_ONLY_RUNTIME:
    from bff.postgres.admin_routes import router as admin_router
    from bff.postgres.audit_repository import PostgresAuditSink
    from bff.postgres.base import ANALYTICS_CACHE
    from bff.postgres.governance_repository import GovernanceRepository
    from bff.postgres.governance_routes import router as governance_router
    from bff.postgres.idempotency_repository import PostgresIdempotencyStore
    from bff.postgres.observability_routes import router as observability_router
    from bff.postgres.pipeline_repository import PipelineRepository
    from bff.postgres.routes import router as charity_router
    from bff.postgres.scraper_routes import router as scraper_router
    from governance.retention import load_governance_configuration
    from pipelines.durable import load_source_configurations
else:
    # The legacy SQLite repository is restricted to development/test while the
    # remaining domain journeys are ported in Phase 5.
    from bff.charity import router as charity_router
    from bff.admin import router as admin_router
    from bff.repositories import get_charity_repository


def _shadow_log(payload):
    logger.info("shadow_comparison", extra={"shadow": payload})


if TRANSITION_SETTINGS.shadow_enabled:
    from transition.sqlite_source import SQLiteShadowReader, resolve_shadow_journey

    SHADOW_COORDINATOR = ShadowComparisonCoordinator(
        SQLiteShadowReader(str(TRANSITION_SETTINGS.shadow_sqlite_path)),
        StructuredLogEvidenceSink(_shadow_log),
        policy=ComparisonPolicy(
            unordered_paths=frozenset(TRANSITION_SETTINGS.approved_unordered_paths),
            ignored_paths=frozenset(TRANSITION_SETTINGS.ignored_operational_paths),
            maximum_differences=TRANSITION_SETTINGS.maximum_recorded_differences,
        ),
        timeout_seconds=TRANSITION_SETTINGS.shadow_timeout_seconds,
        maximum_pending=TRANSITION_SETTINGS.maximum_pending_comparisons,
    )
else:
    SHADOW_COORDINATOR = None

    def resolve_shadow_journey(method, path, query_string):
        return None


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Validate security before accepting traffic, then initialize repository state."""
    validate_security_settings(application.state.security_settings)
    application.state.database = DatabaseManager(DatabaseSettings.from_env())
    if POSTGRESQL_ONLY_RUNTIME:
        # Constructing the pool remains lazy; readiness owns the first bounded
        # connection and all production repositories use the same manager.
        sessions = application.state.database.sessions()
        public_readonly = (
            application.state.security_settings.auth_mode == "public_readonly"
        )
        if public_readonly:
            # The public web task has a database role with SELECT only. It must
            # never synchronize configuration or persist audit/idempotency rows
            # as a side effect of startup or anonymous reads.
            application.state.audit_sink = StructuredLogAuditSink()
            application.state.idempotency_store = IdempotencyStore()
            logger.info("Public read-only PostgreSQL runtime initialized without writes.")
        else:
            application.state.audit_sink = PostgresAuditSink(sessions)
            application.state.idempotency_store = PostgresIdempotencyStore(sessions)
            synchronized_sources = await PipelineRepository(sessions).synchronize_sources(
                load_source_configurations()
            )
            synchronized_policies = await GovernanceRepository(sessions).synchronize_policies(
                load_governance_configuration()
            )
            logger.info(
                "Synchronized %s governance-gated source configurations.",
                synchronized_sources,
            )
            logger.info(
                "Synchronized %s non-destructive retention policies.",
                synchronized_policies,
            )
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
        if SHADOW_COORDINATOR is not None:
            await SHADOW_COORDINATOR.drain()
        await application.state.database.close()


app = FastAPI(
    title="Foundation Intelligence Platform BFF API",
    description=(
        "Backend for the Foundation Intelligence Platform. It serves normalized organization, "
        "grant, provenance, enrichment, and experimental relevance-score data to the dashboard."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if SECURITY_SETTINGS.auth_mode == "cognito_rbac" else "/docs",
    redoc_url=None if SECURITY_SETTINGS.auth_mode == "cognito_rbac" else "/redoc",
    openapi_url=None if SECURITY_SETTINGS.auth_mode == "cognito_rbac" else "/openapi.json",
)

app.state.security_settings = SECURITY_SETTINGS
app.state.rate_limiter = SlidingWindowRateLimiter(
    SECURITY_SETTINGS.rate_limit_requests,
    SECURITY_SETTINGS.rate_limit_window_seconds,
)
app.state.idempotency_store = IdempotencyStore()
app.state.audit_sink = StructuredLogAuditSink()
app.state.database = DatabaseManager(DatabaseSettings.from_env())
app.state.observability_configuration = load_observability_configuration()
app.state.metrics_registry = MetricsRegistry(app.state.observability_configuration)

app.add_middleware(
    ShadowComparisonMiddleware,
    coordinator=SHADOW_COORDINATOR,
    maximum_response_bytes=TRANSITION_SETTINGS.maximum_response_bytes,
    journey_resolver=resolve_shadow_journey,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(SECURITY_SETTINGS.cors_origins),
    allow_credentials=SECURITY_SETTINGS.auth_mode == "development",
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Action-Reason",
        "X-Request-ID",
        "X-Trace-ID",
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
    supplied_trace_id = request.headers.get("x-trace-id", "")
    request.state.trace_id = (
        supplied_trace_id
        if _REQUEST_ID_PATTERN.fullmatch(supplied_trace_id)
        else request.state.request_id
    )
    settings = app.state.security_settings

    if (
        settings.auth_mode == "public_readonly"
        and request.url.path.startswith("/api/")
        and request.method not in {"GET", "HEAD"}
    ):
        # Fail before routing so an anonymous mutation-shaped request never
        # becomes a 405/validation oracle or reaches a current/future handler.
        response = JSONResponse(
            status_code=401,
            content={"detail": "Public demo access is read-only."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
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
    response.headers["X-Trace-ID"] = request.state.trace_id
    record_key = getattr(request.state, "idempotency_record_key", None)
    if record_key:
        if response.status_code < 400 or response.status_code >= 500:
            completion = app.state.idempotency_store.complete(record_key)
        else:
            completion = app.state.idempotency_store.release(record_key)
        if inspect.isawaitable(completion):
            await completion
    if hasattr(request.state, "audit_action"):
        error_class = None if response.status_code < 400 else f"http_{response.status_code}"
        audit_result = app.state.audit_sink.record(
            event_from_request(request, response.status_code, error_class)
        )
        if inspect.isawaitable(audit_result):
            await audit_result

    duration_ms = (time.time() - start_time) * 1000
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    operation = f"{request.method} {route_path}"
    principal = getattr(request.state, "principal", None)
    actor_id = pseudonymous_actor_id(
        getattr(principal, "actor_id", "anonymous")
    )
    role = getattr(principal, "primary_role", "anonymous")
    error_class = None if response.status_code < 400 else f"http_{response.status_code}"
    registry = app.state.metrics_registry
    metric_dimensions = {
        "service": app.state.observability_configuration.service,
        "environment": settings.app_env,
        "operation": operation,
        "status": str(response.status_code),
    }
    registry.observe("api_request_duration_ms", duration_ms, **metric_dimensions)
    if response.status_code >= 500:
        registry.increment(
            "api_errors_total",
            service=app.state.observability_configuration.service,
            environment=settings.app_env,
            operation=operation,
            error_class=error_class,
        )
    pool_status = app.state.database.pool_status()
    registry.set_gauge(
        "db_pool_checked_out",
        pool_status["checked_out"],
        service=app.state.observability_configuration.service,
        environment=settings.app_env,
    )
    registry.set_gauge(
        "db_pool_utilization_ratio",
        pool_status["utilization_ratio"],
        service=app.state.observability_configuration.service,
        environment=settings.app_env,
    )
    if POSTGRESQL_ONLY_RUNTIME:
        registry.set_gauge(
            "cache_hit_ratio",
            ANALYTICS_CACHE.hit_ratio,
            service=app.state.observability_configuration.service,
            environment=settings.app_env,
            cache="analytics",
        )
    logger.info(
        "request_completed",
        extra={
            "request_id": request.state.request_id,
            "trace_id": request.state.trace_id,
            "actor_id": actor_id,
            "role": role,
            "operation": operation,
            "duration_ms": round(duration_ms, 3),
            "status": response.status_code,
            "error_class": error_class,
        },
    )
    return response

# Custom centralized exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "request_unhandled_exception",
        extra={
            "request_id": getattr(request.state, "request_id", "unknown"),
            "trace_id": getattr(request.state, "trace_id", "unknown"),
            "operation": f"{request.method} {request.url.path}",
            "status": 500,
            "error_class": exc.__class__.__name__,
        },
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
if POSTGRESQL_ONLY_RUNTIME:
    app.include_router(scraper_router)
    app.include_router(governance_router)
    app.include_router(observability_router)
app.include_router(news_router)
app.include_router(user_management_router)

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root access to Swagger interactive documentation."""
    if app.state.security_settings.auth_mode == "cognito_rbac":
        return JSONResponse(status_code=404, content={"detail": "Not found"})
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
    """Check schema, dataset, critical controls and durable queue independently."""
    settings = app.state.security_settings
    result = await app.state.database.readiness(
        expected_schema_version=app.state.observability_configuration.expected_schema_version,
        require_critical_configuration=settings.auth_mode != "public_readonly",
    )
    metadata = result.get("metadata", {})
    registry = app.state.metrics_registry
    registry.set_gauge(
        "readiness_success",
        1.0 if result["ready"] else 0.0,
        service=app.state.observability_configuration.service,
        environment=settings.app_env,
    )
    if metadata:
        registry.set_gauge(
            "queue_oldest_message_age_seconds",
            float(metadata["queue_age_seconds"]),
            service=app.state.observability_configuration.service,
            environment=settings.app_env,
            queue="pipeline",
        )
        registry.set_gauge(
            "dlq_depth",
            float(metadata["dead_letter_count"]),
            service=app.state.observability_configuration.service,
            environment=settings.app_env,
            queue="pipeline",
        )
    if result["ready"]:
        return {"status": "ready", "checks": result["checks"]}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "checks": result["checks"]},
    )
