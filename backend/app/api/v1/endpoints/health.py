"""Health-check endpoints.

These are the Phase 1 proof that the frontend, backend, PostgreSQL, and Redis
can all communicate. They are intentionally dependency-light so they can run
before any ORM models exist.
"""

from typing import Any

import redis
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import engine

router = APIRouter()
logger = get_logger(__name__)


@router.get("", summary="Liveness probe")
def health() -> dict[str, Any]:
    """Return basic application status (no external dependencies checked)."""
    return {
        "status": "ok",
        "service": settings.project_name,
        "environment": settings.environment,
        "version": "0.1.0",
    }


@router.get("/db", summary="Database readiness probe")
def health_db() -> dict[str, Any]:
    """Verify that PostgreSQL is reachable and responding."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "reachable"}
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller on purpose
        logger.warning("database health check failed", extra={"error": str(exc)})
        return {"status": "error", "database": "unreachable", "detail": str(exc)}


@router.get("/redis", summary="Redis readiness probe")
def health_redis() -> dict[str, Any]:
    """Verify that Redis (broker/cache) is reachable and responding."""
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return {"status": "ok", "redis": "reachable"}
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller on purpose
        logger.warning("redis health check failed", extra={"error": str(exc)})
        return {"status": "error", "redis": "unreachable", "detail": str(exc)}
