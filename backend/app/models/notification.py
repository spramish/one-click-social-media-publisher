"""In-app notifications and per-user notification preferences."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import NotificationType
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.user import User


class Notification(UUIDPrimaryKeyMixin, Base):
    """A notification addressed to one user, optionally scoped to a workspace.

    ``workspace_id`` is nullable (and ``ON DELETE SET NULL``) so account-level
    notices such as a password change survive the deletion of a workspace.
    This table therefore does not use ``TenantMixin``: it is addressed to the
    *user*, and membership is re-checked by the service layer.

    ``emailed_at`` is set by the background email sweep once the row has been
    considered for delivery (whether or not an email was actually sent, and
    whether or not the user opted in). It is the durable marker that stops the
    sweep from re-sending the same notice on every tick.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read_at"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        #: The email sweep's work queue: rows still awaiting a delivery decision.
        Index(
            "ix_notifications_pending_email",
            "created_at",
            postgresql_where=text("emailed_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
    )
    type: Mapped[NotificationType] = mapped_column(
        enum_column(NotificationType, "notification_type"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Notification id={self.id} type={self.type} user={self.user_id}>"


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's opt-out choice for which notices are also sent by email.

    One row per user (a unique constraint on ``user_id``), created lazily the
    first time preferences are read or changed. Like :class:`Notification` it is
    addressed to the *user* rather than to a workspace, so it does not use
    ``TenantMixin``.

    Each flag governs **email delivery only**. The in-app notification is always
    recorded, so turning a flag off can never hide something from the bell — it
    only stops the extra copy from being mailed.
    """

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    email_publish_success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    email_publish_failure: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    email_schedule_reminder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    email_token_expiring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<NotificationPreference user={self.user_id}>"


__all__ = ["Notification", "NotificationPreference"]
