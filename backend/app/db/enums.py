"""Domain enumerations shared by the ORM models.

Each enumeration maps to a ``VARCHAR`` column guarded by a ``CHECK`` constraint
(see :func:`app.db.types.enum_column`). Storing the enum *values* rather than
the Python *names* keeps the database readable and stable if the Python
identifiers are ever renamed.
"""

from __future__ import annotations

import enum


class WorkspaceMemberStatus(str, enum.Enum):
    """Lifecycle of a user's membership inside a workspace."""

    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SocialPlatform(str, enum.Enum):
    """Publishing destinations supported by the platform."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"


class SocialAccountStatus(str, enum.Enum):
    """Health of a connected social account's OAuth grant."""

    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class StorageBackend(str, enum.Enum):
    """Where a media file physically lives."""

    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"


class MediaStatus(str, enum.Enum):
    """Processing state of an uploaded video."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class PostStatus(str, enum.Enum):
    """Aggregate status of a post across all of its platform targets."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PARTIALLY_PUBLISHED = "partially_published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PostTargetStatus(str, enum.Enum):
    """Per-platform status. One failure never cancels the other targets."""

    PENDING = "pending"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class JobStatus(str, enum.Enum):
    """Execution state of a single Celery publishing job."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class ScheduleStatus(str, enum.Enum):
    """Lifecycle of a scheduled (future-dated) post."""

    PENDING = "pending"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"
    MISSED = "missed"


class LogLevel(str, enum.Enum):
    """Severity of a publishing log entry."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AIContentKind(str, enum.Enum):
    """Type of content produced by the AI assistant."""

    CAPTION = "caption"
    HASHTAGS = "hashtags"
    DESCRIPTION = "description"
    REWRITE = "rewrite"
    SUGGESTION = "suggestion"


class NotificationType(str, enum.Enum):
    """Category of an in-app notification."""

    PUBLISH_SUCCESS = "publish_success"
    PUBLISH_FAILURE = "publish_failure"
    SCHEDULE_REMINDER = "schedule_reminder"
    TOKEN_EXPIRING = "token_expiring"
    #: The AI router fell back from the preferred model to a backup, so the
    #: user knows which model actually produced their content.
    MODEL_FALLBACK = "model_fallback"
    SUBSCRIPTION = "subscription"
    SYSTEM = "system"


class AIModelKind(str, enum.Enum):
    """Transport family used to reach an AI model."""

    #: Any gateway speaking the OpenAI chat-completions protocol (OpenAI,
    #: Azure OpenAI, Groq, Together, OpenRouter, a local proxy).
    OPENAI_COMPATIBLE = "openai_compatible"
    #: A locally hosted model reached through Ollama's HTTP API — no key, no bill.
    OLLAMA = "ollama"


class AIModelStatus(str, enum.Enum):
    """Last observed availability of a configured model."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class AITaskStatus(str, enum.Enum):
    """Outcome of a single model attempt made while routing one AI task."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BillingInterval(str, enum.Enum):
    """How often a subscription plan is billed."""

    MONTH = "month"
    YEAR = "year"


class SubscriptionStatus(str, enum.Enum):
    """Billing state of a workspace subscription."""

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VerificationPurpose(str, enum.Enum):
    """Why a one-time :class:`~app.models.auth.VerificationToken` was issued.

    Both purposes share one table because they have identical semantics — a
    single-use, expiring, hashed secret handed to the user's inbox.
    """

    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"
