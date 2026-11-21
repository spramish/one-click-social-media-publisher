"""Connected-account endpoints: OAuth connect, callback, refresh, disconnect.

Three rules shape this module:

* **The workspace always comes from the URL, never from the body.** Every
  workspace route starts at :func:`app.api.deps.require_permission`, which
  resolves ``{workspace_id}`` through ``TenantMixin.scoped`` and answers ``404``
  for a workspace the caller is not a member of.
* **No secret is ever returned.** The dashboard receives identity, status and
  credential *metadata*; the tokens stay encrypted in the vault.
* **The callback is unauthenticated by necessity.** The browser arrives from the
  platform with no session; the ``state`` value — a single-use, expiring,
  256-bit secret — is the authorization. It is validated (and consumed) by
  :func:`app.services.accounts.complete_connection`.

Because the callback lands in a browser it always answers with a redirect, even
on failure: the outcome is reported as query parameters on
``OAUTH_SUCCESS_REDIRECT`` / ``OAUTH_FAILURE_REDIRECT``.

Error translation is centralised in :func:`_translate` so every account failure
maps to one documented status code.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.config import settings
from app.core.crypto import EncryptionError
from app.core.logging import get_logger
from app.core.permissions import ACCOUNT_CONNECT, ACCOUNT_DISCONNECT, ACCOUNT_READ
from app.db.enums import SocialAccountStatus, SocialPlatform
from app.db.session import get_db
from app.schemas import account as schemas
from app.services import accounts as accounts_service
from app.services.oauth import (
    OAuthApiError,
    OAuthAuthorizationError,
    OAuthError,
    OAuthNotConfiguredError,
    OAuthStateError,
    OAuthTokenError,
    supported_platforms,
)

router = APIRouter()
logger = get_logger(__name__)

#: The error taxonomy every account route funnels through its translator.
_HANDLED = (accounts_service.AccountError, OAuthError, EncryptionError)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map a domain failure onto the documented status code."""
    if isinstance(exc, OAuthNotConfiguredError):
        # Not the caller's fault: this server has no client credentials for it.
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, accounts_service.AccountNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(
        exc,
        (
            accounts_service.AccountCredentialError,
            accounts_service.AccountNeedsReconnectError,
            EncryptionError,
        ),
    ):
        # The request is well-formed but the grant is unusable until reconnect.
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (OAuthStateError, OAuthAuthorizationError, OAuthTokenError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, OAuthApiError):
        # The platform is reachable but refused or failed the call.
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The account operation could not be completed.",
    )


def _redirect(base: str, params: dict[str, str]) -> RedirectResponse:
    """Send the browser to ``base`` with ``params`` merged into its query string."""
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query))
    query.update(params)
    target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/capabilities",
    response_model=schemas.AccountCapabilitiesPublic,
    summary="Which platforms can be connected on this server",
)
def get_capabilities(
    context: WorkspaceContext = Depends(require_permission(ACCOUNT_READ)),
    db: Session = Depends(get_db),
) -> schemas.AccountCapabilitiesPublic:
    """Per-platform configuration state and connection count.

    The frontend reads this before rendering the connect buttons, so an
    integration whose credentials are missing from ``.env`` shows as
    "not configured" rather than failing on click.
    """
    counts = accounts_service.connected_counts(db, context.workspace.id)
    platforms = []
    for platform in supported_platforms():
        provider = accounts_service.provider_for(platform)
        platforms.append(
            schemas.PlatformCapability(
                platform=platform,
                display_name=provider.display_name,
                configured=provider.is_configured(),
                connected_count=counts.get(platform, 0),
            )
        )
    return schemas.AccountCapabilitiesPublic(platforms=platforms)


# ---------------------------------------------------------------------------
# OAuth callback (registered before the two-segment workspace routes so that
# ``/accounts/{platform}/callback`` is matched by *this* pattern, not by
# ``/accounts/{workspace_id}/{account_id}``)
# ---------------------------------------------------------------------------
@router.get(
    "/{platform}/callback",
    summary="OAuth redirect target (browser)",
    response_class=RedirectResponse,
)
def oauth_callback(
    platform: SocialPlatform,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Finish an authorization and bounce the browser back to the dashboard.

    Deliberately unauthenticated: the ``state`` parameter is a single-use,
    expiring secret that is validated and consumed by the service, so it *is*
    the authorization. Success and failure both answer with a redirect.
    """
    try:
        accounts = accounts_service.complete_connection(
            db,
            platform=platform,
            state=state,
            code=code,
            error=error,
            error_description=error_description,
        )
    except _HANDLED as exc:
        logger.warning(
            "oauth callback failed",
            extra={"platform": platform.value, "error": str(exc)},
        )
        return _redirect(
            settings.oauth_failure_redirect,
            {"platform": platform.value, "error": str(exc)},
        )

    return _redirect(
        settings.oauth_success_redirect,
        {"connected": platform.value, "accounts": str(len(accounts))},
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}",
    response_model=list[schemas.SocialAccountPublic],
    summary="List the workspace's connected accounts",
)
def list_accounts(
    workspace_id: uuid.UUID,
    platform: SocialPlatform | None = Query(default=None),
    account_status: SocialAccountStatus | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(require_permission(ACCOUNT_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.SocialAccountPublic]:
    """Connected accounts in this workspace. Disconnected ones are not listed."""
    rows = accounts_service.list_accounts(
        db, context.workspace.id, platform=platform, status=account_status
    )
    return [schemas.SocialAccountPublic.from_account(account) for account in rows]


@router.get(
    "/{workspace_id}/{account_id}",
    response_model=schemas.SocialAccountPublic,
    summary="Get one connected account",
)
def get_account(
    account_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(ACCOUNT_READ)),
    db: Session = Depends(get_db),
) -> schemas.SocialAccountPublic:
    """Another workspace's account id answers ``404``, never ``403``."""
    try:
        account = accounts_service.get_account(db, context.workspace.id, account_id)
    except accounts_service.AccountError as exc:
        raise _translate(exc) from exc
    return schemas.SocialAccountPublic.from_account(account)


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/{platform}/authorize",
    response_model=schemas.AuthorizationStartPublic,
    summary="Start an OAuth authorization and return the consent URL",
)
def authorize(
    platform: SocialPlatform,
    context: WorkspaceContext = Depends(require_permission(ACCOUNT_CONNECT)),
    db: Session = Depends(get_db),
) -> schemas.AuthorizationStartPublic:
    """Create the single-use ``state`` and hand back the platform's consent URL.

    ``503`` means this server has no client credentials for ``platform`` — the
    operator must fill them in before anyone can connect.
    """
    try:
        url, expires_at = accounts_service.begin_authorization(
            db,
            workspace_id=context.workspace.id,
            user_id=context.member.user_id,
            platform=platform,
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return schemas.AuthorizationStartPublic(
        platform=platform,
        authorization_url=url,
        expires_at=expires_at,
        state_ttl_seconds=settings.oauth_state_ttl_seconds,
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/{account_id}/refresh",
    response_model=schemas.SocialAccountPublic,
    summary="Refresh a connected account's access token",
)
def refresh_account(
    account_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(ACCOUNT_CONNECT)),
    db: Session = Depends(get_db),
) -> schemas.SocialAccountPublic:
    """Exchanges the stored refresh token for a new access token.

    ``409`` means there is nothing to refresh with (no refresh token, or the
    stored credential can no longer be decrypted) and the user must reconnect.
    """
    try:
        account = accounts_service.get_account(db, context.workspace.id, account_id)
        account = accounts_service.refresh_account(db, account)
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return schemas.SocialAccountPublic.from_account(account)


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------
@router.delete(
    "/{workspace_id}/{account_id}",
    response_model=schemas.AccountDisconnectResult,
    summary="Disconnect an account and delete its stored tokens",
)
def disconnect_account(
    account_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(ACCOUNT_DISCONNECT)),
    db: Session = Depends(get_db),
) -> schemas.AccountDisconnectResult:
    """Revokes upstream where the platform supports it, then destroys the vault row.

    The account row is soft-deleted so historical post targets keep their
    reference. ``revoked_upstream`` reports whether the platform confirmed the
    grant is gone.
    """
    try:
        account = accounts_service.get_account(db, context.workspace.id, account_id)
        revoked = accounts_service.disconnect_account(db, account)
    except _HANDLED as exc:
        raise _translate(exc) from exc

    message = (
        "Account disconnected and the platform grant revoked."
        if revoked
        else "Account disconnected. The platform does not support programmatic "
        "revocation, so you may still see this app under your authorised apps."
    )
    return schemas.AccountDisconnectResult(message=message, revoked_upstream=revoked)


__all__ = ["router"]
