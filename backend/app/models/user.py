"""User account model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth import AuthSession, VerificationToken
    from app.models.workspace import Workspace, WorkspaceMember


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A platform account.

    Users never own data directly — everything is owned by a workspace and
    reached through :class:`~app.models.workspace.WorkspaceMember`.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owned_workspaces: Mapped[list["Workspace"]] = relationship(
        "Workspace",
        back_populates="owner",
    )
    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="WorkspaceMember.user_id",
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        "AuthSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    verification_tokens: Mapped[list["VerificationToken"]] = relationship(
        "VerificationToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r}>"
