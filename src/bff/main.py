import time
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from bff.auth import router as auth_router
from bff.charity import router as charity_router
from bff.proxy import router as proxy_router
from bff.admin import router as admin_router
from bff.news import router as news_router
from bff.repositories import get_charity_repository
from bff.utils.logging import logger

app = FastAPI(
    title="Foundation Intelligence Platform BFF API",
    description=(
        "Backend for the Foundation Intelligence Platform. It serves normalized organization, "
        "grant, provenance, enrichment, and experimental relevance-score data to the dashboard."
    ),
    version="1.0.0"
)

# CORS configuration
# Note: when allow_credentials=True, allow_origins cannot be ["*"].
# We explicitly list common development and localhost URLs.
origins = [
    "http://localhost:3000",  # React / Next.js default
    "http://localhost:5173",  # Vite default (React/Vue/Svelte)
    "http://localhost:8000",  # FastAPI docs / local
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    # Vite selects the next port when a previous local dev session owns 5173.
    "http://127.0.0.1:5174",
]

# Vite moves to the next available port when another local development session
# already owns its default port. Keep credentialed browser requests local-only,
# while allowing those fallback Vite ports without having to restart the BFF for
# each one.
local_development_origin = r"http://(localhost|127\.0\.0\.1):\d+"

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=local_development_origin,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def warm_repository() -> None:
    """Initialize the repository without blocking readiness on a full scan.

    Persisted derived indexes and cached Overview payloads are reused by the
    first relevant request. A synchronous full-Overview warmup previously kept
    the health endpoint unavailable for tens of seconds on the audited 1.3 GB
    SQLite file, making a healthy local process look crashed.
    """
    get_charity_repository()
    logger.info(
        "Repository initialized; expensive Overview aggregation is request-driven."
    )

# Request-Response logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # Process request
    response = await call_next(request)
    
    duration = time.time() - start_time
    logger.info(
        f"Request: {request.method} {request.url.path} "
        f"| Status: {response.status_code} "
        f"| Duration: {duration:.4f}s"
    )
    return response

# Custom centralized exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception occurred on path {request.url.path}: {exc}", exc_info=True)
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
    """Simple endpoint to verify that the BFF is running and healthy."""
    return {"status": "healthy", "service": "bff"}
