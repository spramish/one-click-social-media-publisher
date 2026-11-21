"""Refresh-token cookie helpers.

The refresh token is the long-lived credential, so it is delivered as an
``HttpOnly`` cookie: JavaScript (and therefore any XSS payload) cannot read it,
and it is only ever sent to ``/api/v1/auth``.

``SameSite`` and ``Secure`` come from settings; a production deployment must
set ``REFRESH_COOKIE_SECURE=true`` (enforced in
:class:`app.core.config.Settings`).
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from typing import Literal

from fastapi import Request, Response

from app.core.config import settings
from app.core.security import utcnow


def set_refresh_cookie(response: Response, token: str, expires_at: datetime) -> None:
    """Attach the rotating refresh token to the response."""
    max_age = max(0, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=max_age,
        expires=expires_at,
        path=settings.refresh_cookie_path,
        domain=settings.refresh_cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=cast(Literal["lax", "strict", "none"], settings.refresh_cookie_samesite),
    )


def clear_refresh_cookie(response: Response) -> None:
    """Expire the refresh cookie (logout, password reset)."""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.refresh_cookie_domain,
    )


def read_refresh_cookie(request: Request) -> str | None:
    """Read the refresh token from the cookie, if present."""
    return request.cookies.get(settings.refresh_cookie_name)
