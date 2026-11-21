"""Connected social accounts and their encrypted OAuth credentials."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import SocialAccountStatus, SocialPlatform
from app.db.mixins import SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.analytics import AccountMetric
    from app.models.post import PostTarget
    from app.models.workspace import Workspace


class SocialAccount(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A social media account connected to a workspace via OAuth."""

    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "platform", "platform_account_id"),
    )

    platform: Mapped[SocialPlatform] = mapped_column(
        enum_column(SocialPlatform, "social_platform"),
        nullable=False,
    )
    platform_account_id: Mapped[str] = mapped_column(String(190), nullable=False)
    display_name: Mapped[str] = mapped_column(String(190), nullable=False)
    username: Mapped[str | None] = mapped_column(String(190))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[SocialAccountStatus] = mapped_column(
        enum_column(SocialAccountStatus, "social_account_status"),
        nullable=False,
        server_default=SocialAccountStatus.CONNECTED.value,
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When an "access expires soon" warning was recorded for this grant, so the
    #: sweep warns once per expiry window rather than on every tick.
    token_warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="social_accounts")
    credentials: Mapped["OAuthCredential | None"] = relationship(
        "OAuthCredential",
        back_populates="social_account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    post_targets: Mapped[list["PostTarget"]] = relationship(
        "PostTarget",
        back_populates="social_account",
    )
    metrics: Mapped[list["AccountMetric"]] = relationship(
        "AccountMetric",
        back_populates="social_account",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SocialAccount platform={self.platform} display_name={self.display_name!r}>"


class OAuthCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-to-one vault row holding a social account's OAuth tokens.

    Tokens are stored **only** in encrypted form (``*_encrypted``); the
    encryption/decryption helper lands with the OAuth work in Phase 6 and the
    key is read from application settings — never hard-coded.

    This table has no ``workspace_id`` of its own: it is reachable only through
    its owning :class:`SocialAccount`, which carries the tenant discriminator.
    """

    __tablename__ = "oauth_credentials"

    social_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("social_accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="Bearer")
    scopes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    social_account: Mapped["SocialAccount"] = relationship(
        "SocialAccount",
        back_populates="credentials",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OAuthCredential social_account_id={self.social_account_id}>"


class OAuthAuthorizationState(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A single in-flight OAuth authorization attempt (the ``state`` parameter).

    The value handed to the platform is a random 256-bit string; only its
    SHA-256 digest is stored, so a leaked database row cannot be replayed as a
    valid ``state``. The row also carries everything the callback needs that
    must stay secret from the browser — in particular the **PKCE code
    verifier** — which is why the verifier is encrypted rather than stored raw.

    A row is usable exactly once: :func:`~app.services.accounts.complete_connection`
    marks ``consumed_at`` under a row lock, so a replayed callback is rejected
    even if it arrives before the TTL elapses.
    """

    __tablename__ = "oauth_authorization_states"
    __table_args__ = (
        UniqueConstraint("state_hash"),
        Index("ix_oauth_authorization_states_expires_at", "expires_at"),
    )

    #: SHA-256 of the random ``state`` value sent to the platform.
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[SocialPlatform] = mapped_column(
        enum_column(SocialPlatform, "social_platform"),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    #: The exact ``redirect_uri`` used at authorization time. The token exchange
    #: must repeat it byte-for-byte or the platform rejects the request.
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    #: PKCE verifier, encrypted with the application key.
    code_verifier_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OAuthAuthorizationState platform={self.platform} consumed={self.consumed_at is not None}>"
