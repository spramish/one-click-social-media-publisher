"""seed system roles and plans

Seeds the global role catalogue (owner/admin/editor/viewer) and the
starter subscription plans. Both sets are reference data that the
application assumes exists, so they ship with the schema rather than being
created lazily at runtime.

Idempotent: every insert uses ``ON CONFLICT DO NOTHING``, so re-running the
migration (or running it against a database that was seeded manually) is safe.

Revision ID: 70ed893bdb70
Revises: 0d24e6e89e59
Create Date: 2026-11-18 01:25:13.043940
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70ed893bdb70'
down_revision: Union[str, None] = '0d24e6e89e59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Permission catalogue -------------------------------------------------
# Codes are ``<resource>:<action>``. Roles grant a subset of these.

WORKSPACE_PERMISSIONS = ["workspace:read", "workspace:update", "workspace:delete"]
MEMBER_PERMISSIONS = ["member:invite", "member:update", "member:remove"]
ACCOUNT_PERMISSIONS = ["account:read", "account:connect", "account:disconnect"]
MEDIA_PERMISSIONS = ["media:read", "media:upload", "media:delete"]
POST_PERMISSIONS = [
    "post:read",
    "post:create",
    "post:update",
    "post:delete",
    "post:publish",
]
SCHEDULE_PERMISSIONS = ["schedule:read", "schedule:create", "schedule:cancel"]
AI_PERMISSIONS = ["ai:generate"]
BILLING_PERMISSIONS = ["billing:read", "billing:manage"]

ALL_PERMISSIONS = sorted(
    WORKSPACE_PERMISSIONS
    + MEMBER_PERMISSIONS
    + ACCOUNT_PERMISSIONS
    + MEDIA_PERMISSIONS
    + POST_PERMISSIONS
    + SCHEDULE_PERMISSIONS
    + AI_PERMISSIONS
    + BILLING_PERMISSIONS
)

ADMIN_PERMISSIONS = sorted(set(ALL_PERMISSIONS) - {"workspace:delete", "billing:manage"})

EDITOR_PERMISSIONS = sorted(
    ACCOUNT_PERMISSIONS
    + MEDIA_PERMISSIONS
    + POST_PERMISSIONS
    + SCHEDULE_PERMISSIONS
    + AI_PERMISSIONS
    + ["workspace:read", "member:invite"]
)

VIEWER_PERMISSIONS = sorted(
    ["workspace:read", "account:read", "media:read", "post:read", "schedule:read"]
)

# (name, display_name, description, permissions)
ROLE_SEED: list[tuple[str, str, str, list[str]]] = [
    (
        "owner",
        "Owner",
        "Full control of the workspace, including billing and deletion.",
        ALL_PERMISSIONS,
    ),
    (
        "admin",
        "Administrator",
        "Manages members, accounts and content; cannot delete the workspace or manage billing.",
        ADMIN_PERMISSIONS,
    ),
    (
        "editor",
        "Editor",
        "Creates and publishes content but cannot manage members or billing.",
        EDITOR_PERMISSIONS,
    ),
    (
        "viewer",
        "Viewer",
        "Read-only access to workspace content.",
        VIEWER_PERMISSIONS,
    ),
]

# (code, name, description, price_cents, interval, features, publishes, accounts, members)
PLAN_SEED: list[tuple[str, str, str, int, str, list[str], int | None, int | None, int | None]] = [
    (
        "free",
        "Free",
        "Try the publisher with a single connected account.",
        0,
        "month",
        ["1 workspace", "2 social accounts", "10 publishes / month"],
        10,
        2,
        1,
    ),
    (
        "starter",
        "Starter",
        "For solo creators publishing to a handful of channels.",
        1900,
        "month",
        ["3 social accounts", "100 publishes / month", "AI caption generation"],
        100,
        3,
        3,
    ),
    (
        "pro",
        "Professional",
        "For small teams running multi-platform campaigns.",
        4900,
        "month",
        [
            "15 social accounts",
            "1,000 publishes / month",
            "10 team members",
            "AI caption generation",
            "Priority support",
        ],
        1000,
        15,
        10,
    ),
    (
        "business",
        "Business",
        "Unlimited publishing for agencies and larger teams.",
        9900,
        "month",
        [
            "Unlimited social accounts",
            "Unlimited publishes",
            "50 team members",
            "AI caption generation",
            "Priority support",
            "Dedicated onboarding",
        ],
        None,
        None,
        50,
    ),
]


def upgrade() -> None:
    bind = op.get_bind()

    role_insert = sa.text(
        """
        INSERT INTO roles (name, display_name, description, permissions, is_system)
        VALUES (:name, :display_name, :description, CAST(:permissions AS jsonb), true)
        ON CONFLICT (name) DO NOTHING
        """
    )
    for name, display_name, description, permissions in ROLE_SEED:
        bind.execute(
            role_insert,
            {
                "name": name,
                "display_name": display_name,
                "description": description,
                "permissions": json.dumps(permissions),
            },
        )

    plan_insert = sa.text(
        """
        INSERT INTO plans (
            code, name, description, price_cents, currency, interval, features,
            max_publishes_per_month, max_social_accounts, max_workspace_members,
            is_active
        )
        VALUES (
            :code, :name, :description, :price_cents, 'USD', :interval,
            CAST(:features AS jsonb), :max_publishes, :max_accounts, :max_members,
            true
        )
        ON CONFLICT (code) DO NOTHING
        """
    )
    for (
        code,
        name,
        description,
        price_cents,
        interval,
        features,
        max_publishes,
        max_accounts,
        max_members,
    ) in PLAN_SEED:
        bind.execute(
            plan_insert,
            {
                "code": code,
                "name": name,
                "description": description,
                "price_cents": price_cents,
                "interval": interval,
                "features": json.dumps(features),
                "max_publishes": max_publishes,
                "max_accounts": max_accounts,
                "max_members": max_members,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM plans WHERE code = ANY(:codes)"),
        {"codes": [row[0] for row in PLAN_SEED]},
    )
    bind.execute(
        sa.text("DELETE FROM roles WHERE name = ANY(:names)"),
        {"names": [row[0] for row in ROLE_SEED]},
    )
