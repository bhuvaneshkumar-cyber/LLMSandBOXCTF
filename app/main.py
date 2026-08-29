"""
main.py — Application entry point.

Responsibilities
----------------
* Create the FastAPI application instance.
* Register lifespan handler (DB table creation on startup).
* Apply middleware in the correct order:
    1. CORSMiddleware  — restrict cross-origin access to known frontend origins.
    2. slowapi state   — attach the global IP rate limiter so it can be used
                         by the @limiter.limit decorator on route handlers.
* Register routers under /api/v1.
* Expose GET /health liveness probe.

CORS configuration
------------------
``settings.CORS_ORIGINS`` is a comma-separated string read from .env::

    CORS_ORIGINS="https://challenge.gdgvitchennai.com,http://localhost:3000"

Edit that variable (not this file) to add or remove allowed origins.
Do NOT set it to "*" in production — that would allow any website to make
credentialed requests to the admin endpoints.

Rate limiting wiring
--------------------
The global slowapi ``Limiter`` is attached to ``app.state`` here.
The ``RateLimitExceeded`` exception handler converts limit violations into
clean 429 JSON responses without leaking internal details.

The per-route @limiter.limit decorator on ``/chat`` applies the IP-level
limit (default: 60/minute).  The per-participant limit is enforced inside
``routes_chat.post_chat()`` via ``check_rate_limit()``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.routes_admin import router as admin_router
from app.api.routes_chat import router as chat_router
from app.core.config import settings
from app.core.security import IP_RATE_LIMIT, limiter
from app.db.session import init_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CORS origin list
# ---------------------------------------------------------------------------

def _parse_cors_origins(raw: str) -> list[str]:
    """
    Parse the comma-separated CORS_ORIGINS setting into a list of origin strings.

    Leading/trailing whitespace is stripped from each entry so that::

        CORS_ORIGINS="https://foo.com, http://localhost:3000"

    works as expected.

    Args:
        raw: Raw value of ``settings.CORS_ORIGINS``.

    Returns:
        List of cleaned origin strings.
    """
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


_CORS_ORIGINS: list[str] = _parse_cors_origins(settings.CORS_ORIGINS)

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Startup
    -------
    * Creates all database tables defined in ``app.db.models`` (via
      ``Base.metadata.create_all``).  This is a no-op on subsequent restarts
      when the tables already exist.

    Shutdown
    --------
    * No explicit teardown needed for SQLite/aiosqlite.
    * For Postgres, dispose the engine connection pool here.

    TODO: Replace ``create_all`` with Alembic migrations before any
    non-ephemeral deployment.
    """
    logger.info("Starting up — initialising database tables…")
    await init_db()
    logger.info("Database ready.  Listening on %s:%s", settings.HOST, settings.PORT)

    yield  # Application runs here.

    logger.info("Shutting down.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "LLM Sandbox — a CTF-style prompt-injection challenge backend.\n\n"
        "Participants interact with the Vault Keeper via ``POST /api/v1/chat``. "
        "Admin endpoints are protected by the ``X-Admin-Key`` header."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# slowapi — global IP rate limiter
# ---------------------------------------------------------------------------

# Attach the limiter to app.state so the @limiter.limit decorator can find it.
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Return a clean 429 JSON response when the IP rate limit is exceeded.

    The raw slowapi exception detail is intentionally suppressed — it can
    contain internal limit strings that are not useful to end users.
    """
    return JSONResponse(
        status_code=429,
        content={
            "detail": (
                "Too many requests from your IP address. "
                f"Limit: {IP_RATE_LIMIT}. Please wait before trying again."
            )
        },
        headers={"Retry-After": "60"},
    )


# ---------------------------------------------------------------------------
# Middleware (order matters — applied bottom-up by Starlette)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    # Explicit origin allowlist — never "*" in production.
    # Edit CORS_ORIGINS in .env to add your frontend URL(s).
    allow_origins=_CORS_ORIGINS,
    # No cookie/session auth exists (see security.py threat model) and the
    # frontend never sets fetch(..., {credentials: "include"}), so this
    # stays False — True buys nothing here and just widens the CORS surface.
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key", "X-Participant-Token"],
    expose_headers=["Retry-After"],
)

# ---------------------------------------------------------------------------
# Request body size cap — rejects oversized payloads before they reach
# Pydantic/the LLM pipeline. Checked via Content-Length only (a request
# without that header, e.g. chunked transfer, is not covered by this check).
# ---------------------------------------------------------------------------


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):  # noqa: ANN001, ANN201
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            too_large = int(content_length) > settings.MAX_REQUEST_BODY_BYTES
        except ValueError:
            too_large = False  # Malformed header — let normal parsing reject it.
        if too_large:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large."},
            )
    return await call_next(request)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])

# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/",
    tags=["health"],
    summary="Root Endpoint",
    response_description="Welcome message",
)
async def root() -> dict:
    """
    Root endpoint.
    """
    return {
        "message": "LLM Sandbox API is running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get(
    "/health",
    tags=["health"],
    summary="Liveness probe",
    response_description="Service status and version.",
)
async def health_check() -> dict:
    """
    Liveness probe — confirms the service is running and accepting requests.

    Does **not** check DB or Redis connectivity (use a readiness probe for
    that).  Extend this endpoint or add ``/ready`` once infrastructure health
    checks are needed.

    Returns:
        JSON with ``status`` and ``version`` fields.
    """
    return {"status": "ok", "version": settings.APP_VERSION}
