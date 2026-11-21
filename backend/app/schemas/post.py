"""Request/response schemas for composing and publishing posts.

A post is *content* (one video, a base caption); the per-platform rendering and
its execution state live on its :class:`~app.models.post.PostTarget` and the
:class:`~app.models.publishing.PublishingJob` behind it. These schemas expose
that whole tree so the composer can show, per destination, both the text that
will be published and the progress of the job carrying it.

Platform limits are *not* duplicated here: the publishers own them, and
:class:`PublisherCapabilityPublic` reports them to the client so the composer
renders the right guidance without a second source of truth.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.enums import JobStatus, LogLevel, PostStatus, PostTargetStatus, SocialPlatform
from app.models.post import Post, PostTarget
from app.models.publishing import PublishingJob, PublishingLog
from app.services.publishers import PublisherCapability

#: Generous sanity bound on user text. The real, per-platform ceiling is
#: enforced by each publisher's ``validate_caption``.
_MAX_TEXT_LENGTH = 10_000


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
class PublisherCapabilityPublic(BaseModel):
    """One platform's documented limits, for the composer to render."""

    platform: SocialPlatform
    display_name: str
    max_size_bytes: int
    max_duration_seconds: int | None = None
    min_width: int
    min_height: int
    max_caption_length: int
    requires_title: bool
    supports_title: bool
    allowed_mime_types: list[str]
    notes: str = ""

    @classmethod
    def from_capability(cls, capability: PublisherCapability) -> "PublisherCapabilityPublic":
        return cls(
            platform=capability.platform,
            display_name=capability.display_name,
            max_size_bytes=capability.max_size_bytes,
            max_duration_seconds=capability.max_duration_seconds,
            min_width=capability.min_width,
            min_height=capability.min_height,
            max_caption_length=capability.max_caption_length,
            requires_title=capability.requires_title,
            supports_title=capability.supports_title,
            allowed_mime_types=list(capability.allowed_mime_types),
            notes=capability.notes,
        )


# ---------------------------------------------------------------------------
# Job + log
# ---------------------------------------------------------------------------
class PublishingLogPublic(BaseModel):
    """One append-only log line from a publishing job."""

    id: uuid.UUID
    level: LogLevel
    message: str
    context: dict | None = None
    response_payload: dict | None = None
    created_at: datetime

    @classmethod
    def from_log(cls, log: PublishingLog) -> "PublishingLogPublic":
        return cls(
            id=log.id,
            level=log.level,
            message=log.message,
            context=log.context,
            response_payload=log.response_payload,
            created_at=log.created_at,
        )


class PublishingJobPublic(BaseModel):
    """Execution state of one (target, attempt-sequence) publishing job."""

    id: uuid.UUID
    status: JobStatus
    attempt_count: int
    max_attempts: int
    celery_task_id: str | None = None
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: PublishingJob) -> "PublishingJobPublic":
        return cls(
            id=job.id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            celery_task_id=job.celery_task_id,
            scheduled_for=job.scheduled_for,
            started_at=job.started_at,
            finished_at=job.finished_at,
            next_retry_at=job.next_retry_at,
            error_class=job.error_class,
            error_message=job.error_message,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class PublishingJobDetail(PublishingJobPublic):
    """A job plus its full log trail, for job monitoring."""

    workspace_id: uuid.UUID
    post_target_id: uuid.UUID
    platform: SocialPlatform
    logs: list[PublishingLogPublic] = Field(default_factory=list)

    @classmethod
    def from_job_with_logs(cls, job: PublishingJob) -> "PublishingJobDetail":
        target = job.post_target
        logs = sorted(job.logs, key=lambda entry: entry.created_at)
        return cls(
            **PublishingJobPublic.from_job(job).model_dump(),
            workspace_id=job.workspace_id,
            post_target_id=job.post_target_id,
            platform=target.platform,
            logs=[PublishingLogPublic.from_log(entry) for entry in logs],
        )


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
class PostTargetPublic(BaseModel):
    """One destination: the text for it, its outcome, and its job."""

    id: uuid.UUID
    post_id: uuid.UUID
    social_account_id: uuid.UUID
    platform: SocialPlatform
    status: PostTargetStatus
    caption: str | None = None
    title: str | None = None
    remote_post_id: str | None = None
    remote_url: str | None = None
    published_at: datetime | None = None
    error_message: str | None = None
    job: PublishingJobPublic | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_target(cls, target: PostTarget) -> "PostTargetPublic":
        # One target has exactly one job (see publishing.publish_everywhere); if
        # history ever accumulates more, the newest is the one that matters.
        job = target.jobs[-1] if target.jobs else None
        return cls(
            id=target.id,
            post_id=target.post_id,
            social_account_id=target.social_account_id,
            platform=target.platform,
            status=target.status,
            caption=target.caption,
            title=target.title,
            remote_post_id=target.remote_post_id,
            remote_url=target.remote_url,
            published_at=target.published_at,
            error_message=target.error_message,
            job=PublishingJobPublic.from_job(job) if job is not None else None,
            created_at=target.created_at,
            updated_at=target.updated_at,
        )


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------
class PostPublic(BaseModel):
    """A composed post and every destination it targets."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    media_file_id: uuid.UUID
    created_by_id: uuid.UUID | None = None
    title: str | None = None
    base_caption: str | None = None
    status: PostStatus
    published_at: datetime | None = None
    targets: list[PostTargetPublic] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_post(cls, post: Post) -> "PostPublic":
        return cls(
            id=post.id,
            workspace_id=post.workspace_id,
            media_file_id=post.media_file_id,
            created_by_id=post.created_by_id,
            title=post.title,
            base_caption=post.base_caption,
            status=post.status,
            published_at=post.published_at,
            targets=[PostTargetPublic.from_target(target) for target in post.targets],
            created_at=post.created_at,
            updated_at=post.updated_at,
        )


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class TargetSelectionRequest(BaseModel):
    """One destination ticked in the composer."""

    social_account_id: uuid.UUID
    #: Per-platform caption, overriding the post's base caption when set.
    caption: str | None = Field(default=None, max_length=_MAX_TEXT_LENGTH)
    #: Per-platform title (YouTube), falling back to the post's title.
    title: str | None = Field(default=None, max_length=200)


class PostCreateRequest(BaseModel):
    """Compose a draft: one uploaded video plus the user's base caption."""

    media_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)
    base_caption: str | None = Field(default=None, max_length=_MAX_TEXT_LENGTH)


class PublishEverywhereRequest(BaseModel):
    """The destinations to publish this post to, all at once."""

    targets: list[TargetSelectionRequest] = Field(min_length=1)


__all__ = [
    "PostCreateRequest",
    "PostPublic",
    "PostTargetPublic",
    "PublishEverywhereRequest",
    "PublisherCapabilityPublic",
    "PublishingJobDetail",
    "PublishingJobPublic",
    "PublishingLogPublic",
    "TargetSelectionRequest",
]
