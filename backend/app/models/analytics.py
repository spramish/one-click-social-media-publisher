"""Performance metrics retrieved from the social platforms.

Two append-only tables hold *measurements*, never estimates:

* :class:`PostMetric` — one snapshot of a single published target's video
  metrics (views, likes, comments, shares, reach, impressions).
* :class:`AccountMetric` — one snapshot of a connected account's audience
  numbers (followers, follows, posts).

Three rules shape the schema:

* **Every column is nullable, and a ``NULL`` means "the platform did not
  provide this metric".** A genuine zero is stored as ``0``. The phase brief
  requires the two cases to stay distinguishable, and the only place that can
  be decided is here — a platform that does not expose shares must not be
  reported as a video with zero shares.
* **A snapshot records when it was taken** (``retrieved_at``). Metrics are
  point-in-time values on the platform, so a row without a retrieval time
  could not be charted or trusted.
* **Snapshots accumulate.** A refresh inserts new rows rather than overwriting
  the previous ones, so historical performance can be reconstructed instead of
  only showing the newest number.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import SocialPlatform
from app.db.mixins import TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.post import PostTarget
    from app.models.social_account import SocialAccount

#: Every non-negative count column, used to generate the CHECK constraints so a
#: negative value can never be written (the platforms do not report negatives).
_COUNT_COLUMNS = ("views", "likes", "comments", "shares", "reach", "impressions")


class PostMetric(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One retrieval of a published target's video metrics.

    ``workspace_id`` duplicates the value on the owning :class:`PostTarget` and
    ``platform`` duplicates its destination platform: both let the analytics
    service aggregate without joining, and keep tenant isolation enforceable on
    this table directly.
    """

    __tablename__ = "post_metrics"
    __table_args__ = (
        Index("ix_post_metrics_target_retrieved", "post_target_id", "retrieved_at"),
        Index("ix_post_metrics_workspace_retrieved", "workspace_id", "retrieved_at"),
        *(
            CheckConstraint(f"{column} >= 0", name=f"{column}_non_negative")
            for column in _COUNT_COLUMNS
        ),
    )

    post_target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        enum_column(SocialPlatform, "social_platform"),
        nullable=False,
    )
    #: When the platform answered. Distinct from ``created_at`` (when the row was
    #: written) so a delayed worker cannot misdate a measurement.
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    impressions: Mapped[int | None] = mapped_column(Integer)
    #: The platform's own response, kept for support and audit. Never a secret.
    raw: Mapped[dict | None] = mapped_column(JSONB)

    post_target: Mapped["PostTarget"] = relationship("PostTarget", back_populates="metrics")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PostMetric target={self.post_target_id} views={self.views}>"


class AccountMetric(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One retrieval of a connected account's audience metrics.

    Audience numbers are account-level rather than per-video, so they live in
    their own table and back the "audience" and "account-level performance"
    views of the dashboard.
    """

    __tablename__ = "account_metrics"
    __table_args__ = (
        Index("ix_account_metrics_account_retrieved", "social_account_id", "retrieved_at"),
        Index("ix_account_metrics_workspace_retrieved", "workspace_id", "retrieved_at"),
        CheckConstraint("followers >= 0", name="followers_non_negative"),
        CheckConstraint("follows >= 0", name="follows_non_negative"),
        CheckConstraint("posts_count >= 0", name="posts_count_non_negative"),
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
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    followers: Mapped[int | None] = mapped_column(Integer)
    follows: Mapped[int | None] = mapped_column(Integer)
    posts_count: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    social_account: Mapped["SocialAccount"] = relationship(
        "SocialAccount", back_populates="metrics"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AccountMetric account={self.social_account_id} followers={self.followers}>"


__all__ = ["AccountMetric", "PostMetric"]
