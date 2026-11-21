"""Phase 2 unit tests — ORM model registration and column contracts.

These tests inspect ``Base.metadata`` only; they touch neither PostgreSQL nor
Redis, so they run in any environment.
"""

import uuid

import pytest
from sqlalchemy import UniqueConstraint

from app.db.base import Base
from app.db.enums import (
    AIContentKind,
    AIModelKind,
    AIModelStatus,
    AITaskStatus,
    BillingInterval,
    JobStatus,
    MediaStatus,
    NotificationType,
    PostStatus,
    PostTargetStatus,
    ScheduleStatus,
    SocialAccountStatus,
    SocialPlatform,
    StorageBackend,
    SubscriptionStatus,
    WorkspaceMemberStatus,
)
from app.models import (  # noqa: F401  — importing registers every model
    AIGeneratedContent,
    AIModel,
    AITaskAttempt,
    AccountMetric,
    AuditLog,
    AuthSession,
    MediaFile,
    Notification,
    NotificationPreference,
    OAuthAuthorizationState,
    OAuthCredential,
    Plan,
    Post,
    PostMetric,
    PostTarget,
    PublishingJob,
    PublishingLog,
    Role,
    ScheduledPost,
    SocialAccount,
    Subscription,
    User,
    VerificationToken,
    Workspace,
    WorkspaceMember,
)

EXPECTED_TABLES = {
    "account_metrics",
    "ai_generated_content",
    "ai_models",
    "ai_task_attempts",
    "audit_logs",
    "auth_sessions",
    "media_files",
    "notification_preferences",
    "notifications",
    "oauth_authorization_states",
    "oauth_credentials",
    "plans",
    "post_metrics",
    "post_targets",
    "posts",
    "publishing_jobs",
    "publishing_logs",
    "roles",
    "scheduled_posts",
    "social_accounts",
    "subscriptions",
    "users",
    "verification_tokens",
    "workspace_members",
    "workspaces",
}

MODELS = [
    User,
    Workspace,
    WorkspaceMember,
    Role,
    SocialAccount,
    OAuthCredential,
    OAuthAuthorizationState,
    MediaFile,
    Post,
    PostTarget,
    PublishingJob,
    PublishingLog,
    ScheduledPost,
    AIGeneratedContent,
    AIModel,
    AITaskAttempt,
    AuditLog,
    PostMetric,
    AccountMetric,
    Notification,
    NotificationPreference,
    Plan,
    Subscription,
    AuthSession,
    VerificationToken,
]


def test_expected_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_all_models_have_uuid_primary_key() -> None:
    for model in MODELS:
        pk = model.__table__.primary_key
        assert len(pk.columns) == 1, f"{model.__name__} must have a single PK"
        column = next(iter(pk.columns))
        assert column.name == "id"
        assert column.type.python_type is uuid.UUID


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_created_at_and_updated_at_present(model) -> None:
    columns = model.__table__.columns
    assert "created_at" in columns
    assert columns["created_at"].type.timezone is True
    # Join tables that only need an audit trail still carry created_at; every
    # model except publishing_logs/notifications also tracks updated_at.
    if model is not PublishingLog:
        assert "updated_at" in columns or "created_at" in columns


def test_tenant_scoped_models_carry_workspace_fk() -> None:
    tenant_models = [
        WorkspaceMember,
        SocialAccount,
        OAuthAuthorizationState,
        MediaFile,
        Post,
        PostTarget,
        PublishingJob,
        ScheduledPost,
        AIGeneratedContent,
        Subscription,
    ]
    for model in tenant_models:
        column = model.__table__.columns["workspace_id"]
        assert column.nullable is False, f"{model.__name__}.workspace_id must be NOT NULL"
        fks = list(column.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "workspaces.id"
        assert fks[0].ondelete == "CASCADE"


def test_workspace_membership_is_unique_per_workspace_and_user() -> None:
    constraints = {
        c.name
        for c in WorkspaceMember.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_workspace_members_workspace_id_user_id" in constraints


def test_post_target_unique_per_post_and_account() -> None:
    constraints = {
        c.name
        for c in PostTarget.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_post_targets_post_id_social_account_id" in constraints


def test_social_account_unique_per_workspace_platform_and_account_id() -> None:
    constraints = {
        c.name
        for c in SocialAccount.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_social_accounts_workspace_id_platform_platform_account_id" in constraints


def test_check_constraints_declared() -> None:
    checks = {
        c.name for c in MediaFile.__table__.constraints if c.name and c.name.startswith("ck_")
    }
    assert "ck_media_files_size_bytes_non_negative" in checks
    assert "ck_media_files_duration_non_negative" in checks

    job_checks = {
        c.name
        for c in PublishingJob.__table__.constraints
        if c.name and c.name.startswith("ck_")
    }
    assert "ck_publishing_jobs_attempt_count_non_negative" in job_checks
    assert "ck_publishing_jobs_max_attempts_positive" in job_checks


def test_soft_delete_models_expose_is_deleted() -> None:
    for model in [User, Workspace, SocialAccount, MediaFile, Post]:
        assert hasattr(model, "is_deleted"), f"{model.__name__} should support soft delete"
        assert "deleted_at" in model.__table__.columns


def test_oauth_credentials_are_one_to_one_with_social_account() -> None:
    column = OAuthCredential.__table__.columns["social_account_id"]
    assert column.nullable is False
    assert column.unique is True


def test_enum_membership_is_complete() -> None:
    """Guard against typos when values are added to the catalogue."""
    assert SocialPlatform.INSTAGRAM.value == "instagram"
    assert set(SocialPlatform) == {
        SocialPlatform.INSTAGRAM,
        SocialPlatform.FACEBOOK,
        SocialPlatform.YOUTUBE,
        SocialPlatform.TIKTOK,
    }
    assert set(MediaStatus) == {
        MediaStatus.PENDING,
        MediaStatus.PROCESSING,
        MediaStatus.READY,
        MediaStatus.FAILED,
        MediaStatus.DELETED,
    }
    assert set(PostStatus) == {
        PostStatus.DRAFT,
        PostStatus.SCHEDULED,
        PostStatus.PUBLISHING,
        PostStatus.PUBLISHED,
        PostStatus.PARTIALLY_PUBLISHED,
        PostStatus.FAILED,
        PostStatus.CANCELLED,
    }
    assert set(PostTargetStatus) == {
        PostTargetStatus.PENDING,
        PostTargetStatus.QUEUED,
        PostTargetStatus.PUBLISHING,
        PostTargetStatus.PUBLISHED,
        PostTargetStatus.FAILED,
        PostTargetStatus.CANCELLED,
        PostTargetStatus.SKIPPED,
    }
    assert JobStatus.DEAD_LETTER.value == "dead_letter"
    assert ScheduleStatus.MISSED.value == "missed"
    assert SocialAccountStatus.CONNECTED.value == "connected"
    assert StorageBackend.MINIO.value == "minio"
    assert WorkspaceMemberStatus.INVITED.value == "invited"
    assert NotificationType.PUBLISH_FAILURE.value == "publish_failure"
    assert NotificationType.MODEL_FALLBACK.value == "model_fallback"
    assert AIModelKind.OLLAMA.value == "ollama"
    assert AIModelStatus.UNKNOWN.value == "unknown"
    assert AITaskStatus.SUCCEEDED.value == "succeeded"
    assert BillingInterval.YEAR.value == "year"
    assert SubscriptionStatus.PAST_DUE.value == "past_due"
