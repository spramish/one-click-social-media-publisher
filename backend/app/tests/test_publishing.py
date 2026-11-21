"""Phase 7 integration tests — the "Publish Everywhere" engine and its API.

These drive the real FastAPI app through ``TestClient`` against the migrated
``publisher_test`` database, with each platform's HTTP layer swapped for an
``httpx.MockTransport`` and Celery's dispatch replaced by a recorder. No test
reaches a real platform or a real broker; what is exercised for real is the
orchestration:

* validation happens before anything is dispatched, and a destination that
  fails it is recorded as ``skipped`` without affecting the others;
* one target + one independent job is created per destination, so a failure on
  one platform can never cancel another;
* a job only reaches ``succeeded`` when the platform confirms the publish, and
  a post only reaches ``published`` when *every* target confirmed;
* duplicate publishes are refused via a stable idempotency key;
* retryable failures back off (honouring ``Retry-After``) and dead-letter once
  ``max_attempts`` is reached;
* tenant isolation (another workspace's post/job is ``404``) and RBAC (a viewer
  may read but not publish) hold on every route.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.security import utcnow
from app.db.enums import (
    JobStatus,
    MediaStatus,
    NotificationType,
    PostStatus,
    PostTargetStatus,
    SocialAccountStatus,
    SocialPlatform,
    StorageBackend,
    WorkspaceMemberStatus,
)
from app.models import (
    MediaFile,
    Notification,
    OAuthCredential,
    Post,
    PublishingJob,
    SocialAccount,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services import auth as auth_service
from app.services import publishing as publishing_service
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


def _workspace(db, *, email: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    workspace = Workspace(name=slug, slug=slug, owner_id=user.id)
    db.add(workspace)
    db.flush()
    return user, workspace


def _ready_media(
    db,
    storage,
    workspace_id: uuid.UUID,
    *,
    mime_type: str = "video/mp4",
    size_bytes: int = 4096,
    duration: float | None = 12.0,
    width: int | None = 1080,
    height: int | None = 1920,
    uploaded_by: uuid.UUID | None = None,
) -> MediaFile:
    """A READY media row backed by a real (tiny) file in the test storage."""
    media_id = uuid.uuid4()
    key = storage_service.original_key(workspace_id, media_id, ".mp4")
    storage.write_bytes(key, b"\x00" * 64)
    media = MediaFile(
        id=media_id,
        workspace_id=workspace_id,
        uploaded_by_id=uploaded_by,
        storage_backend=StorageBackend.LOCAL,
        storage_key=key,
        original_filename="clip.mp4",
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum_sha256=hashlib.sha256(str(media_id).encode()).hexdigest(),
        duration_seconds=duration,
        width=width,
        height=height,
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
    display_name: str = "Account One",
    status: SocialAccountStatus = SocialAccountStatus.CONNECTED,
) -> SocialAccount:
    account = SocialAccount(
        workspace_id=workspace_id,
        platform=platform,
        platform_account_id=account_id,
        display_name=display_name,
        status=status,
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


def _jobs_by_platform(db) -> dict[SocialPlatform, PublishingJob]:
    jobs = db.scalars(select(PublishingJob)).all()
    return {job.post_target.platform: job for job in jobs}


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
def test_capabilities_endpoint_lists_every_platform(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "caps-posts@example.com")

    response = api_client.get(f"{POSTS}/{workspace_id}/capabilities", headers=_bearer(token))

    assert response.status_code == 200
    capabilities = {item["platform"]: item for item in response.json()}
    assert set(capabilities) == {"instagram", "facebook", "youtube", "tiktok"}
    assert capabilities["youtube"]["requires_title"] is True
    assert capabilities["instagram"]["max_caption_length"] == 2200


# ---------------------------------------------------------------------------
# Compose + dispatch
# ---------------------------------------------------------------------------
def test_compose_then_publish_everywhere_creates_one_job_per_target(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "dispatch@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    facebook = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    youtube = _connected_account(
        db, workspace_id, SocialPlatform.YOUTUBE, account_id="chan-1", display_name="Channel"
    )

    created = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "title": "Launch", "base_caption": "Our new video"},
        headers=_bearer(token),
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    assert created.json()["status"] == PostStatus.DRAFT.value

    response = api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish",
        json={
            "targets": [
                {"social_account_id": str(facebook.id)},
                {"social_account_id": str(youtube.id), "title": "YouTube title"},
            ]
        },
        headers=_bearer(token),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    # The post is *publishing*, not published: nothing is confirmed yet.
    assert body["status"] == PostStatus.PUBLISHING.value
    targets = {target["platform"]: target for target in body["targets"]}
    assert set(targets) == {"facebook", "youtube"}
    for target in targets.values():
        assert target["status"] == PostTargetStatus.QUEUED.value
        assert target["job"] is not None
        assert target["job"]["status"] == JobStatus.QUEUED.value
    # Two destinations -> two independent dispatches.
    assert len(dispatched) == 2
    assert all(countdown is None for _, countdown in dispatched)


def test_unpublishable_destination_is_skipped_without_blocking_others(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "skip@example.com")
    # Matroska is accepted by YouTube but not by Facebook.
    media = _ready_media(db, media_storage, workspace_id, mime_type="video/x-matroska")
    facebook = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    youtube = _connected_account(
        db, workspace_id, SocialPlatform.YOUTUBE, account_id="chan-1", display_name="Channel"
    )
    post_id = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(token),
    ).json()["id"]

    response = api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish",
        json={
            "targets": [
                {"social_account_id": str(facebook.id)},
                {"social_account_id": str(youtube.id), "title": "YT"},
            ]
        },
        headers=_bearer(token),
    )

    assert response.status_code == 202, response.text
    targets = {target["platform"]: target for target in response.json()["targets"]}
    assert targets["facebook"]["status"] == PostTargetStatus.SKIPPED.value
    assert "does not accept" in targets["facebook"]["error_message"]
    assert targets["youtube"]["status"] == PostTargetStatus.QUEUED.value
    # Only the destination that passed validation was dispatched.
    assert len(dispatched) == 1


def test_publish_everywhere_with_nothing_publishable_is_a_409(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "nopublish@example.com")
    media = _ready_media(db, media_storage, workspace_id, mime_type="video/x-matroska")
    facebook = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    post_id = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(token),
    ).json()["id"]

    response = api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish",
        json={"targets": [{"social_account_id": str(facebook.id)}]},
        headers=_bearer(token),
    )

    assert response.status_code == 409
    assert "does not accept" in response.json()["detail"]
    assert dispatched == []


def test_publish_without_targets_is_rejected(api_client, db, media_storage, publishing_storage) -> None:
    token, workspace_id = _register(api_client, "notargets@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    post_id = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(token),
    ).json()["id"]

    response = api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish",
        json={"targets": []},
        headers=_bearer(token),
    )
    assert response.status_code == 422


def test_composing_with_a_processing_video_is_a_409(
    api_client, db, media_storage, publishing_storage
) -> None:
    token, workspace_id = _register(api_client, "notready@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    media.status = MediaStatus.PROCESSING
    db.flush()

    response = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(token),
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_republishing_the_same_video_to_the_same_account_is_refused(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    token, workspace_id = _register(api_client, "idempotent@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    facebook = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    payload = {"media_id": str(media.id), "base_caption": "same text"}

    first = api_client.post(f"{POSTS}/{workspace_id}", json=payload, headers=_bearer(token)).json()
    first_publish = api_client.post(
        f"{POSTS}/{workspace_id}/{first['id']}/publish",
        json={"targets": [{"social_account_id": str(facebook.id)}]},
        headers=_bearer(token),
    )
    assert first_publish.status_code == 202
    assert len(dispatched) == 1

    # A second post of the same file, same text, same account is a duplicate.
    second = api_client.post(f"{POSTS}/{workspace_id}", json=payload, headers=_bearer(token)).json()
    second_publish = api_client.post(
        f"{POSTS}/{workspace_id}/{second['id']}/publish",
        json={"targets": [{"social_account_id": str(facebook.id)}]},
        headers=_bearer(token),
    )

    assert second_publish.status_code == 409
    assert "twice" in second_publish.json()["detail"].lower()
    # Nothing new was dispatched.
    assert len(dispatched) == 1


# ---------------------------------------------------------------------------
# Execution: success, retry/backoff, dead-letter, partial failure
# ---------------------------------------------------------------------------
def test_run_job_confirms_success_and_notifies(
    db, media_storage, dispatched, monkeypatch
) -> None:
    user, workspace = _workspace(db, email="success@example.com", slug="ws-success")
    media = _ready_media(db, media_storage, workspace.id, uploaded_by=user.id)
    account = _connected_account(db, workspace.id, SocialPlatform.FACEBOOK, account_id="page-1")
    post = publishing_service.create_post(
        db,
        workspace_id=workspace.id,
        user_id=user.id,
        media_id=media.id,
        base_caption="hello",
    )
    db.flush()
    publishing_service.publish_everywhere(
        db,
        workspace_id=workspace.id,
        post_id=post.id,
        selections=[publishing_service.TargetSelection(social_account_id=account.id)],
        storage=media_storage,
    )
    db.flush()
    job = db.scalars(select(PublishingJob)).one()

    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, _facebook_success)
    status_name = publishing_service.run_job(db, job.id, storage=media_storage)

    assert status_name == JobStatus.SUCCEEDED.value
    db.refresh(job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.attempt_count == 1
    assert job.finished_at is not None

    target = job.post_target
    assert target.status is PostTargetStatus.PUBLISHED
    assert target.remote_post_id == "vid-1"
    assert target.remote_url == "https://fb/vid-1"
    assert target.published_at is not None

    assert target.post.status is PostStatus.PUBLISHED
    assert target.post.published_at is not None

    notification = db.scalar(select(Notification).where(Notification.workspace_id == workspace.id))
    assert notification is not None
    assert notification.type is NotificationType.PUBLISH_SUCCESS
    # Every attempt leaves a log trail.
    assert len(job.logs) >= 2


def test_retryable_failure_backs_off_then_dead_letters(
    db, media_storage, dispatched, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "publishing_max_attempts", 2)
    monkeypatch.setattr(settings, "publishing_retry_base_seconds", 30)

    user, workspace = _workspace(db, email="retry@example.com", slug="ws-retry")
    media = _ready_media(db, media_storage, workspace.id, uploaded_by=user.id)
    account = _connected_account(db, workspace.id, SocialPlatform.FACEBOOK, account_id="page-1")
    post = publishing_service.create_post(
        db, workspace_id=workspace.id, user_id=user.id, media_id=media.id, base_caption="hi"
    )
    db.flush()
    publishing_service.publish_everywhere(
        db,
        workspace_id=workspace.id,
        post_id=post.id,
        selections=[publishing_service.TargetSelection(social_account_id=account.id)],
        storage=media_storage,
    )
    db.flush()
    job = db.scalars(select(PublishingJob)).one()
    assert job.max_attempts == 2

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, failing)

    # Attempt 1 -> retryable, scheduled in the future.
    assert publishing_service.run_job(db, job.id, storage=media_storage) == JobStatus.RETRYING.value
    db.refresh(job)
    assert job.attempt_count == 1
    assert job.status is JobStatus.RETRYING
    assert job.next_retry_at is not None
    assert job.post_target.status is PostTargetStatus.QUEUED
    assert job.post_target.post.status is PostStatus.PUBLISHING
    # The retry was re-dispatched with a countdown.
    assert any(countdown is not None for _, countdown in dispatched)

    # Attempt 2 exhausts the budget -> dead letter, never published.
    assert (
        publishing_service.run_job(db, job.id, storage=media_storage)
        == JobStatus.DEAD_LETTER.value
    )
    db.refresh(job)
    assert job.attempt_count == 2
    assert job.status is JobStatus.DEAD_LETTER
    assert job.post_target.status is PostTargetStatus.FAILED
    assert job.post_target.post.status is PostStatus.FAILED
    assert job.post_target.remote_post_id is None

    notification = db.scalar(
        select(Notification).where(Notification.type == NotificationType.PUBLISH_FAILURE)
    )
    assert notification is not None


def test_rate_limit_backoff_honours_retry_after(db, media_storage, dispatched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "publishing_max_attempts", 5)
    monkeypatch.setattr(settings, "publishing_retry_base_seconds", 1)
    monkeypatch.setattr(settings, "publishing_retry_max_seconds", 60)

    user, workspace = _workspace(db, email="ratelimit@example.com", slug="ws-rate")
    media = _ready_media(db, media_storage, workspace.id, uploaded_by=user.id)
    account = _connected_account(db, workspace.id, SocialPlatform.FACEBOOK, account_id="page-1")
    post = publishing_service.create_post(
        db, workspace_id=workspace.id, user_id=user.id, media_id=media.id, base_caption="hi"
    )
    db.flush()
    publishing_service.publish_everywhere(
        db,
        workspace_id=workspace.id,
        post_id=post.id,
        selections=[publishing_service.TargetSelection(social_account_id=account.id)],
        storage=media_storage,
    )
    db.flush()
    job = db.scalars(select(PublishingJob)).one()

    def throttled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "300"}, json={"error": {"message": "slow down"}}
        )

    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, throttled)

    assert publishing_service.run_job(db, job.id, storage=media_storage) == JobStatus.RETRYING.value
    db.refresh(job)
    # The platform's own delay wins over the (much shorter) computed backoff.
    assert job.next_retry_at is not None
    assert (job.next_retry_at - utcnow()).total_seconds() >= 250


def test_permanent_failure_does_not_retry(db, media_storage, dispatched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "publishing_max_attempts", 5)

    user, workspace = _workspace(db, email="permanent@example.com", slug="ws-perm")
    media = _ready_media(db, media_storage, workspace.id, uploaded_by=user.id)
    account = _connected_account(db, workspace.id, SocialPlatform.FACEBOOK, account_id="page-1")
    post = publishing_service.create_post(
        db, workspace_id=workspace.id, user_id=user.id, media_id=media.id, base_caption="hi"
    )
    db.flush()
    publishing_service.publish_everywhere(
        db,
        workspace_id=workspace.id,
        post_id=post.id,
        selections=[publishing_service.TargetSelection(social_account_id=account.id)],
        storage=media_storage,
    )
    db.flush()
    job = db.scalars(select(PublishingJob)).one()

    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid video format"}})

    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, rejected)

    # 400 is classified permanent: one attempt, straight to failed.
    assert publishing_service.run_job(db, job.id, storage=media_storage) == JobStatus.FAILED.value
    db.refresh(job)
    assert job.attempt_count == 1
    assert job.status is JobStatus.FAILED
    assert job.next_retry_at is None
    assert job.post_target.status is PostTargetStatus.FAILED


def test_one_platforms_failure_never_cancels_another(
    db, media_storage, dispatched, monkeypatch
) -> None:
    user, workspace = _workspace(db, email="partial@example.com", slug="ws-partial")
    media = _ready_media(db, media_storage, workspace.id, uploaded_by=user.id)
    facebook = _connected_account(db, workspace.id, SocialPlatform.FACEBOOK, account_id="page-1")
    instagram = _connected_account(
        db, workspace.id, SocialPlatform.INSTAGRAM, account_id="ig-1", display_name="IG"
    )
    post = publishing_service.create_post(
        db, workspace_id=workspace.id, user_id=user.id, media_id=media.id, base_caption="hi"
    )
    db.flush()
    publishing_service.publish_everywhere(
        db,
        workspace_id=workspace.id,
        post_id=post.id,
        selections=[
            publishing_service.TargetSelection(social_account_id=facebook.id),
            publishing_service.TargetSelection(social_account_id=instagram.id),
        ],
        storage=media_storage,
    )
    db.flush()

    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, _facebook_success)

    def ig_rejects(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "Instagram rejected the Reel"}})

    _install_transport(monkeypatch, SocialPlatform.INSTAGRAM, ig_rejects)

    for job in _jobs_by_platform(db).values():
        publishing_service.run_job(db, job.id, storage=media_storage)

    jobs = _jobs_by_platform(db)
    assert jobs[SocialPlatform.FACEBOOK].status is JobStatus.SUCCEEDED
    assert jobs[SocialPlatform.FACEBOOK].post_target.status is PostTargetStatus.PUBLISHED
    assert jobs[SocialPlatform.INSTAGRAM].status is JobStatus.FAILED
    assert jobs[SocialPlatform.INSTAGRAM].post_target.status is PostTargetStatus.FAILED
    # The post reflects the mixed outcome rather than being cancelled.
    assert jobs[SocialPlatform.FACEBOOK].post_target.post.status is PostStatus.PARTIALLY_PUBLISHED


def test_run_job_is_idempotent_once_terminal(db, media_storage, dispatched, monkeypatch) -> None:
    user, workspace = _workspace(db, email="idem-job@example.com", slug="ws-idem")
    media = _ready_media(db, media_storage, workspace.id, uploaded_by=user.id)
    account = _connected_account(db, workspace.id, SocialPlatform.FACEBOOK, account_id="page-1")
    post = publishing_service.create_post(
        db, workspace_id=workspace.id, user_id=user.id, media_id=media.id, base_caption="hi"
    )
    db.flush()
    publishing_service.publish_everywhere(
        db,
        workspace_id=workspace.id,
        post_id=post.id,
        selections=[publishing_service.TargetSelection(social_account_id=account.id)],
        storage=media_storage,
    )
    db.flush()
    job = db.scalars(select(PublishingJob)).one()

    _install_transport(monkeypatch, SocialPlatform.FACEBOOK, _facebook_success)
    publishing_service.run_job(db, job.id, storage=media_storage)

    # A re-delivered Celery message must not publish a second time.
    assert publishing_service.run_job(db, job.id, storage=media_storage) == JobStatus.SUCCEEDED.value
    db.refresh(job)
    assert job.attempt_count == 1


# ---------------------------------------------------------------------------
# Tenant isolation + RBAC
# ---------------------------------------------------------------------------
def test_another_workspaces_post_and_job_are_404(
    api_client, db, media_storage, publishing_storage, dispatched, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "iso-posts@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    post_id = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(token),
    ).json()["id"]
    api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish",
        json={"targets": [{"social_account_id": str(account.id)}]},
        headers=_bearer(token),
    )
    job_id = str(db.scalars(select(PublishingJob)).one().id)

    other_token, other_workspace = _register(api_client, "iso-other@example.com")

    # The owner cannot see the other workspace's post/job...
    assert (
        api_client.get(f"{POSTS}/{other_workspace}/{post_id}", headers=_bearer(token)).status_code
        == 404
    )
    assert (
        api_client.get(
            f"{POSTS}/{other_workspace}/jobs/{job_id}", headers=_bearer(token)
        ).status_code
        == 404
    )
    # ...and a non-member cannot see this workspace at all (404, never 403).
    assert (
        api_client.get(f"{POSTS}/{workspace_id}/{post_id}", headers=_bearer(other_token)).status_code
        == 404
    )


def test_viewer_can_read_but_not_publish(
    api_client, db, media_storage, publishing_storage, dispatched
) -> None:
    owner_token, workspace_id = _register(api_client, "rbac-owner@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    account = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    post_id = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(owner_token),
    ).json()["id"]

    viewer_token, _ = _register(api_client, "rbac-viewer@example.com")
    viewer = db.scalar(select(User).where(User.email == "rbac-viewer@example.com"))
    _add_membership(db, workspace_id, viewer.id, "viewer")

    # post:read is granted to a viewer...
    assert api_client.get(f"{POSTS}/{workspace_id}", headers=_bearer(viewer_token)).status_code == 200
    # ...post:publish is not.
    denied = api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish",
        json={"targets": [{"social_account_id": str(account.id)}]},
        headers=_bearer(viewer_token),
    )
    assert denied.status_code == 403


def test_endpoints_require_authentication(api_client, db, media_storage, publishing_storage) -> None:
    token, workspace_id = _register(api_client, "anon-posts@example.com")
    media = _ready_media(db, media_storage, workspace_id)
    post_id = api_client.post(
        f"{POSTS}/{workspace_id}",
        json={"media_id": str(media.id), "base_caption": "hi"},
        headers=_bearer(token),
    ).json()["id"]
    del token

    assert api_client.get(f"{POSTS}/{workspace_id}").status_code == 401
    assert api_client.get(f"{POSTS}/{workspace_id}/{post_id}").status_code == 401
    assert api_client.post(
        f"{POSTS}/{workspace_id}/{post_id}/publish", json={"targets": []}
    ).status_code == 401


# ---------------------------------------------------------------------------
# Service: backoff maths
# ---------------------------------------------------------------------------
def test_backoff_grows_exponentially_and_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "publishing_retry_base_seconds", 30)
    monkeypatch.setattr(settings, "publishing_retry_max_seconds", 3600)

    assert publishing_service._backoff_seconds(1) == 30
    assert publishing_service._backoff_seconds(2) == 60
    assert publishing_service._backoff_seconds(3) == 120
    # Capped, not unbounded.
    assert publishing_service._backoff_seconds(20) == 3600
    # A platform-supplied Retry-After always wins when it is longer.
    assert publishing_service._backoff_seconds(1, retry_after=900) == 900


def test_idempotency_key_prefers_the_content_checksum(tmp_path) -> None:
    from app.services.publishers import MediaAsset

    workspace_id = uuid.uuid4()
    account = SocialAccount(
        workspace_id=workspace_id,
        platform=SocialPlatform.FACEBOOK,
        platform_account_id="p",
        display_name="P",
    )
    account.id = uuid.uuid4()
    common = {
        "media_id": uuid.uuid4(),
        "path": None,
        "size_bytes": 1,
        "mime_type": "video/mp4",
        "duration_seconds": None,
        "width": None,
        "height": None,
        "public_url": "https://cdn/x",
        "checksum_sha256": "a" * 64,
    }
    first = publishing_service._idempotency_key(
        workspace_id=workspace_id,
        media=MediaAsset(**common),
        account=account,
        caption="text",
        title="t",
    )
    # A different media row with the *same* bytes must collide...
    second = publishing_service._idempotency_key(
        workspace_id=workspace_id,
        media=MediaAsset(**{**common, "media_id": uuid.uuid4()}),
        account=account,
        caption="text",
        title="t",
    )
    assert first == second
    # ...and a different caption must not.
    third = publishing_service._idempotency_key(
        workspace_id=workspace_id,
        media=MediaAsset(**common),
        account=account,
        caption="other",
        title="t",
    )
    assert third != first
