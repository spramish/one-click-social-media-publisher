"""FastAPI application entry point.

Run locally (PowerShell):
    uvicorn app.main:app --reload

Interactive API docs: http://localhost:8000/docs
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import OriginGuardMiddleware, SecurityHeadersMiddleware
from app.core.observability import configure_error_tracking

logger = get_logger(__name__)

# Sentry patches the ASGI app during ``init``, so it has to run before the
# FastAPI instance below exists. A no-op unless SENTRY_DSN is set.
configure_error_tracking()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on startup and log shutdown."""
    configure_logging()
    logger.info(
        "application starting",
        extra={"environment": settings.environment, "version": "0.1.0"},
    )
    yield
    logger.info("application shutting down")


app = FastAPI(
    title=settings.project_name,
    version="0.1.0",
    description=(
        "Upload one video and publish it to Instagram, Facebook, YouTube, "
        "and TikTok from a single dashboard."
    ),
    debug=settings.debug,
    lifespan=lifespan,
)

# Middleware order (Starlette runs the last-added first, i.e. outermost):
#   CORS → SecurityHeaders → OriginGuard → GZip → app
# CORS is outermost so its headers are on error responses too; the security
# headers wrap the CSRF guard so even its 403 carries them; GZip sits closest to
# the app so it compresses the final body.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(OriginGuardMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """Convenience root endpoint pointing to the docs."""
    return {"service": settings.project_name, "docs": "/docs", "health": "/api/v1/health"}
