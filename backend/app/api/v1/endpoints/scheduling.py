"""Scheduling endpoints: queue a post for later, and manage the queue.

These live under the same ``/api/v1/posts/{workspace_id}`` tree as the
publishing routes because a schedule *is* a post's "when" — but they are kept in
their own module so the calendar's rules (time zones, the due window, cancel and
reschedule) stay together.

Two rules mirror the publishing endpoints:

* **The workspace always comes from the URL.** :func:`require_permission`
  resolves it through ``TenantMixin.scoped`` and answers ``404`` for a workspace
  the caller is not a member of; a ``post_id`` in the body is a *reference* that
  the service re-resolves inside that workspace.
* **Scheduling dispatches nothing.** ``POST`` records the entry and its pending
  destinations and returns ``201``. The post is published only when the worker's
  due-sweep reaches it, at which point it goes through the identical Phase 7
  job path.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.logging import get_logger
from app.core.permissions import SCHEDULE_CANCEL, SCHEDULE_CREATE, SCHEDULE_READ
from app.db.enums import ScheduleStatus
from app.db.session import get_db
from app.schemas import schedule as schemas
from app.services import media as media_service
from app.services import publishing as publishing_service
from app.services import scheduling as scheduling_service

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map a scheduling/publishing failure onto the documented status code."""
    if isinstance(
        exc,
        (
            scheduling_service.ScheduleNotFoundError,
            publishing_service.PostNotFoundError,
            media_service.MediaNotFoundError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(
        exc,
        (
            scheduling_service.ScheduleConflictError,
            publishing_service.MediaNotReadyError,
            publishing_service.NoPublishableTargetError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(
        exc,
        (
            scheduling_service.InvalidScheduleTimeError,
            scheduling_service.InvalidTimezoneError,
            publishing_service.NoTargetsSelectedError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, (scheduling_service.SchedulingError, publishing_service.PublishingError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The scheduling operation could not be completed.",
    )


def _selections(payload: schemas.ScheduleCreateRequest) -> list[publishing_service.TargetSelection]:
    return [
        publishing_service.TargetSelection(
            social_account_id=target.social_account_id,
            caption=target.caption,
            title=target.title,
        )
        for target in payload.targets
    ]


# ---------------------------------------------------------------------------
# Queue (calendar)
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/schedule",
    response_model=list[schemas.SchedulePublic],
    summary="List the workspace's scheduled posts",
)
def list_schedule(
    window: Literal["upcoming", "past", "all"] = Query(
        default="upcoming",
        description="upcoming = still to fire; past = fired, cancelled or missed.",
    ),
    schedule_status: ScheduleStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(require_permission(SCHEDULE_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.SchedulePublic]:
    """The calendar, soonest-first for the queue and newest-first for history."""
    rows = scheduling_service.list_schedules(
        db,
        context.workspace.id,
        window=window,
        status=schedule_status,
        limit=limit,
        offset=offset,
    )
    return [schemas.SchedulePublic.from_schedule(row) for row in rows]


# ---------------------------------------------------------------------------
# Queue a post
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/schedule",
    response_model=schemas.SchedulePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a composed post for a future time",
)
def create_schedule(
    payload: schemas.ScheduleCreateRequest,
    context: WorkspaceContext = Depends(require_permission(SCHEDULE_CREATE)),
    db: Session = Depends(get_db),
) -> schemas.SchedulePublic:
    """Records the entry and its destinations; dispatches nothing yet.

    ``404`` if the post is not in this workspace, ``409`` if it is already live
    or already queued, ``422`` for a past time or an unknown zone.
    """
    try:
        schedule = scheduling_service.schedule_post(
            db,
            workspace_id=context.workspace.id,
            post_id=payload.post_id,
            scheduled_for=payload.scheduled_for,
            timezone=payload.timezone,
            selections=_selections(payload),
        )
    except (
        scheduling_service.SchedulingError,
        publishing_service.PublishingError,
        media_service.MediaError,
    ) as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.SchedulePublic.from_schedule(schedule)


@router.patch(
    "/{workspace_id}/schedule/{post_id}",
    response_model=schemas.SchedulePublic,
    summary="Move a queued post to a different time",
)
def update_schedule(
    post_id: uuid.UUID,
    payload: schemas.ScheduleUpdateRequest,
    context: WorkspaceContext = Depends(require_permission(SCHEDULE_CREATE)),
    db: Session = Depends(get_db),
) -> schemas.SchedulePublic:
    """Only an entry that has not fired yet can be moved."""
    try:
        schedule = scheduling_service.reschedule(
            db,
            workspace_id=context.workspace.id,
            post_id=post_id,
            scheduled_for=payload.scheduled_for,
            timezone=payload.timezone,
        )
    except (
        scheduling_service.SchedulingError,
        publishing_service.PublishingError,
    ) as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.SchedulePublic.from_schedule(schedule)


@router.delete(
    "/{workspace_id}/schedule/{post_id}",
    response_model=schemas.SchedulePublic,
    summary="Cancel a queued post before it fires",
)
def cancel_schedule(
    post_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(SCHEDULE_CANCEL)),
    db: Session = Depends(get_db),
) -> schemas.SchedulePublic:
    """Cancels the entry and its pending destinations, so nothing fires."""
    try:
        schedule = scheduling_service.cancel(
            db,
            workspace_id=context.workspace.id,
            post_id=post_id,
            user_id=context.member.user_id,
        )
    except (
        scheduling_service.SchedulingError,
        publishing_service.PublishingError,
    ) as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.SchedulePublic.from_schedule(schedule)


__all__ = ["router"]
