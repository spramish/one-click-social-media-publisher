"""phase 12 ai model registry and failover log

Phase 12 keeps the assistant free and resilient by letting it hold several
models and choose among them:

* ``ai_models`` — the ordered list of models the router may call. Each row
  carries a ``priority`` (lower is tried first), an ``enabled`` flag, the
  provider ``kind`` and an optional ``credential_env`` naming the **environment
  variable** that holds the key. The key itself is never stored here, so a
  database dump cannot leak a credential.
* ``ai_task_attempts`` — one append-only row per model attempt. Grouped by
  ``task_id`` it is the failover chain; aggregated by ``model_name`` it is each
  model's usage and performance record; in order it is the execution trail.
* ``notifications.type`` gains the ``model_fallback`` category, so a user whose
  content was written by a backup model can be told which model stepped in.
* ``ai:manage`` is granted to the ``owner`` and ``admin`` system roles, which is
  what authorises the registry-management routes.

The ``notification_type`` values live in a ``CHECK`` constraint rather than a
PostgreSQL ``ENUM`` type (see :func:`app.db.types.enum_column`), so widening the
catalogue is a constraint swap rather than an ``ALTER TYPE``.

Revision ID: c3f7a2e91d08
Revises: b8e2f5a91c47
Create Date: 2026-11-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3f7a2e91d08'
down_revision: Union[str, None] = 'b8e2f5a91c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The notification categories before and after this migration.
_NOTIFICATION_TYPES_BEFORE = (
    "'publish_success','publish_failure','schedule_reminder','token_expiring',"
    "'subscription','system'"
)
_NOTIFICATION_TYPES_AFTER = (
    "'publish_success','publish_failure','schedule_reminder','token_expiring',"
    "'model_fallback','subscription','system'"
)


def upgrade() -> None:
    op.create_table(
        'ai_models',
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('display_name', sa.String(length=120), nullable=False),
        sa.Column(
            'kind',
            sa.Enum(
                'openai_compatible',
                'ollama',
                name='ai_model_kind',
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('base_url', sa.String(length=255), nullable=False),
        sa.Column('credential_env', sa.String(length=80), nullable=True),
        sa.Column('priority', sa.Integer(), server_default='100', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('supports', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('timeout_seconds', sa.Integer(), server_default='60', nullable=False),
        sa.Column('max_output_tokens', sa.Integer(), server_default='800', nullable=False),
        sa.Column('temperature', sa.Float(), server_default='0.8', nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'unknown',
                'healthy',
                'unhealthy',
                name='ai_model_status',
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default='unknown',
            nullable=False,
        ),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_models')),
        sa.UniqueConstraint('name', name=op.f('uq_ai_models_name')),
    )
    # The router's working set: enabled models in priority order.
    op.create_index(
        'ix_ai_models_enabled_priority',
        'ai_models',
        ['enabled', 'priority'],
        unique=False,
    )

    op.create_table(
        'ai_task_attempts',
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=True),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('model_id', sa.Uuid(), nullable=True),
        sa.Column('model_name', sa.String(length=120), nullable=False),
        sa.Column(
            'kind',
            sa.Enum(
                'caption',
                'hashtags',
                'description',
                'rewrite',
                'suggestion',
                name='ai_content_kind',
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(
                'succeeded',
                'failed',
                name='ai_task_status',
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('error_class', sa.String(length=80), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['workspace_id'],
            ['workspaces.id'],
            name=op.f('fk_ai_task_attempts_workspace_id_workspaces'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_ai_task_attempts_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['model_id'],
            ['ai_models.id'],
            name=op.f('fk_ai_task_attempts_model_id_ai_models'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_task_attempts')),
    )
    op.create_index(
        'ix_ai_task_attempts_task',
        'ai_task_attempts',
        ['task_id', 'created_at'],
        unique=False,
    )
    op.create_index(
        'ix_ai_task_attempts_model',
        'ai_task_attempts',
        ['model_name', 'created_at'],
        unique=False,
    )

    # ``notifications.type`` gains the model_fallback category. ``op.f`` marks the
    # name as already conventional, so it is dropped verbatim rather than being
    # run through the naming convention a second time.
    op.drop_constraint(
        op.f('ck_notifications_notification_type'), 'notifications', type_='check'
    )
    op.create_check_constraint(
        'notification_type',
        'notifications',
        f"type IN ({_NOTIFICATION_TYPES_AFTER})",
    )

    # Grant registry administration to the two administrator system roles.
    op.execute(
        f"""
        UPDATE roles
        SET permissions = permissions || '["ai:manage"]'::jsonb
        WHERE name IN ('owner', 'admin')
          AND NOT permissions @> '["ai:manage"]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET permissions = permissions - 'ai:manage'
        WHERE name IN ('owner', 'admin')
        """
    )

    op.drop_constraint(
        op.f('ck_notifications_notification_type'), 'notifications', type_='check'
    )
    op.create_check_constraint(
        'notification_type',
        'notifications',
        f"type IN ({_NOTIFICATION_TYPES_BEFORE})",
    )

    op.drop_index('ix_ai_task_attempts_model', table_name='ai_task_attempts')
    op.drop_index('ix_ai_task_attempts_task', table_name='ai_task_attempts')
    op.drop_table('ai_task_attempts')

    op.drop_index('ix_ai_models_enabled_priority', table_name='ai_models')
    op.drop_table('ai_models')
