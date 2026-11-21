"""Connected social accounts: the OAuth connection lifecycle.

This is the one module that knows how a platform grant becomes a
:class:`~app.models.social_account.SocialAccount` row and how that row is kept
alive. It is deliberately written against
:class:`~app.services.oauth.base.OAuthProvider`, so it contains no
platform-specific branching.

The lifecycle has four steps:

1. **begin** — :func:`begin_authorization` mints an unguessable ``state``, stores
   only its SHA-256 digest (plus, for PKCE platforms, the *encrypted* code
   verifier), and returns the consent URL.
2. **complete** — :func:`complete_connection` consumes the state exactly once
   under a row lock, exchanges the code, reads the account identities, and
   upserts one :class:`SocialAccount` per publishing destination.
3. **keep alive** — :func:`refresh_account` and :func:`ensure_fresh_tokens`
   rotate the stored access token before it expires.
4. **end** — :func:`disconnect_account` revokes the grant upstream where the
   platform supports it, then destroys the local credential.

Every mutation commits. The callback route in particular *must* be able to
report success or failure to the browser after the fact, so a partially
consumed state or a failed token exchange has to survive the request.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.crypto import EncryptionError, decrypt_secret, encrypt_secret
from app.core.logging import get_logger
from app.core.security import hash_opaque_token, utcnow
from app.db.enums import SocialAccountStatus, SocialPlatform
from app.models.social_account import (
    OAuthAuthorizationState,
    OAuthCredential,
    SocialAccount,
)
from app.services.oauth import (
    OAuthAuthorizationError,
    OAuthError,
    OAuthProvider,
    OAuthStateError,
    OAuthTokens,
    code_challenge_for,
    generate_code_verifier,
    generate_state,
    get_provider,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AccountError(Exception):
    """Base class for connected-account failures."""


class AccountNotFoundError(AccountError):
    """No such connected account in this workspace."""


class AccountCredentialError(AccountError):
    """The stored credential is missing or cannot be decrypted."""


class AccountNeedsReconnectError(AccountError):
    """The token is dead and cannot be refreshed; the user must reconnect."""


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
def provider_for(platform: SocialPlatform) -> OAuthProvider:
    """The provider behind a platform, for callers that need configuration info."""
    return get_provider(platform)


def platform_is_configured(platform: SocialPlatform) -> bool:
    """True when the platform's client credentials are present on this server."""
    return get_provider(platform).is_configured()


def connected_counts(db: Session, workspace_id: uuid.UUID) -> dict[SocialPlatform, int]:
    """How many live accounts the workspace has, per platform."""
    rows = db.execute(
        SocialAccount.scoped(workspace_id)
        .with_only_columns(SocialAccount.platform, func.count())
        .group_by(SocialAccount.platform)
    ).all()
    return {platform: count for platform, count in rows}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_accounts(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    platform: SocialPlatform | None = None,
    status: SocialAccountStatus | None = None,
) -> list[SocialAccount]:
    """Live accounts in one workspace, newest platform order first.

    Soft-deleted (disconnected) accounts are excluded by ``scoped``; the
    credential is eager-loaded so shaping a response costs no extra query.
    """
    stmt = SocialAccount.scoped(workspace_id).options(selectinload(SocialAccount.credentials))
    if platform is not None:
        stmt = stmt.where(SocialAccount.platform == platform)
    if status is not None:
        stmt = stmt.where(SocialAccount.status == status)
    stmt = stmt.order_by(SocialAccount.platform, SocialAccount.display_name)
    return list(db.scalars(stmt))


def get_account(
    db: Session, workspace_id: uuid.UUID, account_id: uuid.UUID
) -> SocialAccount:
    """One account inside the caller's workspace, or raise."""
    account = db.scalar(
        SocialAccount.scoped(workspace_id)
        .options(selectinload(SocialAccount.credentials))
        .where(SocialAccount.id == account_id)
    )
    if account is None:
        raise AccountNotFoundError("That connected account does not exist in this workspace.")
    return account


def read_tokens(account: SocialAccount) -> OAuthTokens:
    """Decrypt the account's stored credential.

    Raises:
        AccountCredentialError: the account has no vault row, or the value
            cannot be decrypted with the configured key (a rotated secret, or a
            tampered row).
    """
    credential = account.credentials
    if credential is None:
        raise AccountCredentialError(
            "This account has no stored credentials. Reconnect the account."
        )
    try:
        access_token = decrypt_secret(credential.access_token_encrypted)
        refresh_token = (
            decrypt_secret(credential.refresh_token_encrypted)
            if credential.refresh_token_encrypted
            else None
        )
    except EncryptionError as exc:
        logger.error(
            "stored oauth credential could not be decrypted",
            extra={"account_id": str(account.id), "platform": account.platform.value},
        )
        raise AccountCredentialError(
            "The stored credentials for this account can no longer be read. "
            "Reconnect the account."
        ) from exc
    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=credential.token_type or "Bearer",
        scopes=list(credential.scopes or []),
        expires_at=credential.expires_at,
    )


# ---------------------------------------------------------------------------
# Step 1 — begin
# ---------------------------------------------------------------------------
def begin_authorization(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    platform: SocialPlatform,
) -> tuple[str, datetime]:
    """Open an authorization attempt and return ``(consent_url, state_expiry)``.

    Raises:
        OAuthNotConfiguredError: the platform has no client credentials, so no
            usable consent URL exists (mapped to ``503``).
    """
    provider = get_provider(platform)
    provider.require_configured()

    state = generate_state()
    verifier = generate_code_verifier() if provider.uses_pkce else None
    challenge = code_challenge_for(verifier) if verifier is not None else None

    expires_at = utcnow() + timedelta(seconds=settings.oauth_state_ttl_seconds)
    db.add(
        OAuthAuthorizationState(
            workspace_id=workspace_id,
            state_hash=hash_opaque_token(state),
            platform=platform,
            created_by_id=user_id,
            redirect_uri=provider.callback_url(),
            # The verifier must stay secret from the browser, so it is stored
            # encrypted rather than in the clear.
            code_verifier_encrypted=encrypt_secret(verifier) if verifier else None,
            expires_at=expires_at,
        )
    )
    db.commit()
    logger.info(
        "oauth authorization started",
        extra={"platform": platform.value, "workspace_id": str(workspace_id)},
    )
    return provider.authorization_url(state=state, code_challenge=challenge), expires_at


# ---------------------------------------------------------------------------
# Step 2 — complete
# ---------------------------------------------------------------------------
def complete_connection(
    db: Session,
    *,
    platform: SocialPlatform,
    state: str | None,
    code: str | None,
    error: str | None = None,
    error_description: str | None = None,
) -> list[SocialAccount]:
    """Finish an authorization and upsert the connected account(s).

    The ``state`` row is consumed under ``SELECT … FOR UPDATE`` and marked used
    *before* the code is exchanged, so a replayed callback is rejected even if
    it arrives while the first is still in flight.

    Raises:
        OAuthStateError: ``state`` is missing, unknown, expired, already used,
            or belongs to a different platform.
        OAuthAuthorizationError: the platform reported a refusal.
        OAuthTokenError / OAuthApiError: the exchange or profile read failed.
    """
    provider = get_provider(platform)

    if error:
        raise _authorization_error(platform, error, error_description)
    if not state:
        raise OAuthStateError("The authorization response carried no state parameter.")
    if not code:
        raise OAuthStateError("The authorization response carried no code parameter.")

    authorization = db.scalar(
        select(OAuthAuthorizationState)
        .where(OAuthAuthorizationState.state_hash == hash_opaque_token(state))
        .with_for_update()
    )
    if authorization is None:
        raise OAuthStateError("This connection attempt is not recognised. Start again.")
    if authorization.platform != platform:
        raise OAuthStateError("This connection attempt was started for a different platform.")
    if authorization.consumed_at is not None:
        raise OAuthStateError("This connection attempt has already been used.")
    if authorization.expires_at <= utcnow():
        raise OAuthStateError("This connection attempt has expired. Start again.")

    authorization.consumed_at = utcnow()
    db.flush()

    code_verifier = (
        decrypt_secret(authorization.code_verifier_encrypted)
        if authorization.code_verifier_encrypted
        else None
    )

    tokens = provider.exchange_code(
        code=code,
        code_verifier=code_verifier,
        redirect_uri=authorization.redirect_uri,
    )
    profiles = provider.fetch_profiles(tokens)

    accounts = [
        _upsert_account(
            db,
            workspace_id=authorization.workspace_id,
            user_id=authorization.created_by_id,
            platform=platform,
            profile=profile,
            grant_tokens=tokens,
        )
        for profile in profiles
    ]
    db.commit()
    logger.info(
        "oauth connection completed",
        extra={
            "platform": platform.value,
            "workspace_id": str(authorization.workspace_id),
            "accounts": len(accounts),
        },
    )
    return accounts


def _authorization_error(
    platform: SocialPlatform, error: str, description: str | None
) -> OAuthError:
    detail = f": {description}" if description else ""
    return OAuthAuthorizationError(
        f"{get_provider(platform).display_name} declined the authorization "
        f"({error}{detail})."
    )


def _upsert_account(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    platform: SocialPlatform,
    profile,
    grant_tokens: OAuthTokens,
) -> SocialAccount:
    """Create or revive the account row for one platform identity."""
    tokens = profile.tokens or grant_tokens

    account = db.scalar(
        SocialAccount.scoped(workspace_id, include_deleted=True).where(
            SocialAccount.platform == platform,
            SocialAccount.platform_account_id == profile.platform_account_id,
        )
    )
    if account is None:
        account = SocialAccount(
            workspace_id=workspace_id,
            platform=platform,
            platform_account_id=profile.platform_account_id,
            display_name=profile.display_name,
            connected_by_id=user_id,
        )
        db.add(account)
        db.flush()  # assign the id the credential row needs
    else:
        # Reconnecting a previously disconnected account revives the same row,
        # so historical post targets keep pointing at a live account.
        account.deleted_at = None
        account.display_name = profile.display_name

    account.username = profile.username
    account.avatar_url = profile.avatar_url
    account.status = SocialAccountStatus.CONNECTED
    account.token_expires_at = tokens.expires_at
    account.last_synced_at = utcnow()
    if account.connected_by_id is None:
        account.connected_by_id = user_id

    _store_credentials(db, account, tokens)
    return account


def _store_credentials(
    db: Session, account: SocialAccount, tokens: OAuthTokens
) -> None:
    """Write the account's tokens into the vault, encrypting them first."""
    credential = account.credentials
    if credential is None:
        credential = OAuthCredential(social_account_id=account.id)
        db.add(credential)
    else:
        credential.rotated_at = utcnow()

    credential.access_token_encrypted = encrypt_secret(tokens.access_token)
    # A platform that omits the refresh token on a refresh (Google) must keep
    # the stored one; an empty value would break the next refresh.
    if tokens.refresh_token:
        credential.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
    credential.token_type = tokens.token_type
    credential.scopes = list(tokens.scopes)
    credential.expires_at = tokens.expires_at
    db.flush()


# ---------------------------------------------------------------------------
# Step 3 — keep alive
# ---------------------------------------------------------------------------
def refresh_account(db: Session, account: SocialAccount) -> SocialAccount:
    """Exchange the stored refresh token for a fresh access token.

    Raises:
        AccountCredentialError: the account has no refresh token.
        OAuthTokenError / OAuthApiError: the platform refused the refresh.
    """
    provider = get_provider(account.platform)
    provider.require_configured()
    current = read_tokens(account)
    if not current.refresh_token:
        raise AccountCredentialError(
            "This account has no refresh token. Reconnect it to continue publishing."
        )

    refreshed = provider.refresh(refresh_token=current.refresh_token)
    # Google omits ``refresh_token`` on refresh; keep the one in the vault.
    if not refreshed.refresh_token:
        refreshed = replace(refreshed, refresh_token=current.refresh_token)
    if not refreshed.scopes:
        refreshed = replace(refreshed, scopes=current.scopes)

    _store_credentials(db, account, refreshed)
    account.status = SocialAccountStatus.CONNECTED
    account.token_expires_at = refreshed.expires_at
    account.last_synced_at = utcnow()
    db.commit()
    logger.info(
        "oauth tokens refreshed",
        extra={"platform": account.platform.value, "account_id": str(account.id)},
    )
    return account


def ensure_fresh_tokens(db: Session, account: SocialAccount) -> OAuthTokens:
    """Tokens guaranteed usable now — refreshed when they are about to expire.

    A token that is already dead and has no refresh token flips the account to
    ``expired`` and raises, so a caller (the publishing engine) fails with a
    clear "reconnect" message instead of a platform error.
    """
    tokens = read_tokens(account)
    if not tokens.is_expired(margin_seconds=settings.oauth_refresh_margin_seconds):
        return tokens

    if tokens.refresh_token:
        refresh_account(db, account)
        return read_tokens(account)

    account.status = SocialAccountStatus.EXPIRED
    db.commit()
    raise AccountNeedsReconnectError(
        f"The {account.display_name} connection has expired and cannot be "
        f"refreshed automatically. Reconnect the account to continue."
    )


# ---------------------------------------------------------------------------
# Step 4 — end
# ---------------------------------------------------------------------------
def disconnect_account(db: Session, account: SocialAccount) -> bool:
    """Revoke upstream (best effort) and destroy the local connection.

    Returns ``True`` when the platform confirmed the grant is gone. The account
    row is soft-deleted so historical post targets keep their reference, while
    the credential vault row — the only copy of the secret — is deleted.
    """
    revoked = False
    provider = get_provider(account.platform)
    if provider.is_configured():
        try:
            revoked = provider.revoke(read_tokens(account))
        except (OAuthError, AccountCredentialError) as exc:
            # Revocation is a courtesy: the local connection still goes away.
            logger.warning(
                "upstream oauth revocation failed",
                extra={
                    "platform": account.platform.value,
                    "account_id": str(account.id),
                    "error": str(exc),
                },
            )

    if account.credentials is not None:
        db.delete(account.credentials)
    account.status = SocialAccountStatus.REVOKED
    account.deleted_at = utcnow()
    db.commit()
    logger.info(
        "social account disconnected",
        extra={
            "platform": account.platform.value,
            "account_id": str(account.id),
            "revoked_upstream": revoked,
        },
    )
    return revoked


__all__ = [
    "AccountCredentialError",
    "AccountError",
    "AccountNeedsReconnectError",
    "AccountNotFoundError",
    "begin_authorization",
    "complete_connection",
    "connected_counts",
    "disconnect_account",
    "ensure_fresh_tokens",
    "get_account",
    "list_accounts",
    "platform_is_configured",
    "provider_for",
    "read_tokens",
    "refresh_account",
]
