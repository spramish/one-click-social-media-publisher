"""Phase 11 tests — notifications, delivery and preferences.

The phase brief is covered end to end:

* **In-app** — list, unread count, mark read, mark all read and dismiss, all
  scoped to the signed-in user.
* **Publishing outcomes** — a success and a failure notice are delivered by the
  email sweep (the rows themselves are written by the publishing engine).
* **Scheduled-post reminders** and **token-expiration warnings** are raised by
  the sweep, once each.
* **Email delivery** runs in the background sweep, honouring preferences.
* **Preferences** gate the email copy only — never the in-app record.

No test contacts a mail server: the transport is monkeypatched, so what is
asserted is the decision (who gets mailed, and once), not SMTP itself.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import utcnow
from app.db.enums import (
    MediaStatus,
    NotificationType,
    PostStatus,
    ScheduleStatus,
    SocialAccountStatus,
    SocialPlatform,
    StorageBackend,
    WorkspaceMemberStatus,
)
from app.models import (
    MediaFile,
    Notification,
    Post,
    ScheduledPost,
    SocialAccount,
    User,
)
from app.services import auth as auth_service
from app.services import notifications as notifications_service

REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
NOTIFICATIONS = "/api/v1/notifications"

STRONG_PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str) -> tuple[str, uuid.UUID]:
    response = client.post(
        REGISTER,
        json={"email": email, "password": STRONG_PASSWORD, "full_name": "Owner"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    workspaces = client.get(WORKSPACES, headers=_bearer(token)).json()
    return token, uuid.UUID(workspaces[0]["id"])


def _media(db, workspace_id: uuid.UUID) -> MediaFile:
    media = MediaFile(
        workspace_id=workspace_id,
        storage_backend=StorageBackend.LOCAL,
        storage_key=f"{workspace_id}/{uuid.uuid4()}.mp4",
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=2048,
        checksum_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        duration_seconds=10.0,
        width=1080,
        height=1920,
        status=MediaStatus.READY,
    )
    db.add(media)
    db.flush()
    return media


def _post(db, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Post:
    post = Post(
        workspace_id=workspace_id,
        media_file_id=_media(db, workspace_id).id,
        created_by_id=user_id,
        title="Launch clip",
        status=PostStatus.SCHEDULED,
    )
    db.add(post)
    db.flush()
    return post


def _schedule(
    db, workspace_id: uuid.UUID, post: Post, *, scheduled_for, reminder_sent_at=None
) -> ScheduledPost:
    schedule = ScheduledPost(
        workspace_id=workspace_id,
        post_id=post.id,
        scheduled_for=scheduled_for,
        timezone="UTC",
        status=ScheduleStatus.PENDING,
        reminder_sent_at=reminder_sent_at,
    )
    db.add(schedule)
    db.flush()
    return schedule


def _account(
    db,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    expires_at,
    warning_sent_at=None,
    platform: SocialPlatform = SocialPlatform.FACEBOOK,
) -> SocialAccount:
    account = SocialAccount(
        workspace_id=workspace_id,
        platform=platform,
        platform_account_id=f"acct-{uuid.uuid4().hex[:8]}",
        display_name="Studio Page",
        status=SocialAccountStatus.CONNECTED,
        token_expires_at=expires_at,
        token_warning_sent_at=warning_sent_at,
        connected_by_id=user_id,
    )
    db.add(account)
    db.flush()
    return account


def _capture_emails(monkeypatch) -> list[tuple[str, str]]:
    """Replace the transport with a recorder; returns the captured (to, subject) list."""
    sent: list[tuple[str, str]] = []

    def _fake_send(message) -> None:  # noqa: ANN001 - mirrors EmailMessage
        sent.append((message.to, message.subject))

    monkeypatch.setattr(notifications_service, "send_email", _fake_send)
    return sent


# ---------------------------------------------------------------------------
# API: inbox
# ---------------------------------------------------------------------------
def test_notifications_require_authentication(api_client):
    assert api_client.get(NOTIFICATIONS).status_code == 401


def test_empty_inbox_reports_zero_unread(api_client):
    token, _ = _register(api_client, "bell@example.com")
    response = api_client.get(NOTIFICATIONS, headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["unread_count"] == 0


def test_recorded_notification_appears_and_marks_read(api_client, db):
    token, workspace_id = _register(api_client, "inbox@example.com")
    user = db.scalar(select(User).where(User.email == "inbox@example.com"))

    notifications_service.record(
        db,
        user_id=user.id,
        workspace_id=workspace_id,
        type=NotificationType.PUBLISH_SUCCESS,
        title="Published to Facebook",
        body="Your video is live.",
        payload={"post_id": str(uuid.uuid4())},
    )
    db.flush()

    listing = api_client.get(NOTIFICATIONS, headers=_bearer(token)).json()
    assert listing["total"] == 1
    assert listing["unread_count"] == 1
    item = listing["items"][0]
    assert item["type"] == "publish_success"
    assert item["is_read"] is False

    marked = api_client.post(
        f"{NOTIFICATIONS}/{item['id']}/read", headers=_bearer(token)
    )
    assert marked.status_code == 200
    assert marked.json()["is_read"] is True

    assert api_client.get(
        f"{NOTIFICATIONS}/unread-count", headers=_bearer(token)
    ).json()["count"] == 0


def test_unread_only_filter(api_client, db):
    token, workspace_id = _register(api_client, "filter@example.com")
    user = db.scalar(select(User).where(User.email == "filter@example.com"))
    for index in range(3):
        notifications_service.record(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            type=NotificationType.SYSTEM,
            title=f"Notice {index}",
        )
    db.flush()

    unread = api_client.get(
        f"{NOTIFICATIONS}?unread_only=true", headers=_bearer(token)
    ).json()
    assert unread["total"] == 3

    api_client.post(f"{NOTIFICATIONS}/read-all", headers=_bearer(token))
    assert api_client.get(
        f"{NOTIFICATIONS}?unread_only=true", headers=_bearer(token)
    ).json()["total"] == 0


def test_mark_all_read_reports_the_count(api_client, db):
    token, workspace_id = _register(api_client, "allread@example.com")
    user = db.scalar(select(User).where(User.email == "allread@example.com"))
    for index in range(2):
        notifications_service.record(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            type=NotificationType.SYSTEM,
            title=f"Notice {index}",
        )
    db.flush()

    response = api_client.post(f"{NOTIFICATIONS}/read-all", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json()["updated"] == 2


def test_dismiss_removes_notification(api_client, db):
    token, workspace_id = _register(api_client, "dismiss@example.com")
    user = db.scalar(select(User).where(User.email == "dismiss@example.com"))
    notification = notifications_service.record(
        db,
        user_id=user.id,
        workspace_id=workspace_id,
        type=NotificationType.SYSTEM,
        title="Dismiss me",
    )
    db.flush()

    assert api_client.delete(
        f"{NOTIFICATIONS}/{notification.id}", headers=_bearer(token)
    ).status_code == 204
    assert api_client.delete(
        f"{NOTIFICATIONS}/{notification.id}", headers=_bearer(token)
    ).status_code == 404


def test_one_user_cannot_see_anothers_notification(api_client, db):
    owner_token, owner_workspace = _register(api_client, "owner@example.com")
    owner = db.scalar(select(User).where(User.email == "owner@example.com"))
    other_token, _ = _register(api_client, "other@example.com")

    notification = notifications_service.record(
        db,
        user_id=owner.id,
        workspace_id=owner_workspace,
        type=NotificationType.SYSTEM,
        title="Private",
    )
    db.flush()

    # The other account's inbox is empty and the id is not addressable.
    assert api_client.get(NOTIFICATIONS, headers=_bearer(other_token)).json()["total"] == 0
    assert api_client.post(
        f"{NOTIFICATIONS}/{notification.id}/read", headers=_bearer(other_token)
    ).status_code == 404
    assert api_client.delete(
        f"{NOTIFICATIONS}/{notification.id}", headers=_bearer(other_token)
    ).status_code == 404
    # The owner still sees it.
    assert api_client.get(NOTIFICATIONS, headers=_bearer(owner_token)).json()["total"] == 1


# ---------------------------------------------------------------------------
# API: preferences
# ---------------------------------------------------------------------------
def test_preferences_default_to_opted_in_and_persist(api_client):
    token, _ = _register(api_client, "prefs@example.com")

    defaults = api_client.get(f"{NOTIFICATIONS}/preferences", headers=_bearer(token)).json()
    assert defaults["email_publish_success"] is True
    assert defaults["email_publish_failure"] is True
    assert defaults["email_schedule_reminder"] is True
    assert defaults["email_token_expiring"] is True

    updated = api_client.put(
        f"{NOTIFICATIONS}/preferences",
        headers=_bearer(token),
        json={"email_publish_success": False},
    ).json()
    assert updated["email_publish_success"] is False
    # Untouched flags keep their value.
    assert updated["email_publish_failure"] is True

    reread = api_client.get(f"{NOTIFICATIONS}/preferences", headers=_bearer(token)).json()
    assert reread["email_publish_success"] is False


# ---------------------------------------------------------------------------
# Sweep: email delivery
# ---------------------------------------------------------------------------
def test_sweep_emails_publish_outcomes_once(api_client, db, monkeypatch):
    sent = _capture_emails(monkeypatch)
    _, workspace_id = _register(api_client, "mailer@example.com")
    user = db.scalar(select(User).where(User.email == "mailer@example.com"))

    for kind in (NotificationType.PUBLISH_SUCCESS, NotificationType.PUBLISH_FAILURE):
        notifications_service.record(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            type=kind,
            title=f"{kind.value} notice",
            body="A delivery outcome",
        )
    db.flush()

    assert notifications_service.dispatch_emails(db) == 2
    assert {subject for _, subject in sent} == {
        "publish_success notice",
        "publish_failure notice",
    }

    # Idempotent: a second tick sends nothing more.
    assert notifications_service.dispatch_emails(db) == 0
    assert len(sent) == 2


def test_disabled_preference_keeps_notification_but_sends_no_email(
    api_client, db, monkeypatch
):
    sent = _capture_emails(monkeypatch)
    token, _ = _register(api_client, "optout@example.com")
    user = db.scalar(select(User).where(User.email == "optout@example.com"))

    api_client.put(
        f"{NOTIFICATIONS}/preferences",
        headers=_bearer(token),
        json={"email_publish_failure": False},
    )

    notifications_service.record(
        db,
        user_id=user.id,
        type=NotificationType.PUBLISH_FAILURE,
        title="Could not publish",
    )
    db.flush()

    assert notifications_service.dispatch_emails(db) == 0
    assert sent == []
    # The in-app record survived: opting out hides nothing from the bell.
    assert api_client.get(NOTIFICATIONS, headers=_bearer(token)).json()["total"] == 1


# ---------------------------------------------------------------------------
# Sweep: schedule reminders
# ---------------------------------------------------------------------------
def test_sweep_reminds_upcoming_schedule_once(api_client, db):
    token, workspace_id = _register(api_client, "remind@example.com")
    user = db.scalar(select(User).where(User.email == "remind@example.com"))
    post = _post(db, workspace_id, user.id)
    _schedule(db, workspace_id, post, scheduled_for=utcnow() + timedelta(minutes=30))

    assert notifications_service.send_schedule_reminders(db) == 1
    # A second tick must not duplicate it.
    assert notifications_service.send_schedule_reminders(db) == 0

    items = api_client.get(
        f"{NOTIFICATIONS}?type=schedule_reminder", headers=_bearer(token)
    ).json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["post_id"] == str(post.id)


def test_sweep_ignores_schedules_beyond_the_lead_window(api_client, db):
    _, workspace_id = _register(api_client, "faraway@example.com")
    user = db.scalar(select(User).where(User.email == "faraway@example.com"))
    post = _post(db, workspace_id, user.id)
    _schedule(db, workspace_id, post, scheduled_for=utcnow() + timedelta(days=3))

    assert notifications_service.send_schedule_reminders(db) == 0


# ---------------------------------------------------------------------------
# Sweep: token-expiration warnings
# ---------------------------------------------------------------------------
def test_sweep_warns_about_expiring_tokens_once(api_client, db):
    token, workspace_id = _register(api_client, "token@example.com")
    user = db.scalar(select(User).where(User.email == "token@example.com"))
    _account(db, workspace_id, user.id, expires_at=utcnow() + timedelta(days=2))

    assert notifications_service.warn_expiring_tokens(db) == 1
    assert notifications_service.warn_expiring_tokens(db) == 0

    items = api_client.get(
        f"{NOTIFICATIONS}?type=token_expiring", headers=_bearer(token)
    ).json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["platform"] == "facebook"


def test_sweep_ignores_far_off_token_expiry(api_client, db):
    _, workspace_id = _register(api_client, "oktoken@example.com")
    user = db.scalar(select(User).where(User.email == "oktoken@example.com"))
    _account(db, workspace_id, user.id, expires_at=utcnow() + timedelta(days=90))

    assert notifications_service.warn_expiring_tokens(db) == 0


# ---------------------------------------------------------------------------
# Sweep: the whole thing
# ---------------------------------------------------------------------------
def test_full_sweep_reports_every_sub_sweep(api_client, db, monkeypatch):
    _capture_emails(monkeypatch)
    _, workspace_id = _register(api_client, "whole@example.com")
    user = db.scalar(select(User).where(User.email == "whole@example.com"))

    notifications_service.record(
        db,
        user_id=user.id,
        type=NotificationType.PUBLISH_SUCCESS,
        title="Published",
    )
    post = _post(db, workspace_id, user.id)
    _schedule(db, workspace_id, post, scheduled_for=utcnow() + timedelta(minutes=10))
    _account(db, workspace_id, user.id, expires_at=utcnow() + timedelta(days=1))
    db.flush()

    result = notifications_service.sweep(db).as_dict()
    assert result == {"emails_sent": 1, "reminders_sent": 1, "token_warnings_sent": 1}


def test_membership_helper_still_present(db):
    """Guard against an accidental change to the role seeding the helpers rely on."""
    assert auth_service.get_role(db, "owner").id is not None
    assert WorkspaceMemberStatus.ACTIVE.value == "active"
