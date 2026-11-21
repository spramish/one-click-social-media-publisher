"""Publishing jobs and their execution logs."""

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
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import JobStatus, LogLevel
from app.db.mixins import TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.post import PostTarget


class PublishingJob(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One Celery task that publishes one :class:`PostTarget` to one platform.

    Jobs are the unit of retry, idempotency and monitoring. ``idempotency_key``
    is globally unique so a double-click on "Publish Everywhere" can never
    enqueue the same platform upload twice.
    """

    __tablename__ = "publishing_jobs"
    __table_args__ = (
        Index("ix_publishing_jobs_status_scheduled", "status", "scheduled_for"),
        Index("ix_publishing_jobs_status_retry", "status", "next_retry_at"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
    )

    post_target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "job_status"),
        nullable=False,
        server_default=JobStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(155))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_class: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)

    post_target: Mapped["PostTarget"] = relationship("PostTarget", back_populates="jobs")
    logs: Mapped[list["PublishingLog"]] = relationship(
        "PublishingLog",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PublishingJob id={self.id} status={self.status} attempts={self.attempt_count}>"


class PublishingLog(UUIDPrimaryKeyMixin, Base):
    """Append-only log line for a publishing job.

    Reached only through its job, so it carries no ``workspace_id``.
    ``response_payload`` keeps the raw platform response for support and audit
    purposes; secrets must be redacted before writing.
    """

    __tablename__ = "publishing_logs"
    __table_args__ = (
        Index("ix_publishing_logs_job_created", "publishing_job_id", "created_at"),
    )

    publishing_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("publishing_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[LogLevel] = mapped_column(
        enum_column(LogLevel, "log_level"),
        nullable=False,
        server_default=LogLevel.INFO.value,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)
    response_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    job: Mapped["PublishingJob"] = relationship("PublishingJob", back_populates="logs")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PublishingLog job_id={self.publishing_job_id} level={self.level}>"
