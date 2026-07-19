import time
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from bff.auth import router as auth_router
from bff.charity import router as charity_router
from bff.proxy import router as proxy_router
from bff.utils.logging import logger

app = FastAPI(
    title="Backend for Frontend (BFF) API",
    description=(
        "Dedicated Backend for Frontend service for the Register of Charities dashboard. "
        "Handles user session cookies, token translation, data aggregation, and API proxying."
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
    "http://127.0.0.1:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root access to Swagger interactive documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Health Check"])
async def health_check():
    """Simple endpoint to verify that the BFF is running and healthy."""
    return {"status": "healthy", "service": "bff"}
