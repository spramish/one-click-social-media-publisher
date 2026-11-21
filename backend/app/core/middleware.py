"""Security-related HTTP middleware (Phase 13).

Two concerns that are cheaper to apply once to every response than to remember
per route:

* :class:`SecurityHeadersMiddleware` — the hardening headers a browser honours
  (``nosniff``, clickjacking and referrer policy, a deny-by-default CSP, HSTS in
  production). The API returns JSON, so a locked-down policy costs nothing; the
  interactive docs pages are exempted because Swagger UI ships its own scripts.
* :class:`OriginGuardMiddleware` — a CSRF control for **cookie-authenticated**
  requests. State-changing requests that carry an ``Origin`` must come from the
  configured frontend origin or from the API's own origin; a cross-site form POST
  (which browsers do send the refresh cookie with) is refused with ``403``.
  Requests without an ``Origin`` header (CLIs, mobile clients, the test suite)
  are unaffected. Bearer-token requests are inherently CSRF-safe — a browser
  never attaches an ``Authorization`` header to a cross-site request — so the
  same check also protects them at no cost.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths whose HTML needs to load scripts/styles from the docs CDN, so the
#: deny-by-default CSP is skipped for them.
_CSP_EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def _same_origin(origin: str, host: str | None) -> bool:
    """True when ``origin`` points at the same host:port the request arrived on."""
    if not host:
        return False
    return urlsplit(origin).netloc.lower() == host.lower()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardening headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._allowed_origins = {o.rstrip("/") for o in settings.cors_origins_list}

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        if not request.url.path.startswith(_CSP_EXEMPT_PREFIXES):
            headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )

        # HSTS only where HTTPS is guaranteed; sending it over plain HTTP in
        # development would lock the developer's browser out of localhost.
        if settings.environment == "production":
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Refuse cross-site state-changing requests that present an ``Origin``."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._allowed_origins = {o.rstrip("/") for o in settings.cors_origins_list}

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin:
                normalised = origin.rstrip("/")
                if normalised not in self._allowed_origins and not _same_origin(
                    normalised, request.headers.get("host")
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Cross-origin request refused"},
                    )
        return await call_next(request)


__all__ = ["OriginGuardMiddleware", "SecurityHeadersMiddleware"]
