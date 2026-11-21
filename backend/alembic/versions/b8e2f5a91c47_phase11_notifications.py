"""phase 11 notifications

Phase 11 adds durable bookkeeping for notification delivery, plus the per-user
email preferences the delivery honours.

* ``notifications.emailed_at`` — set once the background sweep has considered a
  notice for email, so the same notice is never re-sent on the next tick.
* ``notification_preferences`` — one row per user; four opt-out flags governing
  *email* delivery only. The in-app notification is always recorded.
* ``scheduled_posts.reminder_sent_at`` — the "publishes soon" reminder is
  recorded once per entry.
* ``social_accounts.token_warning_sent_at`` — likewise for an access-expiry
  warning.

The existing ``notification_type`` enum already carries every category Phase 11
needs (``publish_success``, ``publish_failure``, ``schedule_reminder``,
``token_expiring``, ``subscription``, ``system``), so no enum change is made.

Revision ID: b8e2f5a91c47
Revises: f241d12e080b
Create Date: 2026-11-21 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8e2f5a91c47'
down_revision: Union[str, None] = 'f241d12e080b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'notifications',
        sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True),
    )
    # The email sweep's work queue is "rows not yet considered", so a partial
    # index keeps it tiny no matter how large the table grows.
    op.create_index(
        'ix_notifications_pending_email',
        'notifications',
        ['created_at'],
        unique=False,
        postgresql_where=sa.text('emailed_at IS NULL'),
    )

    op.add_column(
        'scheduled_posts',
        sa.Column('reminder_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'social_accounts',
        sa.Column('token_warning_sent_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'notification_preferences',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('email_publish_success', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('email_publish_failure', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('email_schedule_reminder', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('email_token_expiring', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_notification_preferences_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_preferences')),
        sa.UniqueConstraint('user_id', name=op.f('uq_notification_preferences_user_id')),
    )


def downgrade() -> None:
    op.drop_table('notification_preferences')

    op.drop_column('social_accounts', 'token_warning_sent_at')
    op.drop_column('scheduled_posts', 'reminder_sent_at')

    op.drop_index('ix_notifications_pending_email', table_name='notifications')
    op.drop_column('notifications', 'emailed_at')
