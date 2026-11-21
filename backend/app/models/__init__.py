"""ORM model package.

Importing this package registers **every** model on ``Base.metadata`` so that
Alembic autogenerate and ``Base.metadata.create_all()`` see the full schema.
Anything importing a model should import it from here to avoid partial
metadata.
"""

from app.models.ai_content import AIGeneratedContent
from app.models.ai_model import AIModel, AITaskAttempt
from app.models.analytics import AccountMetric, PostMetric
from app.models.audit import AuditLog
from app.models.auth import AuthSession, VerificationToken
from app.models.billing import Plan, Subscription
from app.models.media import MediaFile
from app.models.notification import Notification, NotificationPreference
from app.models.post import Post, PostTarget
from app.models.publishing import PublishingJob, PublishingLog
from app.models.scheduling import ScheduledPost
from app.models.social_account import OAuthAuthorizationState, OAuthCredential, SocialAccount
from app.models.user import User
from app.models.workspace import Role, Workspace, WorkspaceMember

__all__ = [
    "AIGeneratedContent",
    "AIModel",
    "AITaskAttempt",
    "AccountMetric",
    "AuditLog",
    "AuthSession",
    "MediaFile",
    "Notification",
    "NotificationPreference",
    "OAuthAuthorizationState",
    "OAuthCredential",
    "Plan",
    "Post",
    "PostMetric",
    "PostTarget",
    "PublishingJob",
    "PublishingLog",
    "Role",
    "ScheduledPost",
    "SocialAccount",
    "Subscription",
    "User",
    "VerificationToken",
    "Workspace",
    "WorkspaceMember",
]
