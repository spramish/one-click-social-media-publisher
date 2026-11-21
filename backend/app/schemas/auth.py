"""Request/response schemas for authentication.

Two rules are enforced here rather than in the routes:

* ``EmailStr`` normalisation — the service lower-cases addresses so
  ``A@B.com`` and ``a@b.com`` cannot become two accounts;
* password strength — length is checked against ``PASSWORD_MIN_LENGTH`` while
  the clear-text password is never echoed back in any response model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import settings

#: Passwords longer than this are rejected outright; bcrypt itself is capped at
#: 72 bytes, and :mod:`app.core.security` pre-hashes anything above that.
PASSWORD_MAX_LENGTH = 128


def validate_password_strength(value: str) -> str:
    """Shared policy: long enough, not longer than the cap, not blank."""
    if len(value) < settings.password_min_length:
        raise ValueError(
            f"password must be at least {settings.password_min_length} characters long"
        )
    if len(value) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"password must be at most {PASSWORD_MAX_LENGTH} characters long")
    if not value.strip():
        raise ValueError("password must not be blank")
    return value


class UserPublic(BaseModel):
    """An account as exposed over the API. Never contains a hash or token."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    is_active: bool
    is_verified: bool
    is_superuser: bool
    created_at: datetime
    last_login_at: datetime | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = Field(default=None, max_length=160)
    workspace_name: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return validate_password_strength(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class AuthResponse(BaseModel):
    """What register/login/refresh return.

    The refresh token is deliberately absent: it travels in an ``HttpOnly``
    cookie so client-side JavaScript can never read it.
    """

    access_token: str
    token_type: str = "bearer"
    #: Access-token lifetime in seconds.
    expires_in: int
    user: UserPublic


class RefreshRequest(BaseModel):
    """Optional body fallback for non-browser clients (CLI, mobile)."""

    refresh_token: str | None = Field(default=None, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class MessageResponse(BaseModel):
    """Generic acknowledgement."""

    message: str


class SessionPublic(BaseModel):
    """One active session / device."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_agent: str | None = None
    ip_address: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime
    current: bool = False


class TokenConfirmRequest(BaseModel):
    """Confirm a one-time token (email verification)."""

    token: str = Field(min_length=16, max_length=512)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return validate_password_strength(value)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return validate_password_strength(value)
