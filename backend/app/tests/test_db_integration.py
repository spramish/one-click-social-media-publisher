"""Phase 2 integration tests — real PostgreSQL behaviour.

Covers the database-level guarantees the application relies on:

* UUID primary keys and audit timestamps are populated by the database.
* Unique and CHECK constraints actually reject invalid rows.
* ``ON DELETE`` rules behave as designed (CASCADE, RESTRICT, SET NULL).
* ``TenantMixin.scoped`` isolates one workspace from another.
* The reference data seeded by migration ``70ed893bdb70`` is present.

Every test runs inside a transaction that is rolled back by the ``db``
fixture, so the suite leaves the test database untouched and tests cannot
observe each other's rows.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.enums import AIContentKind, NotificationType, SocialPlatform
from app.models import (
    AIGeneratedContent,
    MediaFile,
    Notification,
    OAuthCredential,
    Plan,
    Post,
    PostTarget,
    PublishingJob,
    PublishingLog,
    Role,
    ScheduledPost,
    SocialAccount,
    Subscription,
    User,
    Workspace,
    WorkspaceMember,
)

# Tables whose rows must disappear when the owning workspace is deleted.
# ``notifications`` is deliberately absent: its workspace FK is ON DELETE SET
# NULL so notifications survive the workspace (see the dedicated test below).
CASCADE_TABLES = [
    WorkspaceMember,
    Subscription,
    SocialAccount,
    OAuthCredential,
    MediaFile,
    Post,
    PostTarget,
    PublishingJob,
    PublishingLog,
    ScheduledPost,
    AIGeneratedContent,
]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def owner_role(db) -> Role:
    return db.execute(select(Role).where(Role.name == "owner")).scalar_one()


@pytest.fixture
def free_plan(db) -> Plan:
    return db.execute(select(Plan).where(Plan.code == "free")).scalar_one()


@pytest.fixture
def user(db) -> User:
    row = User(
        email="pramish@example.com",
        hashed_password="hashed-not-real",
        full_name="Pramish Sharma",
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def workspace(db, user) -> Workspace:
    row = Workspace(name="Acme Studio", slug="acme-studio", owner_id=user.id)
    db.add(row)
    db.flush()
    return row


def _make_media(workspace: Workspace, key: str = "media/clip.mp4") -> MediaFile:
    return MediaFile(
        workspace_id=workspace.id,
        storage_key=key,
        original_filename=key.rsplit("/", 1)[-1],
        mime_type="video/mp4",
        size_bytes=1024,
        duration_seconds=12.5,
    )


def _make_account(workspace: Workspace, account_id: str = "acc-1") -> SocialAccount:
    return SocialAccount(
        workspace_id=workspace.id,
        platform=SocialPlatform.INSTAGRAM,
        platform_account_id=account_id,
        display_name="Acme on Instagram",
    )


# --------------------------------------------------------------------------
# Primary keys and timestamps
# --------------------------------------------------------------------------
def test_uuid_primary_key_and_timestamps_are_generated(db, user) -> None:
    assert isinstance(user.id, uuid.UUID)
    assert user.created_at is not None
    assert user.updated_at is not None
    assert user.created_at.tzinfo is not None
    assert user.is_deleted is False


def test_two_users_get_distinct_uuids(db) -> None:
    first = User(email="a@example.com", hashed_password="x")
    second = User(email="b@example.com", hashed_password="x")
    db.add_all([first, second])
    db.flush()
    assert first.id != second.id


def test_updated_at_advances_on_modification(db, user) -> None:
    before = user.updated_at
    user.full_name = "Pramish S."
    db.flush()
    db.refresh(user)
    assert user.updated_at >= before


# --------------------------------------------------------------------------
# Unique constraints
# --------------------------------------------------------------------------
def test_duplicate_email_is_rejected(db, user) -> None:
    db.add(User(email=user.email, hashed_password="x"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_duplicate_workspace_slug_is_rejected(db, workspace) -> None:
    db.add(Workspace(name="Other", slug=workspace.slug, owner_id=workspace.owner_id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_user_cannot_join_same_workspace_twice(db, workspace, user, owner_role) -> None:
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role_id=owner_role.id))
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role_id=owner_role.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_same_platform_account_cannot_be_connected_twice(db, workspace) -> None:
    db.add(_make_account(workspace))
    db.flush()
    db.add(_make_account(workspace))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_idempotency_key_is_unique_across_jobs(db, workspace, user) -> None:
    media = _make_media(workspace)
    account = _make_account(workspace)
    db.add_all([media, account])
    db.flush()
    post = Post(workspace_id=workspace.id, media_file_id=media.id, created_by_id=user.id)
    db.add(post)
    db.flush()
    target = PostTarget(
        workspace_id=workspace.id,
        post_id=post.id,
        social_account_id=account.id,
        platform=SocialPlatform.INSTAGRAM,
    )
    db.add(target)
    db.flush()

    db.add(PublishingJob(post_target_id=target.id, workspace_id=workspace.id, idempotency_key="job-1"))
    db.flush()
    db.add(PublishingJob(post_target_id=target.id, workspace_id=workspace.id, idempotency_key="job-1"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_one_oauth_credential_per_social_account(db, workspace) -> None:
    account = _make_account(workspace)
    db.add(account)
    db.flush()
    db.add(OAuthCredential(social_account_id=account.id, access_token_encrypted="enc-1"))
    db.flush()
    db.add(OAuthCredential(social_account_id=account.id, access_token_encrypted="enc-2"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------
# CHECK constraints
# --------------------------------------------------------------------------
def test_negative_media_size_is_rejected(db, workspace) -> None:
    media = _make_media(workspace, "media/bad.mp4")
    media.size_bytes = -1
    db.add(media)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_negative_media_duration_is_rejected(db, workspace) -> None:
    media = _make_media(workspace, "media/bad2.mp4")
    media.duration_seconds = -5.0
    db.add(media)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_negative_plan_price_is_rejected(db) -> None:
    db.add(Plan(code="broken", name="Broken", price_cents=-1))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_negative_job_attempt_count_is_rejected(db, workspace, user) -> None:
    media = _make_media(workspace)
    account = _make_account(workspace)
    db.add_all([media, account])
    db.flush()
    post = Post(workspace_id=workspace.id, media_file_id=media.id, created_by_id=user.id)
    db.add(post)
    db.flush()
    target = PostTarget(
        workspace_id=workspace.id,
        post_id=post.id,
        social_account_id=account.id,
        platform=SocialPlatform.INSTAGRAM,
    )
    db.add(target)
    db.flush()

    db.add(
        PublishingJob(
            post_target_id=target.id,
            workspace_id=workspace.id,
            idempotency_key="bad-attempts",
            attempt_count=-1,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_unknown_enum_value_is_rejected_by_check_constraint(db, workspace) -> None:
    """The VARCHAR + CHECK enum strategy rejects values outside the catalogue."""
    db.flush()
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO media_files "
                "(workspace_id, storage_backend, storage_key, original_filename, "
                " mime_type, size_bytes, status) "
                "VALUES (:ws, 'local', 'media/x.mp4', 'x.mp4', 'video/mp4', 1, 'bogus')"
            ),
            {"ws": str(workspace.id)},
        )
    db.rollback()


# --------------------------------------------------------------------------
# ON DELETE rules
# --------------------------------------------------------------------------
def test_owner_with_workspace_cannot_be_deleted(db, workspace, user) -> None:
    """``workspaces.owner_id`` is RESTRICT — ownership must be transferred first."""
    with pytest.raises(IntegrityError):
        db.execute(delete(User).where(User.id == user.id))
    db.rollback()


def test_workspace_member_cannot_be_deleted_while_role_in_use(db, workspace, user, owner_role) -> None:
    """``workspace_members.role_id`` is RESTRICT — a role in use can't be dropped."""
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role_id=owner_role.id))
    db.flush()
    with pytest.raises(IntegrityError):
        db.execute(delete(Role).where(Role.id == owner_role.id))
    db.rollback()


def test_deleting_creator_sets_post_author_to_null(db, workspace, user) -> None:
    other_owner = User(email="other@example.com", hashed_password="x")
    db.add(other_owner)
    db.flush()
    workspace.owner_id = other_owner.id

    media = _make_media(workspace)
    db.add(media)
    db.flush()
    post = Post(workspace_id=workspace.id, media_file_id=media.id, created_by_id=user.id)
    db.add(post)
    db.flush()
    post_id = post.id

    db.execute(delete(User).where(User.id == user.id))
    db.flush()
    db.expire_all()

    refreshed = db.get(Post, post_id)
    assert refreshed is not None
    assert refreshed.created_by_id is None


def test_deleting_uploader_keeps_media_file(db, workspace, user) -> None:
    other_owner = User(email="other2@example.com", hashed_password="x")
    db.add(other_owner)
    db.flush()
    workspace.owner_id = other_owner.id

    media = _make_media(workspace)
    media.uploaded_by_id = user.id
    db.add(media)
    db.flush()
    media_id = media.id

    db.execute(delete(User).where(User.id == user.id))
    db.flush()
    db.expire_all()

    refreshed = db.get(MediaFile, media_id)
    assert refreshed is not None
    assert refreshed.uploaded_by_id is None


def test_deleting_workspace_removes_every_child_row(db, workspace, user, owner_role, free_plan) -> None:
    """One workspace delete must tear down the whole tenant subtree."""
    media = _make_media(workspace)
    account = _make_account(workspace)
    db.add_all([media, account])
    db.flush()

    post = Post(workspace_id=workspace.id, media_file_id=media.id, created_by_id=user.id)
    db.add(post)
    db.flush()

    target = PostTarget(
        workspace_id=workspace.id,
        post_id=post.id,
        social_account_id=account.id,
        platform=SocialPlatform.INSTAGRAM,
    )
    db.add(target)
    db.flush()

    job = PublishingJob(post_target_id=target.id, workspace_id=workspace.id, idempotency_key="cascade-1")
    db.add(job)
    db.flush()

    db.add_all(
        [
            PublishingLog(publishing_job_id=job.id, message="publishing started"),
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role_id=owner_role.id),
            ScheduledPost(
                workspace_id=workspace.id,
                post_id=post.id,
                scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
            AIGeneratedContent(
                workspace_id=workspace.id,
                post_id=post.id,
                created_by_id=user.id,
                kind=AIContentKind.CAPTION,
                model="gpt-4o-mini",
                prompt="Write a caption",
                output="Launch day!",
            ),
            Subscription(workspace_id=workspace.id, plan_id=free_plan.id),
            OAuthCredential(social_account_id=account.id, access_token_encrypted="enc"),
            Notification(
                user_id=user.id,
                workspace_id=workspace.id,
                type=NotificationType.SYSTEM,
                title="Welcome",
            ),
        ]
    )
    db.flush()

    db.execute(delete(Workspace).where(Workspace.id == workspace.id))
    db.flush()

    for model in CASCADE_TABLES:
        remaining = db.execute(select(func.count()).select_from(model)).scalar_one()
        assert remaining == 0, f"{model.__name__} rows survived the workspace cascade"


def test_deleting_workspace_keeps_user_and_notification(db, workspace, user) -> None:
    """Notifications outlive the workspace; only the FK is nulled."""
    notification = Notification(
        user_id=user.id,
        workspace_id=workspace.id,
        type=NotificationType.PUBLISH_SUCCESS,
        title="Published",
    )
    db.add(notification)
    db.flush()
    notification_id = notification.id

    db.execute(delete(Workspace).where(Workspace.id == workspace.id))
    db.flush()
    db.expire_all()

    refreshed = db.get(Notification, notification_id)
    assert refreshed is not None
    assert refreshed.workspace_id is None
    assert db.get(User, user.id) is not None


def test_deleting_user_removes_their_notifications(db, user) -> None:
    db.add(
        Notification(
            user_id=user.id,
            workspace_id=None,
            type=NotificationType.SYSTEM,
            title="Heads up",
        )
    )
    db.flush()

    db.execute(delete(User).where(User.id == user.id))
    db.flush()

    assert db.execute(select(func.count()).select_from(Notification)).scalar_one() == 0


# --------------------------------------------------------------------------
# Tenant isolation (TenantMixin.scoped)
# --------------------------------------------------------------------------
def test_scoped_returns_only_the_requested_workspace(db, user) -> None:
    first = Workspace(name="One", slug="one", owner_id=user.id)
    second = Workspace(name="Two", slug="two", owner_id=user.id)
    db.add_all([first, second])
    db.flush()

    first_media = _make_media(first, "media/one.mp4")
    second_media = _make_media(second, "media/two.mp4")
    db.add_all([first_media, second_media])
    db.flush()

    rows = db.execute(MediaFile.scoped(first.id)).scalars().all()
    assert [row.id for row in rows] == [first_media.id]


def test_scoped_hides_soft_deleted_rows_unless_requested(db, workspace) -> None:
    alive = _make_media(workspace, "media/alive.mp4")
    gone = _make_media(workspace, "media/gone.mp4")
    db.add_all([alive, gone])
    db.flush()

    gone.deleted_at = datetime.now(timezone.utc)
    db.flush()

    visible = db.execute(MediaFile.scoped(workspace.id)).scalars().all()
    assert [row.id for row in visible] == [alive.id]

    everything = db.execute(MediaFile.scoped(workspace.id, include_deleted=True)).scalars().all()
    assert {row.id for row in everything} == {alive.id, gone.id}


def test_scoped_works_on_model_without_soft_delete(db, workspace, user) -> None:
    """``PostTarget`` has no ``deleted_at``; scoped must not invent a filter."""
    instagram = _make_account(workspace, "acc-ig")
    youtube = SocialAccount(
        workspace_id=workspace.id,
        platform=SocialPlatform.YOUTUBE,
        platform_account_id="acc-yt",
        display_name="Acme on YouTube",
    )
    media = _make_media(workspace)
    db.add_all([instagram, youtube, media])
    db.flush()
    post = Post(workspace_id=workspace.id, media_file_id=media.id, created_by_id=user.id)
    db.add(post)
    db.flush()
    db.add_all(
        [
            PostTarget(
                workspace_id=workspace.id,
                post_id=post.id,
                social_account_id=instagram.id,
                platform=SocialPlatform.INSTAGRAM,
            ),
            PostTarget(
                workspace_id=workspace.id,
                post_id=post.id,
                social_account_id=youtube.id,
                platform=SocialPlatform.YOUTUBE,
            ),
        ]
    )
    db.flush()

    rows = db.execute(PostTarget.scoped(workspace.id)).scalars().all()
    assert len(rows) == 2
    assert {row.social_account_id for row in rows} == {instagram.id, youtube.id}


# --------------------------------------------------------------------------
# Seeded reference data
# --------------------------------------------------------------------------
def test_system_roles_are_seeded(db) -> None:
    roles = {role.name: role for role in db.execute(select(Role)).scalars()}
    assert set(roles) == {"owner", "admin", "editor", "viewer"}

    assert roles["owner"].is_system is True
    assert "post:publish" in roles["owner"].permissions
    assert "billing:manage" in roles["owner"].permissions

    assert "billing:manage" not in roles["admin"].permissions
    assert "workspace:delete" not in roles["admin"].permissions

    assert "member:remove" not in roles["editor"].permissions
    assert "post:publish" in roles["editor"].permissions

    # Phase 10: every role may read analytics; only roles that can publish may
    # trigger a platform refresh.
    assert "analytics:read" in roles["owner"].permissions
    assert "analytics:refresh" in roles["owner"].permissions
    assert "analytics:refresh" in roles["editor"].permissions

    assert roles["viewer"].permissions == [
        "account:read",
        "analytics:read",
        "media:read",
        "post:read",
        "schedule:read",
        "workspace:read",
    ]
    assert "analytics:refresh" not in roles["viewer"].permissions


def test_plans_are_seeded(db) -> None:
    plans = {plan.code: plan for plan in db.execute(select(Plan)).scalars()}
    assert set(plans) == {"free", "starter", "pro", "business"}

    assert plans["free"].price_cents == 0
    assert plans["starter"].price_cents == 1900
    assert plans["pro"].price_cents == 4900
    assert plans["business"].price_cents == 9900

    assert plans["free"].max_social_accounts == 2
    assert plans["business"].max_publishes_per_month is None  # unlimited
    assert plans["business"].max_social_accounts is None
    assert plans["business"].features  # non-empty JSONB array
    assert all(plan.interval.value == "month" for plan in plans.values())
