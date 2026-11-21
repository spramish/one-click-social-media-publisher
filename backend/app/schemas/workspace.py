"""Request/response schemas for workspaces and their members.

Both models expose the caller's **effective permissions** so the frontend can
hide actions it is not allowed to perform. That is a usability aid only —
every route still re-checks the permission server-side.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.db.enums import WorkspaceMemberStatus
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember


class WorkspacePublic(BaseModel):
    """A workspace together with the requesting user's role in it."""

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    is_owner: bool
    role: str
    permissions: list[str]

    @classmethod
    def from_workspace(
        cls,
        workspace: Workspace,
        role: Role,
        *,
        user_id: uuid.UUID,
    ) -> "WorkspacePublic":
        return cls(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            created_at=workspace.created_at,
            is_owner=workspace.owner_id == user_id,
            role=role.name,
            permissions=sorted(role.permissions or []),
        )


class WorkspaceMemberPublic(BaseModel):
    """A single membership row, flattened with its user and role."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: str
    status: WorkspaceMemberStatus
    joined_at: datetime | None = None

    @classmethod
    def from_row(
        cls,
        member: WorkspaceMember,
        user: User,
        role: Role,
    ) -> "WorkspaceMemberPublic":
        return cls(
            id=member.id,
            user_id=member.user_id,
            email=user.email,
            full_name=user.full_name,
            role=role.name,
            status=member.status,
            joined_at=member.joined_at,
        )
