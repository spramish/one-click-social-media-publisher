"""Request/response schemas for connected social accounts.

Nothing here ever carries a token. The public shape of an account exposes only
its identity, its status, and the *metadata* of its credential — the expiry
instant and the granted scopes — which is what a dashboard needs to show a
health indicator. The secrets themselves live encrypted in
``oauth_credentials`` and never leave the server.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.security import utcnow
from app.db.enums import SocialAccountStatus, SocialPlatform
from app.models.social_account import SocialAccount


class PlatformCapability(BaseModel):
    """Whether one platform can be connected on this server, and how many are."""

    platform: SocialPlatform
    display_name: str
    #: False when the server has no client id/secret for this platform, so the
    #: UI can show "not configured" instead of a button that cannot work.
    configured: bool
    connected_count: int


class AccountCapabilitiesPublic(BaseModel):
    """The connectable platforms for one workspace."""

    platforms: list[PlatformCapability]


class SocialAccountPublic(BaseModel):
    """A connected account as the dashboard sees it."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    platform: SocialPlatform
    platform_account_id: str
    display_name: str
    username: str | None = None
    avatar_url: str | None = None
    status: SocialAccountStatus
    #: When the current access token stops working, if the platform tells us.
    token_expires_at: datetime | None = None
    #: Seconds until expiry (negative once past it); ``None`` when unknown.
    token_expires_in_seconds: int | None = None
    #: True when the connection cannot publish until the user reconnects.
    needs_reconnect: bool
    #: The permission scopes the platform actually granted.
    scopes: list[str]
    connected_by_id: uuid.UUID | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_account(cls, account: SocialAccount) -> "SocialAccountPublic":
        credential = account.credentials
        scopes = list(credential.scopes) if credential and credential.scopes else []

        expires_at = account.token_expires_at
        remaining: int | None = None
        if expires_at is not None:
            remaining = int((expires_at - utcnow()).total_seconds())

        needs_reconnect = account.status is not SocialAccountStatus.CONNECTED or (
            expires_at is not None and expires_at <= utcnow()
        )

        return cls(
            id=account.id,
            workspace_id=account.workspace_id,
            platform=account.platform,
            platform_account_id=account.platform_account_id,
            display_name=account.display_name,
            username=account.username,
            avatar_url=account.avatar_url,
            status=account.status,
            token_expires_at=expires_at,
            token_expires_in_seconds=remaining,
            needs_reconnect=needs_reconnect,
            scopes=scopes,
            connected_by_id=account.connected_by_id,
            last_synced_at=account.last_synced_at,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


class AuthorizationStartPublic(BaseModel):
    """Where to send the browser to grant access on a platform."""

    platform: SocialPlatform
    authorization_url: str
    #: When the ``state`` this URL carries stops being accepted.
    expires_at: datetime
    state_ttl_seconds: int


class AccountDisconnectResult(BaseModel):
    """Outcome of disconnecting an account."""

    message: str
    #: True when the platform confirmed the grant was revoked upstream. False
    #: means the connection is gone locally but the platform may still list it.
    revoked_upstream: bool


__all__ = [
    "AccountCapabilitiesPublic",
    "AccountDisconnectResult",
    "AuthorizationStartPublic",
    "PlatformCapability",
    "SocialAccountPublic",
]
