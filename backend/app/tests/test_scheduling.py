"""Phase 8 integration tests — content scheduling.

Like the Phase 7 suite, these drive the real FastAPI app through ``TestClient``
against the migrated ``publisher_test`` database, with each platform's HTTP layer
swapped for an ``httpx.MockTransport`` and Celery's dispatch replaced by a
recorder. What is exercised for real is the scheduling contract:

* a schedule records a post's destinations as *pending* targets and dispatch
  nothing — the job only appears when the entry comes due;
* the due-sweep claims each entry atomically and can never fire one twice;
* a scheduled publish goes through the identical publishing path, so it shares
  the same idempotency keys and reliability guarantees as a manual publish;
* an entry that is far overdue is marked ``missed`` rather than publishing stale
  content;
* time zones are stored explicitly (UTC instant + the caller's IANA zone);
* cancel and reschedule only apply while the entry is still pending;
* tenant isolation (another workspace's schedule is ``404``) and RBAC (a viewer
  may read but not queue) hold on every route.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.security import utcnow
from app.db.enums import (
    JobStatus,
    MediaStatus,
    PostStatus,
    PostTargetStatus,
    ScheduleStatus,
    SocialAccountStatus,
    SocialPlatform,
    StorageBackend,
    WorkspaceMemberStatus,
)
from app.models import (
    MediaFile,
    OAuthCredential,
    PostTarget,
    PublishingJob,
    ScheduledPost,
    SocialAccount,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services import auth as auth_service
from app.services import publishing as publishing_service
from app.services import scheduling as scheduling_service
from app.services import storage as storage_service
from app.services.publishers import get_publisher

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
POSTS = "/api/v1/posts"

STRONG_PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def dispatched(monkeypatch):
    """Replace Celery dispatch with a recorder; returns the call list."""
    calls: list[tuple[uuid.UUID, int | None]] = []

    def fake_enqueue(job_id: uuid.UUID, *, countdown: int | None = None) -> str:
        calls.append((job_id, countdown))
        return f"task-{len(calls)}"

    monkeypatch.setattr(publishing_service, "enqueue_job", fake_enqueue)
    return calls


@pytest.fixture
def publishing_storage(monkeypatch, media_storage):
    """Point the service's default storage at the test's temporary root."""
    monkeypatch.setattr(publishing_service, "get_storage", lambda: media_storage)
    return media_storage


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


def _add_membership(db, workspace_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> None:
    db.add(
        WorkspaceMember(
            workspace_id=workspace_id,
            user_id=user_id,
            role_id=auth_service.get_role(db, role_name).id,
            status=WorkspaceMemberStatus.ACTIVE,
        )
    )
    db.flush()


def _ready_media(db, storage, workspace_id: uuid.UUID) -> MediaFile:
    """A READY media row backed by a real (tiny) file in the test storage."""
    media_id = uuid.uuid4()
    key = storage_service.original_key(workspace_id, media_id, ".mp4")
    storage.write_bytes(key, b"\x00" * 64)
    media = MediaFile(
        id=media_id,
        workspace_id=workspace_id,
        storage_backend=StorageBackend.LOCAL,
        storage_key=key,
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=4096,
        checksum_sha256=hashlib.sha256(str(media_id).encode()).hexdigest(),
        duration_seconds=12.0,
        width=1080,
        height=1920,
        status=MediaStatus.READY,
    )
    db.add(media)
    db.flush()
    return media


def _connected_account(
    db,
    workspace_id: uuid.UUID,
    platform: SocialPlatform = SocialPlatform.FACEBOOK,
    *,
    account_id: str = "acct-1",
) -> SocialAccount:
    account = SocialAccount(
        workspace_id=workspace_id,
        platform=platform,
        platform_account_id=account_id,
        display_name="Account One",
        status=SocialAccountStatus.CONNECTED,
    )
    db.add(account)
    db.flush()
    db.add(
        OAuthCredential(
            social_account_id=account.id,
            access_token_encrypted=encrypt_secret("access-token"),
            expires_at=utcnow() + timedelta(hours=24),
            scopes=[],
        )
    )
    db.flush()
    db.refresh(account)
    return account


def _install_transport(monkeypatch, platform: SocialPlatform, handler) -> None:
    monkeypatch.setattr(get_publisher(platform), "transport", httpx.MockTransport(handler))


def _facebook_success(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path.endswith("/videos"):
        return httpx.Response(200, json={"id": "vid-1"})
    return httpx.Response(
        200,
        json={"status": {"video_status": "ready"}, "permalink_url": "https://fb/vid-1"},
    )


def _compose(client, token, workspace_id, media_id, *, caption: str = "hi") -> str:
    response = client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media_id), "base_caption": caption},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _schedule(
    client,
    token,
    workspace_id,
    post_id,
    account_id,
    *,
    scheduled_for: datetime | None = None,
    timezone: str | None = "UTC",
):
    payload: dict = {
        "post_id": str(post_id),
        "scheduled_for": (scheduled_for or (utcnow() + timedelta(hours=1))).isoformat(),
        "targets": [{"social_account_id": str(account_id)}],
    }
    if timezone is not None:
        payload["timezone"] = timezone
    return client.post(f"{POSTS}/{workspace_id}/schedule", json=payload, headers=_bearer(token))


def _make_due(db, *, seconds_ago: int = 5) -> None:
    """Pull a pending entry into the past so the next sweep sees it as due."""
    schedule = db.scalars(select(ScheduledPost)).one()
    schedule.scheduled_for = utcnow() - timedelta(seconds=seconds_ago)
    db.flush()


# ---------------------------------------------------------------------------
# Queueing: what a schedule does — and does not — do
# ---------------------------------------------------------------------------
def test_schedule_records_entry_and_pending_targets_without_dispatching(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "schedule@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)

    # A naive local time in Kathmandu; the server must store the UTC instant and
    # return the local rendering of it.
    response = _schedule(
        api_client,
        token,
        workspace_id,
        post_id,
        account.id,
        scheduled_for=datetime(2030, 1, 1, 9, 30),
        timezone="Asia/Kathmandu",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == ScheduleStatus.PENDING.value
    assert body["timezone"] == "Asia/Kathmandu"
    assert body["post_status"] == PostStatus.SCHEDULED.value

    stored = datetime.fromisoformat(body["scheduled_for"])
    assert stored == datetime(2030, 1, 1, 3, 45, tzinfo=dt_timezone.utc)
    local = datetime.fromisoformat(body["local_time"])
    assert local.utcoffset() == timedelta(hours=5, minutes=45)
    assert (local.hour, local.minute) == (9, 30)

    # Destinations are recorded as *pending* targets...
    target = db.scalars(select(PostTarget)).one()
    assert target.status is PostTargetStatus.PENDING
    assert target.post.status is PostStatus.SCHEDULED
    # ...but nothing has been queued or dispatched yet.
    assert db.scalars(select(PublishingJob)).all() == []
    assert dispatched == []


def test_scheduling_a_post_in_the_past_is_rejected(
    api_client, db, media_storage, publishing_storage
) -> None:
    token, workspace_id = _register(api_client, "past@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)

    response = _schedule(
        api_client,
        token,
        workspace_id,
        post_id,
        account.id,
        scheduled_for=utcnow() - timedelta(hours=1),
    )
    assert response.status_code == 422


def test_an_unknown_time_zone_is_rejected(
    api_client, db, media_storage, publishing_storage
) -> None:
    token, workspace_id = _register(api_client, "badzone@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)

    response = _schedule(
        api_client,
        token,
        workspace_id,
        post_id,
        account.id,
        timezone="Mars/Olympus_Mons",
    )
    assert response.status_code == 422
    assert "IANA" in response.json()["detail"]


def test_scheduling_an_already_scheduled_post_is_a_conflict(
    api_client, db, media_storage, publishing_storage
) -> None:
    token, workspace_id = _register(api_client, "dupe@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)

    assert _schedule(api_client, token, workspace_id, post_id, account.id).status_code == 201
    second = _schedule(api_client, token, workspace_id, post_id, account.id)
    assert second.status_code == 409
    assert "already scheduled" in second.json()["detail"].lower()


# ---------------------------------------------------------------------------
# The due-sweep
# ---------------------------------------------------------------------------
def test_sweep_dispatches_a_due_entry_through_the_publishing_path(
    api_client, db, media_storage, publishing_storage, dispatched, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "sweep@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    assert _schedule(api_client, token, workspace_id, post_id, account.id).status_code == 201

    _make_due(db)
    result = scheduling_service.sweep_due(db)

    assert result.claimed == 1
    assert result.dispatched == 1
    assert result.missed == 0

    schedule = db.scalars(select(ScheduledPost)).one()
    assert schedule.status is ScheduleStatus.DISPATCHED
    assert schedule.dispatched_at is not None

    job = db.scalars(select(PublishingJob)).one()
    assert job.status is JobStatus.QUEUED
    assert job.max_attempts == settings.publishing_max_attempts
    assert job.post_target.status is PostTargetStatus.QUEUED
    assert job.post_target.post.status is PostStatus.PUBLISHING
    assert len(dispatched) == 1

    # The job is a normal Phase 7 job: running it publishes for real.
    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, _facebook_success)
    assert publishing_service.run_job(db, job.id, storage=media_storage) == (
        JobStatus.SUCCEEDED.value
    )
    db.refresh(job)
    assert job.post_target.status is PostTargetStatus.PUBLISHED
    assert job.post_target.post.status is PostStatus.PUBLISHED


def test_sweep_never_fires_the_same_entry_twice(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "once@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)

    _make_due(db)
    first = scheduling_service.sweep_due(db)
    second = scheduling_service.sweep_due(db)

    assert first.dispatched == 1
    # The entry is no longer pending, so the re-run claims nothing and — most
    # importantly — creates no second job.
    assert second.claimed == 0
    assert db.scalars(select(PublishingJob)).all().__len__() == 1
    assert len(dispatched) == 1


def test_sweep_leaves_future_entries_alone(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "future@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)

    # Scheduled for an hour from now: nothing is due.
    result = scheduling_service.sweep_due(db)

    assert result.claimed == 0
    assert db.scalars(select(PublishingJob)).all() == []
    assert db.scalars(select(ScheduledPost)).one().status is ScheduleStatus.PENDING


def test_sweep_marks_an_overdue_entry_missed(
    api_client, db, media_storage, publishing_storage, dispatched, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "scheduling_missed_grace_seconds", 60)
    token, workspace_id = _register(api_client, "missed@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)

    # Two hours late — far beyond the grace window.
    _make_due(db, seconds_ago=7200)
    result = scheduling_service.sweep_due(db)

    assert result.missed == 1
    assert result.dispatched == 0
    schedule = db.scalars(select(ScheduledPost)).one()
    assert schedule.status is ScheduleStatus.MISSED
    # Nothing stale was published, and the post is a re-queueable draft again.
    assert db.scalars(select(PublishingJob)).all() == []
    assert db.scalars(select(PostTarget)).one().status is PostTargetStatus.CANCELLED
    assert db.scalars(select(PostTarget)).one().post.status is PostStatus.DRAFT
    assert dispatched == []


def test_a_scheduled_publish_and_a_manual_publish_share_the_idempotency_key(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "idem-sched@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    first_post = _compose(api_client, token, workspace_id, media.id, caption="same text")
    _schedule(api_client, token, workspace_id, first_post, account.id)

    _make_due(db)
    assert scheduling_service.sweep_due(db).dispatched == 1

    # Publishing the same video, to the same account, with the same text through
    # the manual route must be recognised as the very same publish.
    second_post = _compose(api_client, token, workspace_id, media.id, caption="same text")
    response = api_client.post(
        f"{POSTS}/{workspace_id}/{second_post}/publish",
        json={"targets": [{"social_account_id": str(account.id)}]},
        headers=_bearer(token),
    )

    assert response.status_code == 409
    assert "twice" in response.json()["detail"].lower()


def test_cancelling_a_schedule_prevents_it_from_firing(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "cancel@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)

    response = api_client.delete(
        f"{POSTS}/{workspace_id}/schedule/{post_id}", headers=_bearer(token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == ScheduleStatus.CANCELLED.value
    assert body["cancelled_at"] is not None

    # Even pulled into the past, a cancelled entry is invisible to the sweep.
    _make_due(db)
    assert scheduling_service.sweep_due(db).claimed == 0
    assert db.scalars(select(PublishingJob)).all() == []
    assert db.scalars(select(PostTarget)).one().status is PostTargetStatus.CANCELLED

    # And it stays cancelled.
    again = api_client.delete(
        f"{POSTS}/{workspace_id}/schedule/{post_id}", headers=_bearer(token)
    )
    assert again.status_code == 409


def test_reschedule_moves_a_pending_entry_to_a_new_time(
    api_client, db, media_storage, publishing_storage
) -> None:
    token, workspace_id = _register(api_client, "resched@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)

    new_time = utcnow() + timedelta(days=2)
    response = api_client.patch(
        f"{POSTS}/{workspace_id}/schedule/{post_id}",
        json={"scheduled_for": new_time.isoformat(), "timezone": "UTC"},
        headers=_bearer(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert datetime.fromisoformat(body["scheduled_for"]) == new_time
    assert body["status"] == ScheduleStatus.PENDING.value

    # A time that has already passed is refused.
    rejected = api_client.patch(
        f"{POSTS}/{workspace_id}/schedule/{post_id}",
        json={"scheduled_for": (utcnow() - timedelta(minutes=5)).isoformat()},
        headers=_bearer(token),
    )
    assert rejected.status_code == 422


def test_rescheduling_a_dispatched_entry_is_a_conflict(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "resched-fired@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)
    _make_due(db)
    assert scheduling_service.sweep_due(db).dispatched == 1

    response = api_client.patch(
        f"{POSTS}/{workspace_id}/schedule/{post_id}",
        json={"scheduled_for": (utcnow() + timedelta(days=1)).isoformat()},
        headers=_bearer(token),
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Reads + queue views
# ---------------------------------------------------------------------------
def test_listing_separates_the_upcoming_queue_from_history(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "listsched@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)

    due_post = _compose(api_client, token, workspace_id, media.id, caption="a")
    future_post = _compose(api_client, token, workspace_id, media.id, caption="b")
    _schedule(api_client, token, workspace_id, due_post, account.id)
    _schedule(api_client, token, workspace_id, future_post, account.id)
    # Dispatch the first (the one inserted first is the one `_make_due` pulls).
    schedule = db.scalars(
        select(ScheduledPost).where(ScheduledPost.post_id == uuid.UUID(due_post))
    ).one()
    schedule.scheduled_for = utcnow() - timedelta(seconds=5)
    db.flush()
    assert scheduling_service.sweep_due(db).dispatched == 1

    upcoming = api_client.get(
        f"{POSTS}/{workspace_id}/schedule?window=upcoming", headers=_bearer(token)
    ).json()
    past = api_client.get(
        f"{POSTS}/{workspace_id}/schedule?window=past", headers=_bearer(token)
    ).json()
    everything = api_client.get(
        f"{POSTS}/{workspace_id}/schedule?window=all", headers=_bearer(token)
    ).json()

    assert [item["post_id"] for item in upcoming] == [future_post]
    assert [item["post_id"] for item in past] == [due_post]
    assert len(everything) == 2


# ---------------------------------------------------------------------------
# Tenant isolation + RBAC
# ---------------------------------------------------------------------------
def test_another_workspaces_schedule_is_invisible_and_unreachable(
    api_client, db, media_storage, publishing_storage
) -> None:
    token, workspace_id = _register(api_client, "iso-a@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    _schedule(api_client, token, workspace_id, post_id, account.id)

    other_token, other_workspace = _register(api_client, "iso-b@example.com")

    # The other workspace's queue is empty...
    assert (
        api_client.get(f"{POSTS}/{other_workspace}/schedule", headers=_bearer(other_token)).json()
        == []
    )
    # ...and its entries cannot be edited through the other workspace's path.
    assert (
        api_client.patch(
            f"{POSTS}/{other_workspace}/schedule/{post_id}",
            json={"scheduled_for": (utcnow() + timedelta(days=1)).isoformat()},
            headers=_bearer(other_token),
        ).status_code
        == 404
    )
    assert (
        api_client.delete(
            f"{POSTS}/{other_workspace}/schedule/{post_id}", headers=_bearer(other_token)
        ).status_code
        == 404
    )
    # The owner cannot reach the other workspace either.
    assert (
        api_client.get(f"{POSTS}/{other_workspace}/schedule", headers=_bearer(token)).status_code
        == 404
    )


def test_viewer_may_read_the_queue_but_not_change_it(
    api_client, db, media_storage, publishing_storage
) -> None:
    owner_token, workspace_id = _register(api_client, "rbac-sched-owner@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id)
    post_id = _compose(api_client, owner_token, workspace_id, media.id)
    _schedule(api_client, owner_token, workspace_id, post_id, account.id)

    viewer_token, _ = _register(api_client, "rbac-sched-viewer@example.com")
    viewer = db.scalar(select(User).where(User.email == "rbac-sched-viewer@example.com"))
    _add_membership(db, workspace_id, viewer.id, "viewer")

    # schedule:read is granted to a viewer...
    assert (
        api_client.get(f"{POSTS}/{workspace_id}/schedule", headers=_bearer(viewer_token)).status_code
        == 200
    )
    # ...schedule:create and schedule:cancel are not.
    assert (
        api_client.patch(
            f"{POSTS}/{workspace_id}/schedule/{post_id}",
            json={"scheduled_for": (utcnow() + timedelta(days=1)).isoformat()},
            headers=_bearer(viewer_token),
        ).status_code
        == 403
    )
    assert (
        api_client.delete(
            f"{POSTS}/{workspace_id}/schedule/{post_id}", headers=_bearer(viewer_token)
        ).status_code
        == 403
    )


def test_schedule_endpoints_require_authentication(api_client, db, media_storage, publishing_storage) -> None:
    token, workspace_id = _register(api_client, "anon-sched@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    write_off = _ready_media(db, media_storage, workspace_id)
    post_id = _compose(api_client, token, workspace_id, media.id)
    del token

    assert api_client.get(f"{POSTS}/{workspace_id}/schedule").status_code == 401
    assert (
        api_client.post(
            f"{POSTS}/{workspace_id}/schedule",
            json={
                "post_id": str(post_id),
                "scheduled_for": (utcnow() + timedelta(days=1)).isoformat(),
                "targets": [{"social_account_id": str(uuid.uuid4())}],
            },
        ).status_code
        == 401
    )
    del write_off


# ---------------------------------------------------------------------------
# Service-level: time-zone handling
# ---------------------------------------------------------------------------
def test_normalize_timezone_defaults_and_validates(monkeypatch) -> None:
    monkeypatch.setattr(settings, "scheduling_default_timezone", "Europe/London")
    assert scheduling_service.normalize_timezone(None) == "Europe/London"
    assert scheduling_service.normalize_timezone(" Asia/Kathmandu ") == "Asia/Kathmandu"
    with pytest.raises(scheduling_service.InvalidTimezoneError):
        scheduling_service.normalize_timezone("Not/AZone")


def test_to_utc_instant_reads_naive_values_in_the_chosen_zone() -> None:
    naive = datetime(2030, 6, 1, 12, 0)
    assert scheduling_service.to_utc_instant(naive, "Asia/Kathmandu") == datetime(
        2030, 6, 1, 6, 15, tzinfo=dt_timezone.utc
    )
    aware = datetime(2030, 6, 1, 12, 0, tzinfo=ZoneInfo("Europe/London"))
    # British Summer Time is UTC+1 in June.
    assert scheduling_service.to_utc_instant(aware, "Europe/London") == datetime(
        2030, 6, 1, 11, 0, tzinfo=dt_timezone.utc
    )


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------
def test_the_due_sweep_is_on_the_celery_beat_schedule() -> None:
    from app.worker import celery_app

    assert "scheduling.dispatch_due" in celery_app.conf.beat_schedule
    entry = celery_app.conf.beat_schedule["scheduling.dispatch_due"]
    assert entry["task"] == "scheduling.dispatch_due"
    assert entry["schedule"] > 0
