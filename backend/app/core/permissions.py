"""Permission codes used by route guards.

The full catalogue (and the per-role grants) is seeded by the Alembic
migration ``70ed893bdb70_seed_system_roles_and_plans`` and stored in
``roles.permissions``. Only the codes referenced by the current API surface are
declared here, so a typo in a guard fails loudly at import time instead of
silently never matching a role.

Codes follow ``<resource>:<action>``.
"""

from __future__ import annotations

WORKSPACE_READ = "workspace:read"
WORKSPACE_UPDATE = "workspace:update"
WORKSPACE_DELETE = "workspace:delete"

MEMBER_INVITE = "member:invite"
MEMBER_UPDATE = "member:update"
MEMBER_REMOVE = "member:remove"

ACCOUNT_READ = "account:read"
ACCOUNT_CONNECT = "account:connect"
ACCOUNT_DISCONNECT = "account:disconnect"

MEDIA_READ = "media:read"
MEDIA_UPLOAD = "media:upload"
MEDIA_DELETE = "media:delete"

POST_READ = "post:read"
POST_CREATE = "post:create"
POST_UPDATE = "post:update"
POST_DELETE = "post:delete"
POST_PUBLISH = "post:publish"

SCHEDULE_READ = "schedule:read"
SCHEDULE_CREATE = "schedule:create"
SCHEDULE_CANCEL = "schedule:cancel"

AI_GENERATE = "ai:generate"
#: Configure the AI model registry: which models exist, their priority order and
#: their health. Held by the owner/admin system roles.
AI_MANAGE = "ai:manage"

ANALYTICS_READ = "analytics:read"
ANALYTICS_REFRESH = "analytics:refresh"

#: Read the workspace's audit trail. Held by the owner/admin system roles — an
#: audit log is an administrator's tool, not something every member sees.
AUDIT_READ = "audit:read"

__all__ = [
    "ACCOUNT_CONNECT",
    "ACCOUNT_DISCONNECT",
    "ACCOUNT_READ",
    "AI_GENERATE",
    "AI_MANAGE",
    "ANALYTICS_READ",
    "ANALYTICS_REFRESH",
    "AUDIT_READ",
    "MEDIA_DELETE",
    "MEDIA_READ",
    "MEDIA_UPLOAD",
    "MEMBER_INVITE",
    "MEMBER_REMOVE",
    "MEMBER_UPDATE",
    "POST_CREATE",
    "POST_DELETE",
    "POST_PUBLISH",
    "POST_READ",
    "POST_UPDATE",
    "SCHEDULE_CANCEL",
    "SCHEDULE_CREATE",
    "SCHEDULE_READ",
    "WORKSPACE_DELETE",
    "WORKSPACE_READ",
    "WORKSPACE_UPDATE",
]
