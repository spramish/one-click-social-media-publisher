"""Workspace and membership endpoints.

These routes exist to prove the two Phase 3 authorization layers:

* **tenant isolation** — every lookup starts from
  :func:`app.api.deps.get_workspace_context`, which uses
  ``TenantMixin.scoped`` and answers ``404`` for a workspace the caller is not a
  member of;
* **role-based access** — destructive operations are guarded by
  ``require_permission(...)`` against the seeded permission codes stored in
  ``roles.permissions``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_current_user, require_permission
from app.core.permissions import MEMBER_REMOVE, WORKSPACE_READ
from app.db.session import get_db
from app.models.user import User
from app.schemas import auth as auth_schemas
from app.schemas import workspace as schemas
from app.services import workspaces as workspaces_service

router = APIRouter()


@router.get(
    "",
    response_model=list[schemas.WorkspacePublic],
    summary="List the workspaces I belong to",
)
def list_workspaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[schemas.WorkspacePublic]:
    """Every workspace the caller is an active member of, with role and permissions."""
    return [
        schemas.WorkspacePublic.from_workspace(workspace, role, user_id=user.id)
        for workspace, role in workspaces_service.list_workspaces_for_user(db, user.id)
    ]


@router.get(
    "/{workspace_id}",
    response_model=schemas.WorkspacePublic,
    summary="Get one workspace",
)
def get_workspace(
    context: WorkspaceContext = Depends(require_permission(WORKSPACE_READ)),
) -> schemas.WorkspacePublic:
    """Return a single workspace. Non-members receive ``404``, never ``403``."""
    return schemas.WorkspacePublic.from_workspace(
        context.workspace,
        context.role,
        user_id=context.member.user_id,
    )


@router.get(
    "/{workspace_id}/members",
    response_model=list[schemas.WorkspaceMemberPublic],
    summary="List workspace members",
)
def list_members(
    context: WorkspaceContext = Depends(require_permission(WORKSPACE_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.WorkspaceMemberPublic]:
    """Members of this workspace only — the scope comes from the context."""
    return [
        schemas.WorkspaceMemberPublic.from_row(member, user, role)
        for member, user, role in workspaces_service.list_members(db, context.workspace.id)
    ]


@router.delete(
    "/{workspace_id}/members/{member_id}",
    response_model=auth_schemas.MessageResponse,
    summary="Remove a member from the workspace",
)
def remove_member(
    member_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(MEMBER_REMOVE)),
    db: Session = Depends(get_db),
) -> auth_schemas.MessageResponse:
    """Remove a membership. Requires ``member:remove`` (owner/admin only).

    The owner can never be removed, and a viewer's ``403`` is produced by the
    permission guard before this body runs.
    """
    try:
        workspaces_service.remove_member(db, context.workspace.id, member_id)
    except workspaces_service.MemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except workspaces_service.CannotRemoveOwnerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.commit()
    return auth_schemas.MessageResponse(message="Member removed from the workspace.")
