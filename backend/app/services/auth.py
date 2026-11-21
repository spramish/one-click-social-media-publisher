"""Authentication service.

Everything the ``/auth`` endpoints need lives here so the routes stay thin and
the rules stay testable without HTTP:

* registration (a user always gets a personal workspace and the ``owner`` role);
* credential verification with timing-equalised failure paths;
* refresh-token **rotation with reuse detection** (see :func:`rotate_session`);
* session listing/revocation for the "manage devices" screen;
* email verification and password reset via single-use, hashed tokens.

Functions flush but never commit — the caller owns the transaction.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    password_needs_rehash,
    utcnow,
    verify_password,
)
from app.db.enums import VerificationPurpose, WorkspaceMemberStatus
from app.models.auth import AuthSession, VerificationToken
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember
from app.services.email import EmailMessage, send_email

logger = get_logger(__name__)

OWNER_ROLE_NAME = "owner"

#: Reasons recorded in ``auth_sessions.revoked_reason``.
REASON_ROTATED = "rotated"
REASON_LOGOUT = "logout"
REASON_LOGOUT_ALL = "logout_all"
REASON_PASSWORD_CHANGED = "password_changed"
REASON_PASSWORD_RESET = "password_reset"
REASON_REUSE_DETECTED = "reuse_detected"
REASON_SESSION_LIMIT = "session_limit"
REASON_REVOKED_BY_USER = "revoked_by_user"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AuthError(Exception):
    """Base class for authentication failures (mapped to HTTP by the routes)."""


class EmailAlreadyRegisteredError(AuthError):
    """The requested email already belongs to an account."""


class InvalidCredentialsError(AuthError):
    """Email/password combination is wrong, or the account is not usable."""


class InvalidTokenError(AuthError):
    """A one-time or refresh token is unknown, expired, or already spent."""


class PasswordReuseError(AuthError):
    """The new password is identical to the stored one."""


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A session row plus the clear-text refresh token, returned exactly once."""

    session: AuthSession
    refresh_token: str


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def normalise_email(email: str) -> str:
    """Normalise an address so ``A@B.com`` and ``a@b.com`` are one account."""
    return email.strip().lower()


def get_user_by_email(
    db: Session,
    email: str,
    *,
    include_deleted: bool = False,
) -> User | None:
    """Fetch an account by email.

    ``include_deleted`` also matches soft-deleted rows: the unique index on
    ``users.email`` ignores soft deletion, so registration must check for them
    too or it would fail with an ``IntegrityError`` instead of a clear 409.
    """
    stmt = select(User).where(User.email == normalise_email(email))
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    return db.scalar(stmt)


def get_role(db: Session, name: str) -> Role:
    """Return a seeded role by name."""
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:  # pragma: no cover - guarded by the seed migration
        raise RuntimeError(f"role {name!r} is missing; run 'alembic upgrade head'")
    return role


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:50] or "workspace"


def _unique_workspace_slug(db: Session, base: str) -> str:
    """Derive a globally unique workspace slug (soft-deleted rows included)."""
    root = _slugify(base)
    candidate = root
    suffix = 1
    while db.scalar(select(Workspace.id).where(Workspace.slug == candidate)) is not None:
        suffix += 1
        candidate = f"{root}-{suffix}"
    return candidate


def register_user(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    workspace_name: str | None = None,
) -> tuple[User, Workspace]:
    """Create an account, its personal workspace and the owner membership.

    Returns:
        The new ``User`` and ``Workspace`` (both flushed, not committed).

    Raises:
        EmailAlreadyRegisteredError: the email is taken.
    """
    normalised = normalise_email(email)
    if get_user_by_email(db, normalised, include_deleted=True) is not None:
        raise EmailAlreadyRegisteredError("an account with that email already exists")

    user = User(
        email=normalised,
        hashed_password=hash_password(password),
        full_name=full_name.strip() if full_name else None,
        is_active=True,
        is_verified=False,
        is_superuser=False,
    )
    db.add(user)
    db.flush()

    default_name = f"{(full_name.strip() if full_name else normalised.split('@')[0])}'s workspace"
    workspace = Workspace(
        name=workspace_name.strip() if workspace_name else default_name[:120],
        slug=_unique_workspace_slug(db, workspace_name or default_name),
        owner_id=user.id,
    )
    db.add(workspace)
    db.flush()

    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role_id=get_role(db, OWNER_ROLE_NAME).id,
            status=WorkspaceMemberStatus.ACTIVE,
            joined_at=utcnow(),
        )
    )
    db.flush()

    logger.info("user registered", extra={"user_id": str(user.id), "workspace_id": str(workspace.id)})
    return user, workspace


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    """A valid bcrypt hash used to equalise timing for unknown accounts.

    Without it, a request for a missing email returns in microseconds while a
    wrong password takes a full bcrypt verification — a trivial account
    enumeration oracle.
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("timing-equalisation-placeholder")
    return _DUMMY_HASH


def authenticate_user(db: Session, *, email: str, password: str) -> User:
    """Verify credentials and record the login.

    Raises:
        InvalidCredentialsError: unknown email, wrong password, or a disabled
            or soft-deleted account. The message is identical in every case.
    """
    user = get_user_by_email(db, email)
    if user is None:
        verify_password(password, _dummy_hash())
        raise InvalidCredentialsError("invalid email or password")

    if not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError("invalid email or password")

    if not user.is_active:
        raise InvalidCredentialsError("invalid email or password")

    # Transparently upgrade a hash created with an older work factor.
    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)

    user.last_login_at = utcnow()
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Sessions (refresh-token rotation)
# ---------------------------------------------------------------------------
def _refresh_expiry() -> datetime:
    return utcnow() + timedelta(days=settings.refresh_token_expire_days)


def _active_session_filter(user_id: uuid.UUID) -> tuple:
    return (
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > utcnow(),
    )


def _enforce_session_limit(db: Session, user_id: uuid.UUID) -> None:
    """Revoke the oldest sessions so a new login fits inside the limit."""
    limit = settings.max_active_sessions
    if limit <= 0:
        return
    stale = db.scalars(
        select(AuthSession)
        .where(*_active_session_filter(user_id))
        .order_by(AuthSession.created_at.desc())
        .offset(limit - 1)
    ).all()
    for session in stale:
        _revoke(db, session, reason=REASON_SESSION_LIMIT, when=utcnow())


def _new_session(
    db: Session,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    raw_token = generate_opaque_token()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_opaque_token(raw_token),
        family_id=family_id or uuid.uuid4(),
        expires_at=_refresh_expiry(),
        user_agent=user_agent[:255] if user_agent else None,
        ip_address=ip_address[:45] if ip_address else None,
        last_used_at=utcnow(),
    )
    db.add(session)
    db.flush()
    return IssuedSession(session=session, refresh_token=raw_token)


def _revoke(
    db: Session,
    session: AuthSession,
    *,
    reason: str,
    when: datetime | None = None,
) -> None:
    if session.revoked_at is None:
        session.revoked_at = when or utcnow()
        session.revoked_reason = reason
        db.flush()


def revoke_family(db: Session, family_id: uuid.UUID, *, reason: str) -> int:
    """Revoke every session in a rotation family. Returns how many were live."""
    live = db.scalars(
        select(AuthSession).where(
            AuthSession.family_id == family_id,
            AuthSession.revoked_at.is_(None),
        )
    ).all()
    now = utcnow()
    for session in live:
        session.revoked_at = now
        session.revoked_reason = reason
    if live:
        db.flush()
    return len(live)


def create_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    """Start a new session (a fresh rotation family)."""
    _enforce_session_limit(db, user.id)
    return _new_session(db, user, user_agent=user_agent, ip_address=ip_address)


def rotate_session(
    db: Session,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    """Exchange a refresh token for a new one, rotating the chain.

    Trade-off: revocation on reuse only works if the presented token is
    unknown to an attacker who stole a *copy* and raced the legitimate client.
    Because every rotation writes the successor's ``replaced_by_id`` and keeps
    the family identifier, presenting a token whose ``revoked_reason`` is
    ``rotated`` is treated as theft and the **whole family** is revoked.

    Raises:
        InvalidTokenError: unknown, expired, revoked, or replayed token.
    """
    digest = hash_opaque_token(refresh_token)
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == digest))
    if session is None:
        raise InvalidTokenError("refresh token is not recognised")

    if session.revoked_at is not None:
        if session.revoked_reason == REASON_ROTATED:
            revoked = revoke_family(db, session.family_id, reason=REASON_REUSE_DETECTED)
            logger.warning(
                "refresh token reuse detected",
                extra={"user_id": str(session.user_id), "family_id": str(session.family_id),
                       "sessions_revoked": revoked},
            )
        raise InvalidTokenError("refresh token is no longer valid")

    if session.expires_at <= utcnow():
        _revoke(db, session, reason="expired", when=session.expires_at)
        raise InvalidTokenError("refresh token has expired")

    user = session.user
    if user is None or user.deleted_at is not None or not user.is_active:
        _revoke(db, session, reason=REASON_LOGOUT)
        raise InvalidTokenError("refresh token is no longer valid")

    issued = _new_session(
        db,
        user,
        family_id=session.family_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.revoked_at = utcnow()
    session.revoked_reason = REASON_ROTATED
    session.replaced_by_id = issued.session.id
    session.last_used_at = session.revoked_at
    db.flush()
    return issued


def revoke_session(db: Session, session: AuthSession, *, reason: str = REASON_LOGOUT) -> None:
    """Revoke a single session (logout on one device)."""
    _revoke(db, session, reason=reason)


def revoke_by_refresh_token(
    db: Session,
    refresh_token: str,
    *,
    reason: str = REASON_LOGOUT,
) -> bool:
    """Revoke the session identified by a refresh token.

    Logout must not depend on a *valid access token* — a client with an expired
    access token still has to be able to discard its refresh token. Returns
    ``True`` when a live session was revoked.
    """
    digest = hash_opaque_token(refresh_token)
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == digest))
    if session is None or session.revoked_at is not None:
        return False
    _revoke(db, session, reason=reason)
    return True


def revoke_all_sessions(
    db: Session,
    user_id: uuid.UUID,
    *,
    reason: str = REASON_LOGOUT_ALL,
    except_session_id: uuid.UUID | None = None,
) -> int:
    """Revoke every live session for a user, optionally sparing one."""
    stmt = select(AuthSession).where(*_active_session_filter(user_id))
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    sessions = db.scalars(stmt).all()
    now = utcnow()
    for session in sessions:
        session.revoked_at = now
        session.revoked_reason = reason
    if sessions:
        db.flush()
    return len(sessions)


def list_sessions(db: Session, user_id: uuid.UUID) -> list[AuthSession]:
    """Live sessions for a user, newest first (drives the devices screen)."""
    return list(
        db.scalars(
            select(AuthSession)
            .where(*_active_session_filter(user_id))
            .order_by(AuthSession.created_at.desc())
        ).all()
    )


def get_session(db: Session, session_id: uuid.UUID) -> AuthSession | None:
    """Fetch a session by id (used by the "revoke this device" endpoint)."""
    return db.get(AuthSession, session_id)


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------
def _expiry_for(purpose: VerificationPurpose) -> datetime:
    if purpose is VerificationPurpose.PASSWORD_RESET:
        return utcnow() + timedelta(minutes=settings.password_reset_expire_minutes)
    return utcnow() + timedelta(hours=settings.email_verification_expire_hours)


def issue_verification_token(
    db: Session,
    user: User,
    purpose: VerificationPurpose,
) -> tuple[VerificationToken, str]:
    """Issue a single-use token, invalidating earlier unused ones."""
    db.execute(
        update(VerificationToken)
        .where(
            VerificationToken.user_id == user.id,
            VerificationToken.purpose == purpose,
            VerificationToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )
    raw_token = generate_opaque_token()
    token = VerificationToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_opaque_token(raw_token),
        expires_at=_expiry_for(purpose),
    )
    db.add(token)
    db.flush()
    return token, raw_token


def consume_verification_token(
    db: Session,
    raw_token: str,
    purpose: VerificationPurpose,
) -> VerificationToken:
    """Validate and burn a one-time token.

    Raises:
        InvalidTokenError: unknown, already used, or expired.
    """
    digest = hash_opaque_token(raw_token)
    token = db.scalar(
        select(VerificationToken).where(
            VerificationToken.token_hash == digest,
            VerificationToken.purpose == purpose,
        )
    )
    if token is None:
        raise InvalidTokenError("token is not recognised")
    if token.used_at is not None:
        raise InvalidTokenError("token has already been used")
    if token.expires_at <= utcnow():
        raise InvalidTokenError("token has expired")

    token.used_at = utcnow()
    db.flush()
    return token


def send_verification_email(db: Session, user: User) -> None:
    """Issue an email-verification token and deliver the confirmation link."""
    _, raw_token = issue_verification_token(db, user, VerificationPurpose.EMAIL_VERIFICATION)
    link = f"{settings.frontend_base_url.rstrip('/')}/verify-email?token={raw_token}"
    send_email(
        EmailMessage(
            to=user.email,
            subject="Confirm your email address",
            text_body=(
                f"Hi {user.full_name or user.email},\n\n"
                "Confirm your email address to finish setting up your account:\n\n"
                f"{link}\n\n"
                f"The link expires in {settings.email_verification_expire_hours} hours. "
                "If you did not create this account you can ignore this message.\n"
            ),
        )
    )


def request_email_verification(db: Session, user: User) -> bool:
    """(Re)send the verification email. Returns ``False`` if already verified."""
    if user.is_verified:
        return False
    send_verification_email(db, user)
    return True


def verify_email(db: Session, raw_token: str) -> User:
    """Consume a verification token and mark the account verified."""
    token = consume_verification_token(db, raw_token, VerificationPurpose.EMAIL_VERIFICATION)
    user = token.user
    user.is_verified = True
    db.flush()
    logger.info("email verified", extra={"user_id": str(user.id)})
    return user


# ---------------------------------------------------------------------------
# Password reset / change
# ---------------------------------------------------------------------------
def _apply_new_password(db: Session, user: User, new_password: str) -> None:
    if verify_password(new_password, user.hashed_password):
        raise PasswordReuseError("the new password must differ from the current one")
    user.hashed_password = hash_password(new_password)
    db.flush()


def request_password_reset(db: Session, email: str) -> bool:
    """Email a reset link. Returns ``False`` for an unknown/inactive account.

    The caller must return the same response either way so the endpoint cannot
    be used to discover which addresses have accounts.
    """
    user = get_user_by_email(db, email)
    if user is None or not user.is_active:
        return False

    _, raw_token = issue_verification_token(db, user, VerificationPurpose.PASSWORD_RESET)
    link = f"{settings.frontend_base_url.rstrip('/')}/reset-password?token={raw_token}"
    send_email(
        EmailMessage(
            to=user.email,
            subject="Reset your password",
            text_body=(
                f"Hi {user.full_name or user.email},\n\n"
                "Use the link below to choose a new password:\n\n"
                f"{link}\n\n"
                f"The link expires in {settings.password_reset_expire_minutes} minutes. "
                "If you did not request a reset, no action is needed — your password is unchanged.\n"
            ),
        )
    )
    return True


def reset_password(db: Session, raw_token: str, new_password: str) -> User:
    """Complete a password reset and revoke every existing session."""
    token = consume_verification_token(db, raw_token, VerificationPurpose.PASSWORD_RESET)
    user = token.user
    _apply_new_password(db, user, new_password)
    revoked = revoke_all_sessions(db, user.id, reason=REASON_PASSWORD_RESET)
    logger.info(
        "password reset completed",
        extra={"user_id": str(user.id), "sessions_revoked": revoked},
    )
    return user


def change_password(
    db: Session,
    user: User,
    *,
    current_password: str,
    new_password: str,
    keep_session_id: uuid.UUID | None = None,
) -> int:
    """Change a password from an authenticated session.

    Every *other* session is revoked, so a stolen session cannot survive the
    password change. Returns the number of revoked sessions.

    Raises:
        InvalidCredentialsError: ``current_password`` is wrong.
        PasswordReuseError: the new password equals the current one.
    """
    if not verify_password(current_password, user.hashed_password):
        raise InvalidCredentialsError("current password is incorrect")
    _apply_new_password(db, user, new_password)
    revoked = revoke_all_sessions(
        db,
        user.id,
        reason=REASON_PASSWORD_CHANGED,
        except_session_id=keep_session_id,
    )
    logger.info("password changed", extra={"user_id": str(user.id), "sessions_revoked": revoked})
    return revoked
