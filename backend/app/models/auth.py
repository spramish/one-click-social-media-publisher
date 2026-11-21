"""Authentication state: refresh-token sessions and one-time tokens.

Neither table ever stores a usable secret. Refresh tokens and verification
tokens are random 256-bit values handed to the client once; only their
SHA-256 digest is persisted, so a database leak does not hand an attacker a
working credential.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import VerificationPurpose
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.user import User


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One login on one device, backed by a rotating refresh token.

    Rotation model: the client holds a *refresh token*; every call to
    ``/auth/refresh`` revokes the presented row and inserts a successor.
    ``family_id`` ties the whole chain together so that replaying an already
    rotated token (a strong signal of theft) can revoke the entire family in a
    single statement.

    A row therefore doubles as a *session* for the "manage my devices" screen:
    ``revoked_at IS NULL AND expires_at > now()`` means it is live.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: SHA-256 hex digest (64 chars) of the opaque refresh token.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: Identifier shared by every token produced by one login chain.
    family_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Free-text reason such as ``rotated``, ``logout``, ``reuse_detected``.
    revoked_reason: Mapped[str | None] = mapped_column(String(40))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="SET NULL"),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    #: Long enough for an IPv6 address plus a zone/port suffix.
    ip_address: Mapped[str | None] = mapped_column(String(45))

    user: Mapped["User"] = relationship("User", back_populates="auth_sessions")

    @property
    def is_active(self) -> bool:
        """True when the session is neither revoked nor expired."""
        if self.revoked_at is not None:
            return False
        return self.expires_at > datetime.now(timezone.utc)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuthSession id={self.id} user={self.user_id} revoked={self.revoked_at is not None}>"


class VerificationToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single-use, expiring secret emailed to the user.

    Used for both email verification and password reset. Consumption is
    recorded in ``used_at`` rather than deleting the row, so a replay attempt
    can be distinguished from an expired or unknown token.
    """

    __tablename__ = "verification_tokens"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[VerificationPurpose] = mapped_column(
        enum_column(VerificationPurpose, "verification_purpose"),
        nullable=False,
    )
    #: SHA-256 hex digest (64 chars) of the opaque token sent by email.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship("User", back_populates="verification_tokens")

    @property
    def is_usable(self) -> bool:
        """True when the token has neither been used nor expired."""
        if self.used_at is not None:
            return False
        return self.expires_at > datetime.now(timezone.utc)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<VerificationToken id={self.id} purpose={self.purpose.value}>"
