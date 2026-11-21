"""Scheduled (future-dated) posts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import ScheduleStatus
from app.db.mixins import TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.post import Post
    from app.models.user import User


class ScheduledPost(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """The *when* for a post, separated from the post's content.

    Exactly one schedule row exists per post (enforced by the unique constraint),
    so the calendar can be queried independently and a schedule can be edited or
    cancelled without touching published history.

    ``scheduled_for`` is always persisted in UTC; ``timezone`` stores the IANA
    zone the user picked so the UI can render the original local time.
    """

    __tablename__ = "scheduled_posts"
    __table_args__ = (
        UniqueConstraint("post_id"),
        UniqueConstraint("celery_task_id"),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    status: Mapped[ScheduleStatus] = mapped_column(
        enum_column(ScheduleStatus, "schedule_status"),
        nullable=False,
        server_default=ScheduleStatus.PENDING.value,
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(155))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    #: When the "your post publishes soon" reminder was recorded, so the sweep
    #: notifies once per entry rather than on every tick.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    post: Mapped["Post"] = relationship("Post")
    cancelled_by: Mapped["User | None"] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ScheduledPost post_id={self.post_id} at={self.scheduled_for}>"
