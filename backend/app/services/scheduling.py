"""Content scheduling — queue a composed post for a future, chosen time.

A :class:`~app.models.scheduling.ScheduledPost` is the *when*; the post itself is
the *what*. Exactly one schedule row exists per post (a unique constraint in the
Phase 2 schema), so the calendar can be listed independently while publishing
history stays untouched.

The design has four moving parts:

1. **Queue it.** :func:`schedule_post` validates the destinations through
   :func:`app.services.publishing.record_targets` — the same validation a manual
   publish does — and stores them as *pending* targets with no job created.
2. **Never lose it.** The schedule row is the durable queue: it lives in
   PostgreSQL, so it survives an API or worker restart.
3. **Fire it once.** :func:`sweep_due` claims each due row atomically
   (``SELECT … FOR UPDATE SKIP LOCKED``) and only then calls
   :func:`app.services.publishing.queue_targets`, which builds the very same
   per-target jobs, idempotency keys, retries and dead-letter handling as a
   manual "Publish Everywhere". A re-run, a second worker, or a re-delivered
   sweep can therefore not publish the same post twice.
4. **Time zones are explicit.** ``scheduled_for`` is always persisted in UTC,
   and the caller's IANA zone is stored alongside it so the UI can show the
   local time the user actually picked — never a guess.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.enums import JobStatus, PostStatus, PostTargetStatus, ScheduleStatus
from app.models.post import Post
from app.models.scheduling import ScheduledPost
from app.services import publishing as publishing_service
from app.services.publishing import TargetSelection

logger = get_logger(__name__)

#: Statuses from which a post cannot be scheduled — it is already live.
_UNSCHEDULABLE_POST_STATUSES = frozenset(
    {
        PostStatus.PUBLISHING,
        PostStatus.PUBLISHED,
        PostStatus.PARTIALLY_PUBLISHED,
        PostStatus.CANCELLED,
    }
)

#: A schedule in one of these states is still holding its slot: the post has not
#: been dispatched and the user may still reschedule or cancel it.
_EDITABLE_SCHEDULE_STATUSES = frozenset({ScheduleStatus.PENDING, ScheduleStatus.QUEUED})

#: Job states that no longer need to be cancelled when a schedule is dropped.
_TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.DEAD_LETTER}
)

#: The ``window`` values :func:`list_schedules` accepts.
SCHEDULE_WINDOWS = ("upcoming", "past", "all")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class SchedulingError(Exception):
    """Base class for scheduling-domain failures."""


class ScheduleNotFoundError(SchedulingError):
    """No such schedule in this workspace."""


class ScheduleConflictError(SchedulingError):
    """The requested change is not allowed in the schedule's current state."""


class InvalidScheduleTimeError(SchedulingError):
    """The chosen instant is not usable (in the past)."""


class InvalidTimezoneError(SchedulingError):
    """The supplied zone is not a recognised IANA time zone."""


# ---------------------------------------------------------------------------
# Time-zone handling
# ---------------------------------------------------------------------------
def normalize_timezone(name: str | None) -> str:
    """Return a stored-safe IANA zone name, or raise.

    Validating here — not in the browser — is what lets the API promise that a
    stored zone can always be resolved back to a real offset.
    """
    candidate = (name or settings.scheduling_default_timezone).strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(
            f"'{name}' is not a recognised IANA time zone (for example 'Europe/London')."
        ) from exc
    return candidate


def to_utc_instant(scheduled_for: datetime, timezone_name: str) -> datetime:
    """Turn a caller's chosen moment into the UTC instant that gets stored.

    A naive datetime is interpreted in ``timezone_name`` (that is the zone the
    user picked); an aware one is converted. Either way the value written to the
    database is unambiguous UTC.
    """
    zone = ZoneInfo(timezone_name)
    moment = scheduled_for if scheduled_for.tzinfo is not None else scheduled_for.replace(
        tzinfo=zone
    )
    return moment.astimezone(dt_timezone.utc)


def local_time(schedule: ScheduledPost) -> datetime:
    """The stored UTC instant rendered in the schedule's own zone.

    Falls back to UTC for a row whose stored zone is no longer resolvable (a
    tzdata rollback), so reading the calendar can never 500.
    """
    instant = _ensure_aware(schedule.scheduled_for)
    try:
        zone = ZoneInfo(schedule.timezone)
    except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - defensive
        zone = dt_timezone.utc
    return instant.astimezone(zone)


def _ensure_aware(moment: datetime) -> datetime:
    """A ``DateTime(timezone=True)`` value is aware, but a raw fixture may not be."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def get_schedule(db: Session, workspace_id: uuid.UUID, post_id: uuid.UUID) -> ScheduledPost:
    """The schedule for one post in this workspace, or raise.

    Resolved through ``TenantMixin.scoped``, so another tenant's post id answers
    "not found" rather than confirming that a schedule exists elsewhere.
    """
    schedule = db.scalar(
        ScheduledPost.scoped(workspace_id)
        .options(selectinload(ScheduledPost.post))
        .where(ScheduledPost.post_id == post_id)
    )
    if schedule is None:
        raise ScheduleNotFoundError("That post is not scheduled in this workspace.")
    return schedule


def list_schedules(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    window: str = "upcoming",
    status: ScheduleStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ScheduledPost]:
    """The workspace's calendar entries.

    ``window`` is ``upcoming`` (still to fire), ``past`` (fired, cancelled or
    missed) or ``all``. An explicit ``status`` filter overrides the window. The
    more recent entries come first for ``past``, the soonest first otherwise.
    """
    stmt = ScheduledPost.scoped(workspace_id).options(selectinload(ScheduledPost.post))
    if status is not None:
        stmt = stmt.where(ScheduledPost.status == status)
    elif window == "upcoming":
        stmt = stmt.where(
            ScheduledPost.status.in_((ScheduleStatus.PENDING, ScheduleStatus.QUEUED))
        )
    elif window == "past":
        stmt = stmt.where(
            ScheduledPost.status.in_(
                (ScheduleStatus.DISPATCHED, ScheduleStatus.CANCELLED, ScheduleStatus.MISSED)
            )
        )
    order = (
        ScheduledPost.scheduled_for.desc()
        if window == "past"
        else ScheduledPost.scheduled_for.asc()
    )
    stmt = stmt.order_by(order).limit(limit).offset(offset)
    return list(db.scalars(stmt))


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def schedule_post(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post_id: uuid.UUID,
    scheduled_for: datetime,
    timezone: str | None = None,
    selections: list[TargetSelection],
    storage=None,
) -> ScheduledPost:
    """Queue a composed post for a future time.

    The destinations are validated and recorded as pending targets immediately
    (using the publishing layer), but nothing is dispatched — the job is only
    created when the schedule comes due. The post's status becomes ``scheduled``.

    Raises:
        publishing_service.PostNotFoundError: no such post in this workspace.
        publishing_service.MediaNotReadyError: the video is still processing.
        publishing_service.NoTargetsSelectedError: no destination was selected.
        publishing_service.NoPublishableTargetError: every destination was rejected.
        ScheduleConflictError: the post is already live, or already queued.
        InvalidScheduleTimeError: the chosen instant is in the past.
        InvalidTimezoneError: the zone is not a valid IANA name.
    """
    zone = normalize_timezone(timezone)
    instant = to_utc_instant(scheduled_for, zone)
    now = utcnow()
    if instant < now:
        raise InvalidScheduleTimeError("Choose a time in the future.")

    post = publishing_service.get_post(db, workspace_id, post_id)
    if post.status in _UNSCHEDULABLE_POST_STATUSES:
        raise ScheduleConflictError(
            f"This post is '{post.status.value}' and can no longer be scheduled."
        )

    existing = db.scalar(
        ScheduledPost.scoped(workspace_id).where(ScheduledPost.post_id == post_id)
    )
    if existing is not None and existing.status not in (
        ScheduleStatus.CANCELLED,
        ScheduleStatus.MISSED,
    ):
        raise ScheduleConflictError(
            "This post is already scheduled. Edit or cancel the existing entry instead."
        )

    # Validate and record the destinations before touching the schedule row, so a
    # request that cannot be fulfilled leaves both the post and the calendar as
    # they were.
    publishing_service.record_targets(
        db,
        workspace_id=workspace_id,
        post_id=post_id,
        selections=selections,
        storage=storage,
    )

    if existing is None:
        schedule = ScheduledPost(
            workspace_id=workspace_id,
            post_id=post_id,
            scheduled_for=instant,
            timezone=zone,
            status=ScheduleStatus.PENDING,
        )
        db.add(schedule)
    else:
        # Re-activate an entry that was cancelled or missed.
        schedule = existing
        schedule.scheduled_for = instant
        schedule.timezone = zone
        schedule.status = ScheduleStatus.PENDING
        schedule.dispatched_at = None
        schedule.cancelled_at = None
        schedule.cancelled_by_id = None
        schedule.celery_task_id = None

    post.status = PostStatus.SCHEDULED
    db.flush()
    logger.info(
        "post scheduled",
        extra={
            "workspace_id": str(workspace_id),
            "post_id": str(post_id),
            "scheduled_for": instant.isoformat(),
            "timezone": zone,
        },
    )
    return schedule


def reschedule(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post_id: uuid.UUID,
    scheduled_for: datetime,
    timezone: str | None = None,
) -> ScheduledPost:
    """Move an already-queued post to a different time.

    Only an entry that has not fired yet can be moved; once it has been
    dispatched the publish is in the hands of the reliability layer and editing
    the time would be meaningless.

    Raises:
        ScheduleNotFoundError: the post is not scheduled in this workspace.
        ScheduleConflictError: the entry has already fired, or been cancelled.
        InvalidScheduleTimeError / InvalidTimezoneError: as :func:`schedule_post`.
    """
    schedule = get_schedule(db, workspace_id, post_id)
    if schedule.status not in _EDITABLE_SCHEDULE_STATUSES:
        raise ScheduleConflictError(
            f"This schedule is '{schedule.status.value}' and can no longer be rescheduled."
        )

    zone = normalize_timezone(timezone or schedule.timezone)
    instant = to_utc_instant(scheduled_for, zone)
    if instant < utcnow():
        raise InvalidScheduleTimeError("Choose a time in the future.")

    schedule.scheduled_for = instant
    schedule.timezone = zone
    db.flush()
    logger.info(
        "post rescheduled",
        extra={
            "workspace_id": str(workspace_id),
            "post_id": str(post_id),
            "scheduled_for": instant.isoformat(),
        },
    )
    return schedule


def cancel(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    post_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> ScheduledPost:
    """Drop a queued post before it fires.

    The pending destinations and any not-yet-terminal job are cancelled with it,
    so nothing is left behind for the sweep to pick up — the guarantee that a
    cancelled post never publishes.

    Raises:
        ScheduleNotFoundError: the post is not scheduled in this workspace.
        ScheduleConflictError: the entry has already fired or been cancelled.
    """
    schedule = get_schedule(db, workspace_id, post_id)
    if schedule.status is ScheduleStatus.CANCELLED:
        raise ScheduleConflictError("This schedule is already cancelled.")
    if schedule.status is ScheduleStatus.DISPATCHED:
        raise ScheduleConflictError(
            "This post has already been dispatched and can no longer be cancelled."
        )

    schedule.status = ScheduleStatus.CANCELLED
    schedule.cancelled_at = utcnow()
    schedule.cancelled_by_id = user_id

    post = schedule.post
    for target in post.targets:
        if target.status in (
            PostTargetStatus.PENDING,
            PostTargetStatus.QUEUED,
            PostTargetStatus.PUBLISHING,
        ):
            target.status = PostTargetStatus.CANCELLED
        for job in target.jobs:
            if job.status not in _TERMINAL_JOB_STATUSES:
                job.status = JobStatus.CANCELLED
                job.finished_at = utcnow()
    post.status = PostStatus.CANCELLED

    db.flush()
    logger.info(
        "schedule cancelled",
        extra={"workspace_id": str(workspace_id), "post_id": str(post_id)},
    )
    return schedule


# ---------------------------------------------------------------------------
# Dispatch sweep (runs in the Celery worker)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SweepResult:
    """What one due-sweep did, for the worker's task result and the logs."""

    claimed: int = 0
    dispatched: int = 0
    missed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "dispatched": self.dispatched,
            "missed": self.missed,
            "failed": self.failed,
        }


def sweep_due(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> SweepResult:
    """Claim the entries that have come due and dispatch them.

    The claim is a single ``SELECT … FOR UPDATE SKIP LOCKED``: two workers (or
    two ticks that overlap) each take disjoint rows, and a row already locked by
    another transaction is skipped rather than waited on. That, plus the fact
    that an entry is only reached in ``pending``, is what makes a scheduled post
    fire exactly once.

    An entry that is overdue by more than ``scheduling_missed_grace_seconds``
    (the worker was down, the machine slept) is marked ``missed`` and its
    destinations cancelled instead of publishing hours-old content: a post the
    user is told to re-queue is better than one that appears at the wrong time.

    The caller owns the transaction (the worker's ``session_scope`` commits).
    """
    now = _ensure_aware(now or utcnow())
    limit = limit or settings.scheduling_batch_size

    due = list(
        db.scalars(
            select(ScheduledPost)
            .where(
                ScheduledPost.status == ScheduleStatus.PENDING,
                ScheduledPost.scheduled_for <= now,
            )
            .order_by(ScheduledPost.scheduled_for)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    result = SweepResult(claimed=len(due))
    if not due:
        return result

    grace = timedelta(seconds=settings.scheduling_missed_grace_seconds)
    for schedule in due:
        if now - _ensure_aware(schedule.scheduled_for) > grace:
            _mark_missed(db, schedule)
            result.missed += 1
            continue

        try:
            jobs = publishing_service.queue_targets(
                db,
                workspace_id=schedule.workspace_id,
                post_id=schedule.post_id,
            )
        except publishing_service.PublishingError as exc:
            # The post or its video became unusable between scheduling and now.
            # Leave the entry un-dispatched for the operator rather than
            # publishing something broken.
            logger.warning(
                "scheduled post could not be dispatched",
                extra={"schedule_id": str(schedule.id), "error": str(exc)},
            )
            schedule.status = ScheduleStatus.MISSED
            result.failed += 1
            continue

        schedule.status = ScheduleStatus.DISPATCHED
        schedule.dispatched_at = now
        result.dispatched += 1
        logger.info(
            "scheduled post dispatched",
            extra={
                "schedule_id": str(schedule.id),
                "post_id": str(schedule.post_id),
                "jobs": len(jobs),
            },
        )

    db.flush()
    return result


def _mark_missed(db: Session, schedule: ScheduledPost) -> None:
    """Abandon an entry that came due too long ago to publish now."""
    schedule.status = ScheduleStatus.MISSED
    schedule.dispatched_at = None
    post = schedule.post
    for target in post.targets:
        if target.status in (PostTargetStatus.PENDING, PostTargetStatus.QUEUED):
            target.status = PostTargetStatus.CANCELLED
    if post.status is PostStatus.SCHEDULED:
        # Back to a draft so the user can simply re-queue it.
        post.status = PostStatus.DRAFT
    logger.warning(
        "scheduled post missed its window",
        extra={"schedule_id": str(schedule.id), "post_id": str(schedule.post_id)},
    )


__all__ = [
    "SCHEDULE_WINDOWS",
    "InvalidScheduleTimeError",
    "InvalidTimezoneError",
    "ScheduleConflictError",
    "ScheduleNotFoundError",
    "SchedulingError",
    "SweepResult",
    "cancel",
    "get_schedule",
    "list_schedules",
    "local_time",
    "normalize_timezone",
    "reschedule",
    "schedule_post",
    "sweep_due",
    "to_utc_instant",
]
