"""Workspace queries and membership management.

Reads go through :meth:`app.db.mixins.TenantMixin.scoped` so the workspace
discriminator is applied by the same helper everywhere — a route can never
forget it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.enums import WorkspaceMemberStatus
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember

logger = get_logger(__name__)


class WorkspaceError(Exception):
    """Base class for workspace domain failures."""


class MemberNotFoundError(WorkspaceError):
    """No such membership inside this workspace."""


class CannotRemoveOwnerError(WorkspaceError):
    """The workspace owner cannot be removed from their own workspace."""


def list_workspaces_for_user(db: Session, user_id: uuid.UUID) -> list[tuple[Workspace, Role]]:
    """Every workspace the user is an active member of, oldest first.

    This is intentionally the one query that is *not* workspace-scoped: the
    user id itself is the discriminator, and the result is what the tenant
    switcher in the UI renders.
    """
    rows = db.execute(
        select(Workspace, Role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .join(Role, Role.id == WorkspaceMember.role_id)
        .where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == WorkspaceMemberStatus.ACTIVE,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at.asc())
    ).all()
    return [(workspace, role) for workspace, role in rows]


def get_membership(
    db: Session,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> WorkspaceMember | None:
    """The user's active membership in one workspace, if any."""
    return db.scalar(
        WorkspaceMember.scoped(workspace_id).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == WorkspaceMemberStatus.ACTIVE,
        )
    )


def list_members(
    db: Session,
    workspace_id: uuid.UUID,
) -> list[tuple[WorkspaceMember, User, Role]]:
    """Members of one workspace with their user and role, owners first."""
    rows = db.execute(
        WorkspaceMember.scoped(workspace_id)
        .join(User, User.id == WorkspaceMember.user_id)
        .join(Role, Role.id == WorkspaceMember.role_id)
        .add_columns(User, Role)
        .where(User.deleted_at.is_(None))
        .order_by(WorkspaceMember.created_at.asc())
    ).all()
    return [(member, user, role) for member, user, role in rows]


def remove_member(db: Session, workspace_id: uuid.UUID, member_id: uuid.UUID) -> None:
    """Remove a membership.

    Raises:
        MemberNotFoundError: the membership does not belong to this workspace.
        CannotRemoveOwnerError: the membership is the workspace owner.
    """
    member = db.scalar(
        WorkspaceMember.scoped(workspace_id).where(WorkspaceMember.id == member_id)
    )
    if member is None:
        raise MemberNotFoundError("member not found in this workspace")

    workspace = db.get(Workspace, workspace_id)
    if workspace is not None and workspace.owner_id == member.user_id:
        raise CannotRemoveOwnerError("the workspace owner cannot be removed")

    db.delete(member)
    db.flush()
    logger.info(
        "workspace member removed",
        extra={"workspace_id": str(workspace_id), "member_id": str(member_id)},
    )
