"""Shared FastAPI dependencies: authentication, authorization, tenant scope.

Three layers of protection, all built on the Phase 2 schema:

1. :func:`get_current_session`  — a valid, live session (access token → session).
2. :func:`get_current_user`     — an active account behind that session.
3. :func:`get_workspace_context`/``require_permission`` — an active membership
   in the target workspace plus the permission codes granted by its role.

A user who is not a member of the requested workspace receives ``404`` rather
than ``403``: otherwise the API would confirm the existence of workspaces the
caller has no business knowing about.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_access_token, utcnow
from app.db.mixins import TenantMixin
from app.db.session import get_db
from app.models.auth import AuthSession
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember
from app.services import workspaces as workspaces_service

#: ``auto_error=False`` so a missing header produces our own 401 envelope
#: (with a ``WWW-Authenticate`` challenge) instead of Starlette's 403.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AccessToken",
    description="Short-lived JWT issued by /api/v1/auth/login",
)

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=_UNAUTHENTICATED_HEADERS,
    )


def get_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthSession:
    """Resolve ``Authorization: Bearer <jwt>`` to a live session row.

    The token is stateless, but the session is not: logout, password changes and
    refresh rotation revoke rows, so an access token whose session died stops
    working immediately instead of surviving until it expires.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized()

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc

    session = db.get(AuthSession, claims.session_id)
    if (
        session is None
        or session.user_id != claims.user_id
        or not session.is_active
    ):
        raise _unauthorized("Session is no longer valid")

    session.last_used_at = utcnow()
    return session


def get_current_user(session: AuthSession = Depends(get_current_session)) -> User:
    """The authenticated, active account."""
    user = session.user
    if user is None or user.deleted_at is not None:
        raise _unauthorized("Session is no longer valid")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        )
    return user


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """The caller's identity *and* authority inside one workspace."""

    workspace: Workspace
    member: WorkspaceMember
    role: Role
    permissions: frozenset[str]

    def has_permission(self, code: str) -> bool:
        return code in self.permissions

    def scoped(self, model: type[TenantMixin]):
        """Shorthand for ``model.scoped(self.workspace.id)``.

        Every tenant query in a workspace route should start here or in a
        service that does the same thing.
        """
        return model.scoped(self.workspace.id)


def get_workspace_context(
    workspace_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceContext:
    """Resolve ``{workspace_id}`` into a context, or raise 404.

    Raises:
        HTTPException: 404 when the workspace does not exist, is soft-deleted,
            or the caller has no active membership — the three cases are
            deliberately indistinguishable to the caller.
    """
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
    )
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    membership = workspaces_service.get_membership(db, workspace_id, user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    role = membership.role
    if role is None:  # pragma: no cover - enforced by the FK
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your membership has no role assigned",
        )

    return WorkspaceContext(
        workspace=workspace,
        member=membership,
        role=role,
        permissions=frozenset(role.permissions or []),
    )


class RequirePermission:
    """Dependency that demands one or more permission codes in the workspace.

    ``is_superuser`` bypasses the permission check but **not** tenant isolation:
    a superuser still needs an active membership to reach a workspace.
    """

    def __init__(self, *permissions: str) -> None:
        if not permissions:
            raise ValueError("RequirePermission needs at least one permission code")
        self.permissions = tuple(permissions)

    def __call__(
        self,
        context: WorkspaceContext = Depends(get_workspace_context),
        user: User = Depends(get_current_user),
    ) -> WorkspaceContext:
        if user.is_superuser:
            return context

        missing = [code for code in self.permissions if code not in context.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Missing required permission for role '{context.role.name}': "
                    f"{', '.join(missing)}"
                ),
            )
        return context


def require_permission(*permissions: str) -> RequirePermission:
    """Usage: ``context = Depends(require_permission(POST_PUBLISH))``."""
    return RequirePermission(*permissions)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Like :func:`get_current_user` but returns ``None`` instead of raising."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError:
        return None
    session = db.get(AuthSession, claims.session_id)
    if session is None or session.user_id != claims.user_id or not session.is_active:
        return None
    return session.user


__all__ = [
    "RequirePermission",
    "WorkspaceContext",
    "bearer_scheme",
    "get_current_session",
    "get_current_user",
    "get_optional_user",
    "get_workspace_context",
    "require_permission",
]
