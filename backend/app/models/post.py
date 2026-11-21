"""Posts and their per-platform targets."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import PostStatus, PostTargetStatus, SocialPlatform
from app.db.mixins import SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.analytics import PostMetric
    from app.models.media import MediaFile
    from app.models.publishing import PublishingJob
    from app.models.social_account import SocialAccount
    from app.models.user import User
    from app.models.workspace import Workspace


class Post(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One uploaded video plus the user's base caption.

    A post holds *content only*. Timing lives in
    :class:`~app.models.scheduling.ScheduledPost` and execution lives in
    :class:`~app.models.publishing.PublishingJob`.
    """

    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_workspace_created", "workspace_id", "created_at"),
        Index("ix_posts_workspace_status", "workspace_id", "status"),
    )

    media_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("media_files.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    title: Mapped[str | None] = mapped_column(String(200))
    base_caption: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PostStatus] = mapped_column(
        enum_column(PostStatus, "post_status"),
        nullable=False,
        server_default=PostStatus.DRAFT.value,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped["Workspace"] = relationship("Workspace")
    media_file: Mapped["MediaFile"] = relationship("MediaFile", back_populates="posts")
    created_by: Mapped["User | None"] = relationship("User")
    targets: Mapped[list["PostTarget"]] = relationship(
        "PostTarget",
        back_populates="post",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Post id={self.id} status={self.status}>"


class PostTarget(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One post rendered for one connected account (one publishing destination).

    ``workspace_id`` duplicates the value on :class:`Post` on purpose: it lets
    the publishing engine list and filter targets without joining, and it keeps
    tenant isolation enforceable on this table directly.
    """

    __tablename__ = "post_targets"
    __table_args__ = (
        UniqueConstraint("post_id", "social_account_id"),
        Index("ix_post_targets_workspace_status", "workspace_id", "status"),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("social_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        enum_column(SocialPlatform, "social_platform"),
        nullable=False,
    )
    caption: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[PostTargetStatus] = mapped_column(
        enum_column(PostTargetStatus, "post_target_status"),
        nullable=False,
        server_default=PostTargetStatus.PENDING.value,
    )
    remote_post_id: Mapped[str | None] = mapped_column(String(190))
    remote_url: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    post: Mapped["Post"] = relationship("Post", back_populates="targets")
    social_account: Mapped["SocialAccount"] = relationship(
        "SocialAccount",
        back_populates="post_targets",
    )
    jobs: Mapped[list["PublishingJob"]] = relationship(
        "PublishingJob",
        back_populates="post_target",
        cascade="all, delete-orphan",
    )
    metrics: Mapped[list["PostMetric"]] = relationship(
        "PostMetric",
        back_populates="post_target",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PostTarget post_id={self.post_id} platform={self.platform}>"
