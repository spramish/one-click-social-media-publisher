"""Notification endpoints: the bell, the inbox and the email preferences.

A notification is addressed to the signed-in **user**, not a workspace, so these
routes are authenticated but not workspace-scoped:

* every query is filtered by the caller's own ``user_id``, so one account can
  never read or dismiss another's notice — a foreign id answers ``404`` rather
  than confirming the row exists;
* the in-app record is always written; the preferences here govern the *email
  copy* only, so turning one off cannot hide anything from the bell.

Reads never contact a platform or a mail server. Email is delivered by the
background sweep (``notifications.sweep``), which keeps SMTP latency and failure
off the request path.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.enums import NotificationType
from app.db.session import get_db
from app.models.user import User
from app.schemas import notification as schemas
from app.services import notifications as notifications_service
from app.services.notifications import NotificationNotFoundError

router = APIRouter()
logger = get_logger(__name__)


def _not_found(detail: str = "That notification does not exist.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=schemas.NotificationListPublic,
    summary="List the signed-in user's notifications, newest first",
)
def list_notifications(
    unread_only: bool = Query(default=False, description="Only unread notifications."),
    type: NotificationType | None = Query(default=None, description="Filter by category."),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.NotificationListPublic:
    """A page of notifications plus the badge count.

    ``total`` counts the rows matching the filters; ``unread_count`` is the
    user's overall unread figure, so the bell badge stays correct no matter how
    the inbox is filtered.
    """
    items, total, unread_count = notifications_service.list_notifications(
        db,
        user.id,
        unread_only=unread_only,
        type=type,
        limit=limit,
        offset=offset,
    )
    return schemas.NotificationListPublic(
        items=[schemas.NotificationPublic.from_model(item) for item in items],
        total=total,
        unread_count=unread_count,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/unread-count",
    response_model=schemas.UnreadCountPublic,
    summary="The number of unread notifications (the bell badge)",
)
def unread_count(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.UnreadCountPublic:
    return schemas.UnreadCountPublic(
        count=notifications_service.count_unread(db, user.id)
    )


@router.post(
    "/read-all",
    response_model=schemas.MarkAllReadResult,
    summary="Mark every unread notification read",
)
def mark_all_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.MarkAllReadResult:
    updated = notifications_service.mark_all_read(db, user.id)
    db.commit()
    return schemas.MarkAllReadResult(updated=updated)


@router.post(
    "/{notification_id}/read",
    response_model=schemas.NotificationPublic,
    summary="Mark one notification read",
)
def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.NotificationPublic:
    try:
        notification = notifications_service.mark_read(db, user.id, notification_id)
    except NotificationNotFoundError as exc:
        raise _not_found() from exc
    db.commit()
    return schemas.NotificationPublic.from_model(notification)


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Dismiss one notification",
)
def delete_notification(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        notifications_service.delete(db, user.id, notification_id)
    except NotificationNotFoundError as exc:
        raise _not_found() from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
@router.get(
    "/preferences",
    response_model=schemas.NotificationPreferencePublic,
    summary="The user's email-delivery preferences",
)
def get_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.NotificationPreferencePublic:
    preference = notifications_service.get_preferences(db, user.id)
    db.commit()
    return schemas.NotificationPreferencePublic.from_model(preference)


@router.put(
    "/preferences",
    response_model=schemas.NotificationPreferencePublic,
    summary="Update the user's email-delivery preferences",
)
def update_preferences(
    payload: schemas.NotificationPreferenceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.NotificationPreferencePublic:
    """Only the flags present in the body are changed.

    These govern the *email copy* only — the in-app notification is always
    recorded, so a disabled flag never hides something from the bell.
    """
    preference = notifications_service.update_preferences(
        db,
        user.id,
        email_publish_success=payload.email_publish_success,
        email_publish_failure=payload.email_publish_failure,
        email_schedule_reminder=payload.email_schedule_reminder,
        email_token_expiring=payload.email_token_expiring,
    )
    db.commit()
    return schemas.NotificationPreferencePublic.from_model(preference)
