"""Multi-platform publishing engine — the "Publish Everywhere" orchestration.

This is the module the rest of the application talks to. The platform
implementations live in :mod:`app.services.publishers`; nothing here names a
concrete platform, so adding a fifth destination is a publisher class plus a
registry line.

The flow the master spec calls "Publish Everywhere" is implemented as two
steps, so a draft can be composed, previewed, and published independently (and
so Phase 8's scheduler can reuse the first step):

1. :func:`create_post` — records the video plus the user's base caption.
2. :func:`publish_everywhere` — validates the video, every selected account and
   every selected platform, then creates one :class:`~app.models.post.PostTarget`
   and one independent :class:`~app.models.publishing.PublishingJob` per
   destination and dispatches each job to Celery.
3. :func:`run_job` — executed by the worker for one job: refreshes the
   credential, uploads, drives the platform to a confirmed final status, stores
   the returned identifiers, and notifies the user.

Reliability rules, all encoded here rather than left to callers:

* **Nothing is reported as published until the platform confirms it.** The
  publishers poll the platform's own processing status; a job only reaches
  ``succeeded`` when that status is terminal-success, and a post only reaches
  ``published`` when *every* target has a confirmed remote id.
* **One platform's failure never cancels another's.** Every target has its own
  row, its own job, and its own attempt counter. A failure writes to that
  target alone and the post becomes ``partially_published``.
* **Retries are bounded and backed off.** A classified *retryable* failure
  (rate limit, 5xx, timeout) schedules the next attempt at
  ``base * 2 ** (attempt - 1)`` seconds, capped, honouring the platform's own
  ``Retry-After`` when it sent one; once ``max_attempts`` is reached the job is
  dead-lettered rather than retried forever.
* **Duplicate publishes are refused.** The idempotency key is derived from the
  workspace, the video's content, the destination account, and the exact text,
  so pressing "Publish Everywhere" twice cannot post the same video twice.
* **Every attempt is logged.** :class:`~app.models.publishing.PublishingLog`
  keeps the classified error and the platform's raw response (redacted) for
  support and audit.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.enums import (
    JobStatus,
    LogLevel,
    MediaStatus,
    NotificationType,
    PostStatus,
    PostTargetStatus,
    SocialAccountStatus,
    SocialPlatform,
)
from app.models.notification import Notification
from app.models.post import Post, PostTarget
from app.models.publishing import PublishingJob, PublishingLog
from app.models.social_account import SocialAccount
from app.services import accounts as accounts_service
from app.services import media as media_service
from app.services import storage as storage_service
from app.services.publishers import (
    MediaAsset,
    MediaValidationError,
    PublishError,
    PublishRateLimitError,
    PublishRequest,
    PublishTransientError,
    PublisherCapability,
    get_publisher,
    supported_platforms,
)
from app.services.storage import LocalStorage, StorageError, get_storage

logger = get_logger(__name__)

#: Celery task that executes one job. Dispatched by name so this module never
#: imports the worker (which imports back into this module).
TASK_NAME = "publishing.publish_target"

#: A job in one of these states is finished and must never be executed again.
_TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.DEAD_LETTER,
    }
)

#: A duplicate-publishing guard only counts a job that is live *or* succeeded —
#: a dead-lettered attempt never published anything, so it must not block a
#: legitimate retry by the user.
_BLOCKING_JOB_STATUSES = (
    JobStatus.PENDING,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.RETRYING,
    JobStatus.SUCCEEDED,
)

#: Keys scrubbed from anything written to a job log.
_SECRET_HINTS = ("token", "secret", "authorization", "password", "signature")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class PublishingError(Exception):
    """Base class for publishing-domain failures."""


class PostNotFoundError(PublishingError):
    """No such post in this workspace."""


class MediaNotReadyError(PublishingError):
    """The video has not finished processing and cannot be published yet."""


class NoTargetsSelectedError(PublishingError):
    """A publish request carried no destinations."""


class NoPublishableTargetError(PublishingError):
    """Every selected destination was rejected before anything was dispatched.

    Carries the per-target reasons so the composer can explain exactly which
    platform refused and why, instead of failing with a bare "cannot publish".
    """

    def __init__(self, reasons: Sequence[tuple[SocialPlatform | None, str]]) -> None:
        self.reasons = list(reasons)
        summary = "; ".join(
            f"{platform.value if platform else 'account'}: {reason}"
            for platform, reason in self.reasons
        )
        super().__init__(f"None of the selected destinations could accept this video ({summary}).")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TargetSelection:
    """One destination the user ticked in the composer."""

    social_account_id: uuid.UUID
    #: Per-platform caption, overriding the post's base caption when set.
    caption: str | None = None
    #: Per-platform title (YouTube); falls back to the post's title.
    title: str | None = None


@dataclass(slots=True)
class _PlannedTarget:
    """One selection after validation, ready to become a target row."""

    selection: TargetSelection
    account: SocialAccount | None = None
    platform: SocialPlatform | None = None
    caption: str | None = None
    title: str | None = None
    idempotency_key: str | None = None
    #: When set, the target is recorded as ``skipped`` and never dispatched.
    error: str | None = None

    @property
    def publishable(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
def capabilities() -> list[PublisherCapability]:
    """Every platform's documented limits, for the composer to render."""
    return [get_publisher(platform).capability() for platform in supported_platforms()]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _post_query(workspace_id: uuid.UUID):
    return (
        Post.scoped(workspace_id)
        .options(selectinload(Post.targets).selectinload(PostTarget.jobs))
    )


def get_post(db: Session, workspace_id: uuid.UUID, post_id: uuid.UUID) -> Post:
    """One post with its targets and their jobs, or raise."""
    post = db.scalar(_post_query(workspace_id).where(Post.id == post_id))
    if post is None:
        raise PostNotFoundError("That post does not exist in this workspace.")
    return post


def list_posts(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    status: PostStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Post]:
    """The workspace's posts, newest first."""
    stmt = _post_query(workspace_id)
    if status is not None:
        stmt = stmt.where(Post.status == status)
    stmt = stmt.order_by(Post.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_job(db: Session, workspace_id: uuid.UUID, job_id: uuid.UUID) -> PublishingJob:
    """One job with its target and every log line it produced, or raise."""
    job = db.scalar(
        PublishingJob.scoped(workspace_id)
        .options(
            selectinload(PublishingJob.logs),
            selectinload(PublishingJob.post_target),
        )
        .where(PublishingJob.id == job_id)
    )
    if job is None:
        raise PostNotFoundError("That publishing job does not exist in this workspace.")
    return job


# ---------------------------------------------------------------------------
# Step 1 — compose
# ---------------------------------------------------------------------------
def create_post(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    media_id: uuid.UUID,
    title: str | None = None,
    base_caption: str | None = None,
) -> Post:
    """Record a draft post: one video plus the user's base caption.

    Raises:
        media_service.MediaNotFoundError: the video is not in this workspace.
        MediaNotReadyError: the video is still processing (or failed), so it
            cannot be published yet.
    """
    media = media_service.get_media(db, workspace_id, media_id)
    if media.status is not MediaStatus.READY:
        raise MediaNotReadyError(
            f"This video is '{media.status.value}' and cannot be published yet. "
            f"Wait for it to finish processing."
        )

    post = Post(
        workspace_id=workspace_id,
        media_file_id=media.id,
        created_by_id=user_id,
        title=title,
        base_caption=base_caption,
        status=PostStatus.DRAFT,
    )
    db.add(post)
    db.flush()
    logger.info(
        "post created",
        extra={"workspace_id": str(workspace_id), "post_id": str(post.id)},
    )
    return post


# ---------------------------------------------------------------------------
# Step 2 — publish everywhere
# ---------------------------------------------------------------------------
def publish_everywhere(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post_id: uuid.UUID,
    selections: Sequence[TargetSelection],
    storage: LocalStorage | None = None,
) -> Post:
    """Dispatch one independent job per selected destination.

    Validation happens *before* anything is dispatched: the video must be
    ready, every account must exist in this workspace and still be connected,
    and every platform must accept the video and its caption. A destination
    that fails validation is recorded as ``skipped`` with the reason — it never
    affects the destinations that succeeded.

    Raises:
        PostNotFoundError: no such post in this workspace.
        NoTargetsSelectedError: no destinations were selected.
        MediaNotReadyError: the post's video is not ready.
        NoPublishableTargetError: every destination was rejected, so nothing
            was dispatched. ``reasons`` lists them.
    """
    if not selections:
        raise NoTargetsSelectedError("Select at least one account to publish to.")

    storage = storage or get_storage()
    post = get_post(db, workspace_id, post_id)
    media = media_service.get_media(db, workspace_id, post.media_file_id)
    if media.status is not MediaStatus.READY:
        raise MediaNotReadyError(
            f"This video is '{media.status.value}' and cannot be published yet."
        )

    asset = _media_asset(media, storage)
    plan = [
        _plan_target(db, post=post, asset=asset, selection=selection)
        for selection in selections
    ]

    publishable = [planned for planned in plan if planned.publishable]
    if not publishable:
        raise NoPublishableTargetError(
            [(planned.platform, planned.error or "unknown reason") for planned in plan]
        )

    queued = _create_targets(db, workspace_id=workspace_id, post=post, plan=plan)
    _refresh_post_status(db, post)
    db.flush()
    logger.info(
        "publish everywhere dispatched",
        extra={
            "workspace_id": str(workspace_id),
            "post_id": str(post.id),
            "queued": len(queued),
            "skipped": len(plan) - len(queued),
        },
    )
    return post


# ---------------------------------------------------------------------------
# Step 2 (deferred) — record destinations now, queue them later
# ---------------------------------------------------------------------------
def record_targets(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post_id: uuid.UUID,
    selections: Sequence[TargetSelection],
    storage: LocalStorage | None = None,
) -> Post:
    """Validate destinations and persist them as *pending* targets.

    This is the first half of :func:`publish_everywhere`, split out for the
    scheduler: the destinations are chosen and validated now, but no job is
    created and nothing is dispatched. When the schedule comes due,
    :func:`queue_targets` runs the second half, so a scheduled publish walks
    through exactly the same target rows, idempotency keys, retries and
    dead-letter handling as a manual one.

    Targets recorded by a previous, never-dispatched schedule are replaced, so
    the one-target-per-account constraint holds and stale rows never linger.

    Raises the same errors as :func:`publish_everywhere`.
    """
    if not selections:
        raise NoTargetsSelectedError("Select at least one account to publish to.")

    storage = storage or get_storage()
    post = get_post(db, workspace_id, post_id)
    media = media_service.get_media(db, workspace_id, post.media_file_id)
    if media.status is not MediaStatus.READY:
        raise MediaNotReadyError(
            f"This video is '{media.status.value}' and cannot be published yet."
        )

    asset = _media_asset(media, storage)
    plan = [
        _plan_target(db, post=post, asset=asset, selection=selection)
        for selection in selections
    ]
    if not any(planned.publishable for planned in plan):
        raise NoPublishableTargetError(
            [(planned.platform, planned.error or "unknown reason") for planned in plan]
        )

    replaceable = {
        PostTargetStatus.PENDING,
        PostTargetStatus.SKIPPED,
        PostTargetStatus.CANCELLED,
    }
    for existing in list(post.targets):
        if existing.status in replaceable:
            post.targets.remove(existing)
    db.flush()

    for planned in plan:
        if planned.account is None:
            continue
        post.targets.append(
            PostTarget(
                workspace_id=workspace_id,
                social_account_id=planned.account.id,
                platform=planned.platform or planned.account.platform,
                caption=planned.caption,
                title=planned.title,
                status=(
                    PostTargetStatus.PENDING
                    if planned.publishable
                    else PostTargetStatus.SKIPPED
                ),
                error_message=planned.error,
            )
        )
    db.flush()
    logger.info(
        "scheduled destinations recorded",
        extra={"workspace_id": str(workspace_id), "post_id": str(post.id)},
    )
    return post


def queue_targets(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post_id: uuid.UUID,
) -> list[PublishingJob]:
    """Create and dispatch one job per still-pending target of a post.

    The second half of the deferred path (see :func:`record_targets`). It builds
    the same :class:`PublishingJob` rows, with the same idempotency key derived
    from the video, the account and the exact text, as a manual publish, so a
    scheduled post gains the whole Phase 7 reliability layer unchanged.

    Raises:
        PostNotFoundError: no such post in this workspace.
        MediaNotReadyError: the post's video is no longer publishable.
    """
    storage = get_storage()
    post = get_post(db, workspace_id, post_id)
    media = media_service.get_media(db, workspace_id, post.media_file_id)
    if media.status is not MediaStatus.READY:
        raise MediaNotReadyError(
            f"This video is '{media.status.value}' and cannot be published yet."
        )
    asset = _media_asset(media, storage)

    queued: list[PublishingJob] = []
    for target in post.targets:
        if target.status is not PostTargetStatus.PENDING:
            continue
        # A target that already has a live or succeeded job must not get a
        # second one, however the sweep is re-run.
        if any(job.status in _BLOCKING_JOB_STATUSES for job in target.jobs):
            continue
        account = accounts_service.get_account(db, workspace_id, target.social_account_id)
        key = _idempotency_key(
            workspace_id=workspace_id,
            media=asset,
            account=account,
            caption=target.caption or "",
            title=target.title,
        )
        job = PublishingJob(
            workspace_id=workspace_id,
            status=JobStatus.QUEUED,
            max_attempts=settings.publishing_max_attempts,
            idempotency_key=key,
            scheduled_for=utcnow(),
        )
        target.jobs.append(job)
        target.status = PostTargetStatus.QUEUED
        db.flush()
        _log(
            db,
            job,
            LogLevel.INFO,
            f"Queued for {target.platform.value}.",
            context={"attempt": 0, "max_attempts": job.max_attempts, "scheduled": True},
        )
        job.celery_task_id = _dispatch(db, job)
        queued.append(job)

    _refresh_post_status(db, post)
    db.flush()
    return queued


def _plan_target(
    db: Session,
    *,
    post: Post,
    asset: MediaAsset,
    selection: TargetSelection,
) -> _PlannedTarget:
    """Validate one destination, or explain why it cannot be used."""
    try:
        account = accounts_service.get_account(db, post.workspace_id, selection.social_account_id)
    except accounts_service.AccountError:
        # Also covers another tenant's id: TenantMixin.scoped answers "not
        # found" rather than confirming that the account exists elsewhere.
        return _PlannedTarget(selection, error="That connected account is no longer available.")

    if account.status is not SocialAccountStatus.CONNECTED:
        return _PlannedTarget(
            selection,
            account=account,
            platform=account.platform,
            error=f"The {account.display_name} connection is {account.status.value}. Reconnect it.",
        )

    publisher = get_publisher(account.platform)
    caption = publisher.build_caption(
        base_caption=post.base_caption,
        target_caption=selection.caption,
        title=selection.title,
    )
    title = publisher.build_title(
        title=selection.title or post.title,
        base_caption=caption,
    )

    try:
        publisher.validate_media(asset)
        publisher.validate_caption(caption=caption, title=title)
    except MediaValidationError as exc:
        return _PlannedTarget(selection, account=account, platform=account.platform, error=str(exc))
    except PublishError as exc:
        return _PlannedTarget(selection, account=account, platform=account.platform, error=str(exc))

    key = _idempotency_key(
        workspace_id=post.workspace_id,
        media=asset,
        account=account,
        caption=caption,
        title=title,
    )
    if _duplicate_exists(db, key):
        return _PlannedTarget(
            selection,
            account=account,
            platform=account.platform,
            caption=caption,
            title=title,
            error=(
                f"There is already a live or successful publish of this video to "
                f"{account.display_name}. Not publishing it twice."
            ),
        )

    return _PlannedTarget(
        selection,
        account=account,
        platform=account.platform,
        caption=caption,
        title=title,
        idempotency_key=key,
    )


def _create_targets(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post: Post,
    plan: Sequence[_PlannedTarget],
) -> list[PostTarget]:
    """Materialise the plan as target rows, jobs, and Celery dispatches."""
    created: list[tuple[PostTarget, _PlannedTarget]] = []
    for planned in plan:
        if planned.account is None:
            # The account could not be resolved (deleted, or another tenant's
            # id), so there is no valid ``social_accounts`` row for the target's
            # NOT NULL FK. Nothing to record; the reason is still reported when
            # *no* destination could be used.
            continue
        target = PostTarget(
            workspace_id=workspace_id,
            social_account_id=planned.account.id,
            platform=planned.platform or planned.account.platform,
            caption=planned.caption,
            title=planned.title,
            status=(
                PostTargetStatus.QUEUED if planned.publishable else PostTargetStatus.SKIPPED
            ),
            error_message=planned.error,
        )
        post.targets.append(target)
        created.append((target, planned))
    db.flush()  # assign target ids before the jobs reference them

    queued: list[PostTarget] = []
    for target, planned in created:
        if not planned.publishable:
            continue
        job = PublishingJob(
            workspace_id=workspace_id,
            status=JobStatus.QUEUED,
            max_attempts=settings.publishing_max_attempts,
            idempotency_key=planned.idempotency_key or "",
            scheduled_for=utcnow(),
        )
        # Append through the relationship (not just the FK) so ``target.jobs``
        # reflects the job immediately — the response DTO reads it.
        target.jobs.append(job)
        db.flush()
        _log(
            db,
            job,
            LogLevel.INFO,
            f"Queued for {planned.platform.value if planned.platform else 'platform'}.",
            context={"attempt": 0, "max_attempts": job.max_attempts},
        )
        job.celery_task_id = _dispatch(db, job)
        queued.append(target)
    return queued


# ---------------------------------------------------------------------------
# Step 3 — execute one job (runs in the Celery worker)
# ---------------------------------------------------------------------------
def run_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    storage: LocalStorage | None = None,
) -> str:
    """Execute one publishing attempt and record its outcome.

    Returns the job's resulting status name. Idempotent: a job that already
    reached a terminal status is returned untouched, so a re-delivered Celery
    message can never publish twice.
    """
    storage = storage or get_storage()
    job = db.scalar(
        select(PublishingJob).where(PublishingJob.id == job_id).with_for_update()
    )
    if job is None:  # pragma: no cover - only if the row was deleted mid-flight
        logger.warning("publishing job not found", extra={"job_id": str(job_id)})
        return "missing"
    if job.status in _TERMINAL_JOB_STATUSES:
        return job.status.value

    job.attempt_count += 1
    job.status = JobStatus.RUNNING
    if job.started_at is None:
        job.started_at = utcnow()
    job.next_retry_at = None
    _log(
        db,
        job,
        LogLevel.INFO,
        f"Attempt {job.attempt_count} of {job.max_attempts} started.",
        context={"attempt": job.attempt_count, "max_attempts": job.max_attempts},
    )
    target = job.post_target
    target.status = PostTargetStatus.PUBLISHING

    try:
        _execute(db, job, storage=storage)
    except PublishError as exc:
        _handle_failure(db, job, exc)
    except Exception as exc:  # noqa: BLE001 - anything unexpected is transient
        logger.exception(
            "publishing job raised an unexpected error",
            extra={"job_id": str(job.id), "platform": target.platform.value},
        )
        _handle_failure(db, job, PublishTransientError(f"{type(exc).__name__}: {exc}"))

    db.commit()
    return job.status.value


def _execute(db: Session, job: PublishingJob, *, storage: LocalStorage) -> None:
    """Upload once and require a platform-confirmed final status."""
    target = job.post_target
    post = target.post
    media = media_service.get_media(db, target.workspace_id, post.media_file_id)
    asset = _media_asset(media, storage)

    account = accounts_service.get_account(db, target.workspace_id, target.social_account_id)
    publisher = get_publisher(target.platform)
    tokens = accounts_service.ensure_fresh_tokens(db, account)

    result = publisher.publish(
        PublishRequest(
            asset=asset,
            caption=target.caption or "",
            title=target.title,
            access_token=tokens.access_token,
            platform_account_id=account.platform_account_id,
            privacy=settings.publishing_default_privacy,
        )
    )

    # Only reached once the platform reported a terminal, successful status.
    target.status = PostTargetStatus.PUBLISHED
    target.remote_post_id = result.remote_post_id
    target.remote_url = result.remote_url
    target.error_message = None
    target.published_at = utcnow()

    job.status = JobStatus.SUCCEEDED
    job.finished_at = utcnow()
    job.next_retry_at = None
    job.error_class = None
    job.error_message = None

    _log(
        db,
        job,
        LogLevel.INFO,
        f"{publisher.display_name} confirmed the publish (status: {result.status}).",
        context={"remote_post_id": result.remote_post_id, "remote_url": result.remote_url},
        response_payload=result.raw,
    )
    _notify(db, post, target, success=True)
    _refresh_post_status(db, post)


def _handle_failure(db: Session, job: PublishingJob, exc: PublishError) -> None:
    """Classify a failure into a retry, a dead letter, or a permanent failure."""
    target = job.post_target
    post = target.post
    job.error_class = type(exc).__name__
    job.error_message = str(exc)

    retry_after = exc.retry_after if isinstance(exc, PublishRateLimitError) else None
    if exc.retryable and job.attempt_count < job.max_attempts:
        delay = _backoff_seconds(job.attempt_count, retry_after=retry_after)
        job.status = JobStatus.RETRYING
        job.next_retry_at = utcnow() + timedelta(seconds=delay)
        target.status = PostTargetStatus.QUEUED
        _log(
            db,
            job,
            LogLevel.WARNING,
            f"Attempt {job.attempt_count} failed and will be retried in {delay}s: {exc}",
            context={"retry_in_seconds": delay, "error_class": job.error_class},
        )
        try:
            _dispatch(db, job, countdown=delay)
        except Exception:  # noqa: BLE001 - a dead broker must not lose the job
            logger.exception(
                "could not re-dispatch a retrying publishing job",
                extra={"job_id": str(job.id), "platform": target.platform.value},
            )
    else:
        # Retries exhausted (dead letter) or the platform will never accept it.
        job.status = JobStatus.DEAD_LETTER if exc.retryable else JobStatus.FAILED
        job.finished_at = utcnow()
        job.next_retry_at = None
        target.status = PostTargetStatus.FAILED
        target.error_message = str(exc)
        _log(
            db,
            job,
            LogLevel.ERROR,
            f"Publishing failed permanently after {job.attempt_count} attempt(s): {exc}",
            context={"error_class": job.error_class, "dead_letter": exc.retryable},
        )
        _notify(db, post, target, success=False)

    _refresh_post_status(db, post)
    db.flush()


# ---------------------------------------------------------------------------
# Post status aggregation
# ---------------------------------------------------------------------------
def _refresh_post_status(db: Session, post: Post) -> None:
    """Derive the post's status from its targets.

    A post is ``published`` only when *every* target holds a
    platform-confirmed publish; anything less is ``partially_published`` — which
    is the visible guarantee that one platform's failure did not cancel the
    others.
    """
    statuses = [target.status for target in post.targets]
    if not statuses:
        post.status = PostStatus.DRAFT
        return

    if any(
        status in (PostTargetStatus.PENDING, PostTargetStatus.QUEUED, PostTargetStatus.PUBLISHING)
        for status in statuses
    ):
        post.status = PostStatus.PUBLISHING
        return

    published = sum(1 for status in statuses if status is PostTargetStatus.PUBLISHED)
    if published and published == len(statuses):
        post.status = PostStatus.PUBLISHED
        if post.published_at is None:
            post.published_at = utcnow()
    elif published:
        post.status = PostStatus.PARTIALLY_PUBLISHED
        if post.published_at is None:
            post.published_at = utcnow()
    elif all(status is PostTargetStatus.CANCELLED for status in statuses):
        post.status = PostStatus.CANCELLED
    else:
        post.status = PostStatus.FAILED


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def _dispatch(db: Session, job: PublishingJob, *, countdown: int | None = None) -> str | None:
    """Hand one job to Celery and record the task id on the row."""
    job.celery_task_id = enqueue_job(job.id, countdown=countdown)
    return job.celery_task_id


def enqueue_job(job_id: uuid.UUID, *, countdown: int | None = None) -> str | None:
    """Dispatch one publishing job to the worker by name.

    The worker is imported lazily so this module (and its tests) never needs a
    running broker. Tests replace this function with a recorder.
    """
    from app.worker import celery_app  # noqa: PLC0415 - breaks an import cycle

    result = celery_app.send_task(TASK_NAME, args=[str(job_id)], countdown=countdown)
    return result.id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _media_asset(media, storage: LocalStorage) -> MediaAsset:
    """Everything a publisher needs about one stored video."""
    try:
        path = storage.path_for(media.storage_key)
    except StorageError as exc:  # pragma: no cover - a corrupt key is a bug
        raise MediaValidationError("The stored video key is not usable.") from exc
    if not path.is_file():
        # Permanent: retrying cannot conjure the file back; the user must
        # re-upload the video.
        raise MediaValidationError(
            "The stored video file is no longer available on the server. Re-upload it."
        )

    return MediaAsset(
        media_id=media.id,
        path=path,
        size_bytes=media.size_bytes,
        mime_type=media.mime_type,
        duration_seconds=media.duration_seconds,
        width=media.width,
        height=media.height,
        public_url=storage_service.public_media_url(
            media.id,
            media.workspace_id,
            "original",
            ttl_seconds=settings.publishing_media_url_ttl_seconds,
        ),
        checksum_sha256=media.checksum_sha256,
    )


def _idempotency_key(
    *,
    workspace_id: uuid.UUID,
    media: MediaAsset,
    account: SocialAccount,
    caption: str,
    title: str | None,
) -> str:
    """A stable fingerprint of "this video, to this account, with this text".

    Prefers the video's *content* checksum so re-uploading the same file is
    still recognised as a duplicate; falls back to the media row id for any
    file whose checksum was never recorded. 64 hex characters, inside the
    column's 80.
    """
    fingerprint = "\x1f".join(
        (
            str(workspace_id),
            media.checksum_sha256 or media.media_id.hex,
            str(account.id),
            (caption or "").strip(),
            (title or "").strip(),
        )
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _duplicate_exists(db: Session, key: str) -> bool:
    return (
        db.scalar(
            select(PublishingJob.id)
            .where(
                PublishingJob.idempotency_key == key,
                PublishingJob.status.in_(_BLOCKING_JOB_STATUSES),
            )
            .limit(1)
        )
        is not None
    )


def _backoff_seconds(attempt: int, *, retry_after: float | None = None) -> int:
    """Exponential backoff, capped, never shorter than the platform's request."""
    delay = settings.publishing_retry_base_seconds * (2 ** max(0, attempt - 1))
    delay = min(delay, settings.publishing_retry_max_seconds)
    if retry_after is not None:
        delay = max(delay, int(retry_after))
    return int(delay)


def _log(
    db: Session,
    job: PublishingJob,
    level: LogLevel,
    message: str,
    *,
    context: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        PublishingLog(
            publishing_job_id=job.id,
            level=level,
            message=message,
            context=_redact(context),
            response_payload=_redact(response_payload),
        )
    )


def _redact(payload: Any) -> Any:
    """Strip anything credential-shaped before it is written to a log row."""
    if isinstance(payload, dict):
        return {
            key: ("[redacted]" if any(hint in key.lower() for hint in _SECRET_HINTS) else _redact(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact(item) for item in payload]
    return payload


def _notify(db: Session, post: Post, target: PostTarget, *, success: bool) -> None:
    """Record the in-app notification for one target's confirmed outcome."""
    if post.created_by_id is None:
        return
    publisher = get_publisher(target.platform)
    if success:
        notification_type = NotificationType.PUBLISH_SUCCESS
        title = f"Published to {publisher.display_name}"
        body = f"Your video is live on {publisher.display_name}."
    else:
        notification_type = NotificationType.PUBLISH_FAILURE
        title = f"Could not publish to {publisher.display_name}"
        body = target.error_message

    db.add(
        Notification(
            user_id=post.created_by_id,
            workspace_id=post.workspace_id,
            type=notification_type,
            title=title,
            body=body,
            payload={
                "post_id": str(post.id),
                "post_target_id": str(target.id),
                "platform": target.platform.value,
                "remote_url": target.remote_url,
            },
        )
    )


__all__ = [
    "MediaNotReadyError",
    "NoPublishableTargetError",
    "NoTargetsSelectedError",
    "PostNotFoundError",
    "PublishingError",
    "TargetSelection",
    "capabilities",
    "create_post",
    "enqueue_job",
    "get_job",
    "get_post",
    "list_posts",
    "publish_everywhere",
    "queue_targets",
    "record_targets",
    "run_job",
]
