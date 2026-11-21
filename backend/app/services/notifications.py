"""In-app notifications and their background delivery.

Notifications are addressed to a **user**, not a workspace: the bell and the
inbox are per-account, and a notice may outlive the workspace it referred to.
Two rules keep that honest:

* **The in-app row is the source of truth.** It is always written, whatever the
  user's email preferences say. Those preferences only decide whether a *copy*
  is also mailed, so switching one off can never hide something from the bell.
* **Delivery is idempotent.** Each notice carries ``emailed_at``, each schedule
  ``reminder_sent_at`` and each connected account ``token_warning_sent_at``, so
  a sweep that runs twice (or two workers that overlap) still notifies once.

The sweep is split into three sub-sweeps — pending emails, upcoming-publish
reminders and access-expiry warnings — that :func:`sweep` runs together. Every
one is driven by a plain query over persisted state, so a worker restart simply
catches up on its next tick.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.enums import NotificationType, ScheduleStatus, SocialAccountStatus
from app.models.notification import Notification, NotificationPreference
from app.models.scheduling import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.email import EmailMessage, send_email
from app.services.scheduling import local_time

logger = get_logger(__name__)


class NotificationError(Exception):
    """Base class for notification-domain failures."""


class NotificationNotFoundError(NotificationError):
    """No such notification for this user."""


#: Which preference flag gates the email copy of a notification type. A type
#: absent from this map is never emailed (``subscription`` and ``system`` are
#: in-app only) — the row is still recorded and still marked as considered.
_EMAIL_PREFERENCE_FLAG: dict[NotificationType, str] = {
    NotificationType.PUBLISH_SUCCESS: "email_publish_success",
    NotificationType.PUBLISH_FAILURE: "email_publish_failure",
    NotificationType.SCHEDULE_REMINDER: "email_schedule_reminder",
    NotificationType.TOKEN_EXPIRING: "email_token_expiring",
}

#: The list endpoint's per-request cap. Bounds the payload an inbox page returns.
_MAX_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def record(
    db: Session,
    *,
    user_id: uuid.UUID,
    type: NotificationType,
    title: str,
    body: str | None = None,
    workspace_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> Notification:
    """Add one in-app notification to the session (the caller commits).

    Email delivery is *not* attempted here: the row is picked up by the next
    :func:`dispatch_emails` tick, which keeps a slow or failing mail server off
    the request/worker path that produced the notice.
    """
    notification = Notification(
        user_id=user_id,
        workspace_id=workspace_id,
        type=type,
        title=title,
        body=body,
        payload=payload,
    )
    db.add(notification)
    return notification


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_notifications(
    db: Session,
    user_id: uuid.UUID,
    *,
    unread_only: bool = False,
    type: NotificationType | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    """A page of the user's notifications, newest first.

    Returns ``(items, total, unread_count)`` where ``total`` counts the rows
    matching the filters and ``unread_count`` is the user's overall unread
    figure — the number the bell badge shows, independent of any filter.
    """
    limit = max(1, min(limit, _MAX_PAGE_SIZE))
    offset = max(0, offset)

    filters = [Notification.user_id == user_id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    if type is not None:
        filters.append(Notification.type == type)

    items = list(
        db.scalars(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    unread_count = count_unread(db, user_id)
    return items, total, unread_count


def count_unread(db: Session, user_id: uuid.UUID) -> int:
    """The user's unread total, for the badge."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        or 0
    )


def _get_owned(
    db: Session, user_id: uuid.UUID, notification_id: uuid.UUID
) -> Notification:
    """Fetch a notification only if it belongs to this user.

    Scoping by ``user_id`` — not just the id — is what stops one account from
    reading or dismissing another's notice, and makes a foreign id answer "not
    found" rather than confirming it exists.
    """
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    if notification is None:
        raise NotificationNotFoundError("That notification does not exist.")
    return notification


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def mark_read(
    db: Session, user_id: uuid.UUID, notification_id: uuid.UUID
) -> Notification:
    """Mark one notification read. Idempotent: an already-read row is returned."""
    notification = _get_owned(db, user_id, notification_id)
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.flush()
    return notification


def mark_all_read(db: Session, user_id: uuid.UUID) -> int:
    """Mark every unread notification read; returns how many changed."""
    unread = list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
    )
    now = utcnow()
    for notification in unread:
        notification.read_at = now
    if unread:
        db.flush()
    return len(unread)


def delete(db: Session, user_id: uuid.UUID, notification_id: uuid.UUID) -> None:
    """Dismiss one notification."""
    notification = _get_owned(db, user_id, notification_id)
    db.delete(notification)
    db.flush()


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def get_preferences(db: Session, user_id: uuid.UUID) -> NotificationPreference:
    """The user's preferences, created with defaults on first access."""
    preference = db.scalar(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    )
    if preference is None:
        preference = NotificationPreference(user_id=user_id)
        db.add(preference)
        db.flush()
    return preference


def update_preferences(
    db: Session, user_id: uuid.UUID, **flags: bool | None
) -> NotificationPreference:
    """Apply the provided flags to the user's preferences."""
    preference = get_preferences(db, user_id)
    for name, value in flags.items():
        if value is not None:
            setattr(preference, name, value)
    db.flush()
    return preference


# ---------------------------------------------------------------------------
# Background delivery (runs in the Celery worker)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SweepResult:
    """What one notification sweep did, for the worker's task result and logs."""

    emails_sent: int = 0
    reminders_sent: int = 0
    token_warnings_sent: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "emails_sent": self.emails_sent,
            "reminders_sent": self.reminders_sent,
            "token_warnings_sent": self.token_warnings_sent,
        }


def dispatch_emails(db: Session, *, limit: int | None = None) -> int:
    """Email the notifications that are still awaiting a delivery decision.

    A row is chosen once — ``emailed_at IS NULL`` — and marked considered
    afterwards whether or not a message went out (opted out, or a type that has
    no email form). A transport failure leaves the marker unset so the next tick
    retries it, without holding up the rest of the batch.
    """
    now = utcnow()
    batch = limit or settings.notifications_email_batch_size
    pending = list(
        db.scalars(
            select(Notification)
            .where(
                Notification.emailed_at.is_(None),
                Notification.created_at <= now,
            )
            .order_by(Notification.created_at)
            .limit(batch)
        )
    )

    sent = 0
    for notification in pending:
        flag = _EMAIL_PREFERENCE_FLAG.get(notification.type)
        user = db.get(User, notification.user_id)
        if (
            flag is not None
            and user is not None
            and user.deleted_at is None
            and user.email
            and getattr(get_preferences(db, notification.user_id), flag)
        ):
            try:
                send_email(
                    EmailMessage(
                        to=user.email,
                        subject=notification.title,
                        text_body=notification.body or notification.title,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a transport failure must not drop the notice
                logger.warning(
                    "notification email could not be sent",
                    extra={"notification_id": str(notification.id), "error": str(exc)},
                )
                continue
            sent += 1
            logger.info(
                "notification emailed",
                extra={
                    "notification_id": str(notification.id),
                    "to": user.email,
                    "type": notification.type.value,
                },
            )
        notification.emailed_at = now

    db.flush()
    return sent


def send_schedule_reminders(
    db: Session, *, now: datetime | None = None, limit: int | None = None
) -> int:
    """Notify the owner of each post that publishes within the lead window.

    Only an entry that is still ``pending`` and has not been reminded about yet
    qualifies; ``reminder_sent_at`` is stamped so a later tick cannot repeat it.
    A post the user has already cancelled or that has fired is never considered,
    so a reminder always describes something that is genuinely still to come.
    """
    now = now or utcnow()
    lead = timedelta(seconds=settings.notifications_reminder_lead_seconds)
    batch = limit or settings.notifications_email_batch_size

    due = list(
        db.scalars(
            select(ScheduledPost)
            .options(selectinload(ScheduledPost.post))
            .where(
                ScheduledPost.status == ScheduleStatus.PENDING,
                ScheduledPost.reminder_sent_at.is_(None),
                ScheduledPost.scheduled_for > now,
                ScheduledPost.scheduled_for <= now + lead,
            )
            .order_by(ScheduledPost.scheduled_for)
            .limit(batch)
        )
    )

    sent = 0
    for schedule in due:
        post = schedule.post
        recipient = post.created_by_id if post is not None else None
        schedule.reminder_sent_at = now
        if recipient is None:
            continue
        when = local_time(schedule).strftime("%Y-%m-%d %H:%M %Z")
        title = "Your post publishes soon"
        body = f"“{post.title or 'Your video'}” is scheduled to publish at {when}."
        record(
            db,
            user_id=recipient,
            type=NotificationType.SCHEDULE_REMINDER,
            title=title,
            body=body,
            workspace_id=schedule.workspace_id,
            payload={
                "post_id": str(schedule.post_id),
                "schedule_id": str(schedule.id),
                "scheduled_for": schedule.scheduled_for.isoformat(),
            },
        )
        sent += 1

    db.flush()
    return sent


def warn_expiring_tokens(
    db: Session, *, now: datetime | None = None, limit: int | None = None
) -> int:
    """Warn about connected accounts whose access token expires within the window.

    The recipient is whoever connected the account, falling back to the
    workspace owner when the original connector's account is gone. An account is
    warned once per expiry window via ``token_warning_sent_at``.
    """
    now = now or utcnow()
    horizon = now + timedelta(days=settings.notifications_token_warning_days)
    batch = limit or settings.notifications_email_batch_size

    expiring = list(
        db.scalars(
            select(SocialAccount)
            .options(selectinload(SocialAccount.workspace))
            .where(
                SocialAccount.status == SocialAccountStatus.CONNECTED,
                SocialAccount.deleted_at.is_(None),
                SocialAccount.token_warning_sent_at.is_(None),
                SocialAccount.token_expires_at.is_not(None),
                SocialAccount.token_expires_at > now,
                SocialAccount.token_expires_at <= horizon,
            )
            .order_by(SocialAccount.token_expires_at)
            .limit(batch)
        )
    )

    sent = 0
    for account in expiring:
        account.token_warning_sent_at = now
        recipient = account.connected_by_id
        if recipient is None and account.workspace is not None:
            recipient = account.workspace.owner_id
        if recipient is None:
            continue
        expires_on = account.token_expires_at.strftime("%Y-%m-%d %H:%M %Z")  # type: ignore[union-attr]
        record(
            db,
            user_id=recipient,
            type=NotificationType.TOKEN_EXPIRING,
            title=f"Reconnect {account.display_name}",
            body=(
                f"The {account.platform.value} connection for “{account.display_name}” "
                f"expires at {expires_on}. Reconnect it to keep publishing."
            ),
            workspace_id=account.workspace_id,
            payload={
                "social_account_id": str(account.id),
                "platform": account.platform.value,
                "token_expires_at": account.token_expires_at.isoformat(),  # type: ignore[union-attr]
            },
        )
        sent += 1

    db.flush()
    return sent


def sweep(
    db: Session, *, now: datetime | None = None
) -> SweepResult:
    """Run every notification sub-sweep once.

    The caller owns the transaction (the worker's ``session_scope`` commits), so
    a failure anywhere leaves nothing half-delivered.
    """
    now = now or utcnow()
    result = SweepResult()
    result.emails_sent = dispatch_emails(db)
    result.reminders_sent = send_schedule_reminders(db, now=now)
    result.token_warnings_sent = warn_expiring_tokens(db, now=now)
    return result


__all__ = [
    "NotificationError",
    "NotificationNotFoundError",
    "SweepResult",
    "count_unread",
    "delete",
    "dispatch_emails",
    "get_preferences",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "record",
    "send_schedule_reminders",
    "sweep",
    "update_preferences",
    "warn_expiring_tokens",
]
