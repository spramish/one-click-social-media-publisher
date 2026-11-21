"""Error tracking (Phase 15).

Sentry is **opt-in**: with no ``SENTRY_DSN`` this module does nothing, so local
development and the automated test suite never ship events anywhere. Set the DSN
and unhandled exceptions, their request context, and the JSON logs around them
become searchable in one place.

Request bodies are deliberately left off — they carry credentials, refresh
tokens, and video bytes.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def configure_error_tracking() -> bool:
    """Initialise Sentry when a DSN is configured.

    Returns whether tracking was enabled, which is what the boot log asserts.
    ``sentry_sdk.init`` patches the ASGI app, so this must run **before** the
    ``FastAPI`` instance is created.
    """
    if not settings.sentry_dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release="0.1.0",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    logger.info("error tracking enabled", extra={"environment": settings.environment})
    return True
