"""Workspace (tenant) and membership models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import WorkspaceMemberStatus
from app.db.mixins import (
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.social_account import SocialAccount
    from app.models.user import User


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Global role catalogue.

    ``permissions`` holds permission codes (for example ``"post:create"``).
    Roles are global rather than per-workspace so that permission checks are a
    single lookup; the seeded system roles are ``owner``, ``admin``, ``editor``
    and ``viewer``.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="role",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Role id={self.id} name={self.name!r}>"


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """The tenant boundary. Every business row belongs to exactly one workspace."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    owner: Mapped["User"] = relationship("User", back_populates="owned_workspaces")
    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    social_accounts: Mapped[list["SocialAccount"]] = relationship(
        "SocialAccount",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Workspace id={self.id} slug={self.slug!r}>"


class WorkspaceMember(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Links a user to a workspace with exactly one role.

    ``workspace_id`` is supplied by :class:`~app.db.mixins.TenantMixin`.
    """

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[WorkspaceMemberStatus] = mapped_column(
        enum_column(WorkspaceMemberStatus, "workspace_member_status"),
        nullable=False,
        server_default=WorkspaceMemberStatus.ACTIVE.value,
    )
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="members")
    user: Mapped["User"] = relationship(
        "User",
        back_populates="memberships",
        foreign_keys=[user_id],
    )
    role: Mapped["Role"] = relationship("Role", back_populates="members")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<WorkspaceMember workspace={self.workspace_id} user={self.user_id}>"
