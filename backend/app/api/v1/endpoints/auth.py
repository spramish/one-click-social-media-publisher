"""Authentication endpoints.

Route responsibilities stay thin: validate input (Pydantic), call
:mod:`app.services.auth`, commit, and translate domain errors into HTTP. The
service owns every rule about credentials and tokens.

Token delivery:

* **access token** — JSON body, short-lived, kept in the client's memory;
* **refresh token** — ``HttpOnly`` cookie scoped to ``/api/v1/auth``, rotated on
  every use. It never appears in a response body for browser clients, though
  ``/refresh`` and ``/logout`` also accept it in the body so CLIs and mobile
  apps can use the same endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.cookies import clear_refresh_cookie, read_refresh_cookie, set_refresh_cookie
from app.api.deps import get_current_session, get_current_user
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit
from app.core.security import create_access_token, utcnow
from app.db.enums import WorkspaceMemberStatus
from app.db.session import get_db
from app.models.auth import AuthSession
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.schemas import auth as schemas
from app.services import audit as audit_service
from app.services import auth as auth_service

router = APIRouter()
logger = get_logger(__name__)

#: One counter shared by every credential endpoint, keyed on the caller's
#: address: ten secret guesses a minute across login/register/reset combined.
_auth_rate_limit = rate_limit("auth", limit=settings.rate_limit_auth_per_minute)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client_ip(request: Request) -> str | None:
    """Best-effort client address (first hop of ``X-Forwarded-For`` behind a proxy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _primary_workspace_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """The workspace a *sign-in* is filed under: the user's oldest membership.

    The audit read route is workspace-scoped, so an event with no workspace — a
    login, which is not tied to one — would be invisible in every trail. Filing
    it under the account's primary workspace keeps "who signed in" answerable.
    """
    return db.scalar(
        select(WorkspaceMember.workspace_id)
        .where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == WorkspaceMemberStatus.ACTIVE,
        )
        .order_by(WorkspaceMember.created_at.asc())
        .limit(1)
    )


def _build_auth_response(
    user: User,
    issued: auth_service.IssuedSession,
    response: Response,
) -> schemas.AuthResponse:
    """Mint an access token, plant the refresh cookie, and shape the body."""
    access_token, expires_at = create_access_token(
        user_id=user.id,
        session_id=issued.session.id,
    )
    set_refresh_cookie(response, issued.refresh_token, issued.session.expires_at)
    return schemas.AuthResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=int((expires_at - utcnow()).total_seconds()),
        user=schemas.UserPublic.model_validate(user),
    )


# ---------------------------------------------------------------------------
# Registration & login
# ---------------------------------------------------------------------------
@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.AuthResponse,
    summary="Create an account and sign in",
    dependencies=[Depends(_auth_rate_limit)],
)
def register(
    payload: schemas.RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> schemas.AuthResponse:
    """Register a user.

    A personal workspace is created with the caller as its ``owner``, so the
    account is immediately usable. The account starts unverified: the API sends
    a confirmation link but does not block sign-in on it.
    """
    try:
        user, _workspace = auth_service.register_user(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            workspace_name=payload.workspace_name,
        )
    except auth_service.EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    auth_service.request_email_verification(db, user)
    issued = auth_service.create_session(
        db,
        user,
        user_agent=_user_agent(request),
        ip_address=_client_ip(request),
    )
    response_body = _build_auth_response(user, issued, response)
    audit_service.record(
        db,
        action=audit_service.REGISTERED,
        user_id=user.id,
        workspace_id=_workspace.id,
        request=request,
        target_type="user",
        target_id=user.id,
    )
    db.commit()

    logger.info("user registered", extra={"user_id": str(user.id)})
    return response_body


@router.post(
    "/login",
    response_model=schemas.AuthResponse,
    summary="Exchange credentials for tokens",
    dependencies=[Depends(_auth_rate_limit)],
)
def login(
    payload: schemas.LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> schemas.AuthResponse:
    """Authenticate an existing account.

    Unknown email, wrong password and deactivated account all return the same
    ``401`` with the same message.
    """
    try:
        user = auth_service.authenticate_user(
            db,
            email=payload.email,
            password=payload.password,
        )
    except auth_service.InvalidCredentialsError as exc:
        logger.info("failed login attempt", extra={"email": auth_service.normalise_email(payload.email)})
        # Audit the rejection, then commit before raising: the HTTPException
        # would otherwise let ``get_db`` roll the entry back.
        known = auth_service.get_user_by_email(db, payload.email)
        audit_service.record(
            db,
            action=audit_service.LOGIN_FAILED,
            user_id=known.id if known else None,
            workspace_id=_primary_workspace_id(db, known.id) if known else None,
            request=request,
            detail={"email": auth_service.normalise_email(payload.email)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    issued = auth_service.create_session(
        db,
        user,
        user_agent=_user_agent(request),
        ip_address=_client_ip(request),
    )
    response_body = _build_auth_response(user, issued, response)
    audit_service.record(
        db,
        action=audit_service.LOGIN_SUCCEEDED,
        user_id=user.id,
        workspace_id=_primary_workspace_id(db, user.id),
        request=request,
        target_type="user",
        target_id=user.id,
    )
    db.commit()

    logger.info("user logged in", extra={"user_id": str(user.id)})
    return response_body


@router.post(
    "/refresh",
    response_model=schemas.AuthResponse,
    summary="Rotate the refresh token",
    dependencies=[Depends(_auth_rate_limit)],
)
def refresh(
    request: Request,
    response: Response,
    payload: schemas.RefreshRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> schemas.AuthResponse:
    """Exchange a refresh token for a new access token **and** a new refresh token.

    Presenting an already-rotated token is treated as theft: every session in
    that rotation family is revoked before the ``401`` is returned.
    """
    raw_token = read_refresh_cookie(request) or (payload.refresh_token if payload else None)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        issued = auth_service.rotate_session(
            db,
            raw_token,
            user_agent=_user_agent(request),
            ip_address=_client_ip(request),
        )
    except auth_service.InvalidTokenError as exc:
        # Commit first: reuse detection has already revoked the family, and the
        # raised HTTPException would otherwise trigger get_db's rollback.
        clear_refresh_cookie(response)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = issued.session.user
    response_body = _build_auth_response(user, issued, response)
    db.commit()
    return response_body


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@router.post(
    "/logout",
    response_model=schemas.MessageResponse,
    summary="Revoke the current session",
    dependencies=[Depends(_auth_rate_limit)],
)
def logout(
    request: Request,
    response: Response,
    payload: schemas.LogoutRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Revoke the session behind the presented refresh token.

    Works without a valid access token so an expired client can still clean up.
    Always returns ``200`` — telling an attacker whether the token existed would
    be an unnecessary oracle.
    """
    raw_token = read_refresh_cookie(request) or (payload.refresh_token if payload else None)
    revoked = bool(raw_token) and auth_service.revoke_by_refresh_token(db, raw_token)
    if revoked:
        audit_service.record(db, action=audit_service.LOGOUT, request=request)
    db.commit()
    clear_refresh_cookie(response)
    return schemas.MessageResponse(
        message="Signed out." if revoked else "No active session was found."
    )


@router.post(
    "/logout-all",
    response_model=schemas.MessageResponse,
    summary="Revoke every session of the current user",
)
def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Sign out everywhere — the "I lost my laptop" button."""
    revoked = auth_service.revoke_all_sessions(
        db,
        user.id,
        reason=auth_service.REASON_LOGOUT_ALL,
    )
    audit_service.record(
        db,
        action=audit_service.LOGOUT_ALL,
        user_id=user.id,
        request=request,
        detail={"revoked": revoked},
    )
    db.commit()
    clear_refresh_cookie(response)
    return schemas.MessageResponse(message=f"Signed out of {revoked} session(s).")


# ---------------------------------------------------------------------------
# Profile & sessions
# ---------------------------------------------------------------------------
@router.get("/me", response_model=schemas.UserPublic, summary="Current account")
def read_me(user: User = Depends(get_current_user)) -> User:
    """Return the authenticated account (never a hash or token)."""
    return user


@router.get(
    "/sessions",
    response_model=list[schemas.SessionPublic],
    summary="List active sessions",
)
def list_sessions(
    user: User = Depends(get_current_user),
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[schemas.SessionPublic]:
    """Active sessions for the current account, newest first."""
    return [
        schemas.SessionPublic.model_validate(row).model_copy(
            update={"current": row.id == session.id}
        )
        for row in auth_service.list_sessions(db, user.id)
    ]


@router.delete(
    "/sessions/{session_id}",
    response_model=schemas.MessageResponse,
    summary="Revoke one session",
)
def revoke_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Revoke a single device. Sessions of other users are invisible (``404``)."""
    target = auth_service.get_session(db, session_id)
    if target is None or target.user_id != user.id or target.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    auth_service.revoke_session(db, target, reason=auth_service.REASON_REVOKED_BY_USER)
    db.commit()
    return schemas.MessageResponse(message="Session revoked.")


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------
@router.post(
    "/password/change",
    response_model=schemas.MessageResponse,
    summary="Change the password of the signed-in account",
)
def change_password(
    payload: schemas.ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Change the password, keeping only the calling session alive."""
    try:
        revoked = auth_service.change_password(
            db,
            user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            keep_session_id=session.id,
        )
    except auth_service.InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except auth_service.PasswordReuseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_service.record(
        db,
        action=audit_service.PASSWORD_CHANGED,
        user_id=user.id,
        request=request,
        target_type="user",
        target_id=user.id,
        detail={"other_sessions_revoked": revoked},
    )
    db.commit()
    return schemas.MessageResponse(
        message=f"Password updated. {revoked} other session(s) signed out."
    )


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------
@router.post(
    "/email/verification",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=schemas.MessageResponse,
    summary="(Re)send the email verification link",
)
def request_email_verification(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Send a fresh confirmation link, invalidating any previous one."""
    sent = auth_service.request_email_verification(db, user)
    db.commit()
    return schemas.MessageResponse(
        message=(
            "Verification email sent."
            if sent
            else "This email address is already verified."
        )
    )


@router.post(
    "/email/verify",
    response_model=schemas.MessageResponse,
    summary="Confirm an email address with a one-time token",
)
def confirm_email(
    payload: schemas.TokenConfirmRequest,
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Consume the emailed token. Unauthenticated by design (the link is the proof)."""
    try:
        auth_service.verify_email(db, payload.token)
    except auth_service.InvalidTokenError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.commit()
    return schemas.MessageResponse(message="Email address verified.")


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
@router.post(
    "/password/reset",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=schemas.MessageResponse,
    summary="Request a password-reset link",
    dependencies=[Depends(_auth_rate_limit)],
)
def request_password_reset(
    payload: schemas.PasswordResetRequest,
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Email a reset link.

    The response is identical whether or not the address exists, so the endpoint
    cannot be used to enumerate accounts.
    """
    auth_service.request_password_reset(db, payload.email)
    db.commit()
    return schemas.MessageResponse(
        message="If an account exists for that address, a reset link has been sent."
    )


@router.post(
    "/password/reset/confirm",
    response_model=schemas.MessageResponse,
    summary="Complete a password reset",
    dependencies=[Depends(_auth_rate_limit)],
)
def confirm_password_reset(
    payload: schemas.PasswordResetConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Set a new password and revoke every existing session."""
    try:
        user = auth_service.reset_password(db, payload.token, payload.new_password)
    except auth_service.InvalidTokenError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except auth_service.PasswordReuseError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_service.record(
        db,
        action=audit_service.PASSWORD_RESET,
        user_id=user.id,
        request=request,
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    clear_refresh_cookie(response)
    return schemas.MessageResponse(message="Password updated. Please sign in again.")
