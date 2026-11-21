"""add analytics metrics

Phase 10 storage. Two append-only snapshot tables join the schema, plus the
two permission codes the dashboard is guarded by.

* ``post_metrics`` — one row per retrieval of a published target's video
  metrics (views, likes, comments, shares, reach, impressions).
* ``account_metrics`` — one row per retrieval of a connected account's
  audience numbers (followers, follows, posts).

Every metric column is **nullable on purpose**: ``NULL`` records "the platform
did not provide this metric" while ``0`` is a genuine zero. ``retrieved_at``
records when the platform answered, so a snapshot can be charted and a delayed
worker cannot misdate a measurement.

The same migration grants ``analytics:read`` to every role (a viewer may see
performance) and ``analytics:refresh`` to owner/admin/editor (only a role that
can publish may trigger a platform fetch). Grants are an additive union, so the
migration is idempotent and never clobbers a customised role.

Revision ID: f241d12e080b
Revises: 5347775d077d
Create Date: 2026-11-18 19:23:52.671500
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f241d12e080b'
down_revision: Union[str, None] = '5347775d077d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Permission grants -----------------------------------------------------
# ``analytics:read`` is granted to every role, including the read-only viewer.
# ``analytics:refresh`` is a write-shaped action (it talks to the platforms), so
# it stays with the roles that may publish.
ANALYTICS_PERMISSION_GRANTS: dict[str, list[str]] = {
    "owner": ["analytics:read", "analytics:refresh"],
    "admin": ["analytics:read", "analytics:refresh"],
    "editor": ["analytics:read", "analytics:refresh"],
    "viewer": ["analytics:read"],
}


def _merge_permissions(bind, *, grant: bool) -> None:
    """Union (or remove) the analytics codes on each seeded role's permission list."""
    for name, codes in ANALYTICS_PERMISSION_GRANTS.items():
        current = bind.execute(
            sa.text("SELECT permissions FROM roles WHERE name = :name"),
            {"name": name},
        ).scalar()
        if current is None:
            continue
        if not isinstance(current, list):
            current = json.loads(current)
        if grant:
            updated = sorted(set(current) | set(codes))
        else:
            updated = sorted(set(current) - set(codes))
        bind.execute(
            sa.text("UPDATE roles SET permissions = CAST(:permissions AS jsonb) WHERE name = :name"),
            {"permissions": json.dumps(updated), "name": name},
        )


def upgrade() -> None:
    op.create_table('account_metrics',
    sa.Column('social_account_id', sa.Uuid(), nullable=False),
    sa.Column('platform', sa.Enum('instagram', 'facebook', 'youtube', 'tiktok', name='social_platform', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('followers', sa.Integer(), nullable=True),
    sa.Column('follows', sa.Integer(), nullable=True),
    sa.Column('posts_count', sa.Integer(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('followers >= 0', name=op.f('ck_account_metrics_followers_non_negative')),
    sa.CheckConstraint('follows >= 0', name=op.f('ck_account_metrics_follows_non_negative')),
    sa.CheckConstraint('posts_count >= 0', name=op.f('ck_account_metrics_posts_count_non_negative')),
    sa.ForeignKeyConstraint(['social_account_id'], ['social_accounts.id'], name=op.f('fk_account_metrics_social_account_id_social_accounts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_account_metrics_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_account_metrics'))
    )
    op.create_index('ix_account_metrics_account_retrieved', 'account_metrics', ['social_account_id', 'retrieved_at'], unique=False)
    op.create_index(op.f('ix_account_metrics_social_account_id'), 'account_metrics', ['social_account_id'], unique=False)
    op.create_index(op.f('ix_account_metrics_workspace_id'), 'account_metrics', ['workspace_id'], unique=False)
    op.create_index('ix_account_metrics_workspace_retrieved', 'account_metrics', ['workspace_id', 'retrieved_at'], unique=False)
    op.create_table('post_metrics',
    sa.Column('post_target_id', sa.Uuid(), nullable=False),
    sa.Column('platform', sa.Enum('instagram', 'facebook', 'youtube', 'tiktok', name='social_platform', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('views', sa.Integer(), nullable=True),
    sa.Column('likes', sa.Integer(), nullable=True),
    sa.Column('comments', sa.Integer(), nullable=True),
    sa.Column('shares', sa.Integer(), nullable=True),
    sa.Column('reach', sa.Integer(), nullable=True),
    sa.Column('impressions', sa.Integer(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('comments >= 0', name=op.f('ck_post_metrics_comments_non_negative')),
    sa.CheckConstraint('impressions >= 0', name=op.f('ck_post_metrics_impressions_non_negative')),
    sa.CheckConstraint('likes >= 0', name=op.f('ck_post_metrics_likes_non_negative')),
    sa.CheckConstraint('reach >= 0', name=op.f('ck_post_metrics_reach_non_negative')),
    sa.CheckConstraint('shares >= 0', name=op.f('ck_post_metrics_shares_non_negative')),
    sa.CheckConstraint('views >= 0', name=op.f('ck_post_metrics_views_non_negative')),
    sa.ForeignKeyConstraint(['post_target_id'], ['post_targets.id'], name=op.f('fk_post_metrics_post_target_id_post_targets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_post_metrics_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_post_metrics'))
    )
    op.create_index(op.f('ix_post_metrics_post_target_id'), 'post_metrics', ['post_target_id'], unique=False)
    op.create_index('ix_post_metrics_target_retrieved', 'post_metrics', ['post_target_id', 'retrieved_at'], unique=False)
    op.create_index(op.f('ix_post_metrics_workspace_id'), 'post_metrics', ['workspace_id'], unique=False)
    op.create_index('ix_post_metrics_workspace_retrieved', 'post_metrics', ['workspace_id', 'retrieved_at'], unique=False)

    _merge_permissions(op.get_bind(), grant=True)


def downgrade() -> None:
    _merge_permissions(op.get_bind(), grant=False)

    op.drop_index('ix_post_metrics_workspace_retrieved', table_name='post_metrics')
    op.drop_index(op.f('ix_post_metrics_workspace_id'), table_name='post_metrics')
    op.drop_index('ix_post_metrics_target_retrieved', table_name='post_metrics')
    op.drop_index(op.f('ix_post_metrics_post_target_id'), table_name='post_metrics')
    op.drop_table('post_metrics')
    op.drop_index('ix_account_metrics_workspace_retrieved', table_name='account_metrics')
    op.drop_index(op.f('ix_account_metrics_workspace_id'), table_name='account_metrics')
    op.drop_index(op.f('ix_account_metrics_social_account_id'), table_name='account_metrics')
    op.drop_index('ix_account_metrics_account_retrieved', table_name='account_metrics')
    op.drop_table('account_metrics')
