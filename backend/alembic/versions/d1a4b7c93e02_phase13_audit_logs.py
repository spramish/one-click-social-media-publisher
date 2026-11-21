"""phase 13 audit trail

Phase 13 adds the append-only security audit trail:

* ``audit_logs`` — one row per security-relevant action (a sign-in, a password
  change, a registry edit), recording *who* did *what* to *which* object, from
  *where*. The table is append-only: the application never updates or deletes a
  row, so the trail cannot be rewritten after the fact.
* ``user_id`` and ``workspace_id`` are ``ON DELETE SET NULL`` — the trail must
  outlive the account or workspace it names, because "the workspace was deleted"
  is itself a fact worth keeping.
* ``action`` is a plain string (``<area>.<object>.<verb>``), not an enum, so a
  new auditable action needs no schema migration.
* ``audit:read`` is granted to the ``owner`` and ``admin`` system roles, which
  is what authorises the audit read route.

Revision ID: d1a4b7c93e02
Revises: c3f7a2e91d08
Create Date: 2026-11-22 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1a4b7c93e02'
down_revision: Union[str, None] = 'c3f7a2e91d08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_logs',
        sa.Column('action', sa.String(length=80), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('workspace_id', sa.Uuid(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('target_type', sa.String(length=60), nullable=True),
        sa.Column('target_id', sa.Uuid(), nullable=True),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_audit_logs_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'],
            ['workspaces.id'],
            name=op.f('fk_audit_logs_workspace_id_workspaces'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs')),
    )
    # The three questions the trail is asked: "what happened in this workspace",
    # "what did this user do", and "who touched this kind of thing".
    op.create_index(
        'ix_audit_logs_workspace_created',
        'audit_logs',
        ['workspace_id', 'created_at'],
        unique=False,
    )
    op.create_index(
        'ix_audit_logs_user_created',
        'audit_logs',
        ['user_id', 'created_at'],
        unique=False,
    )
    op.create_index(
        'ix_audit_logs_action_created',
        'audit_logs',
        ['action', 'created_at'],
        unique=False,
    )

    # Grant the audit read surface to the two administrator system roles.
    op.execute(
        """
        UPDATE roles
        SET permissions = permissions || '["audit:read"]'::jsonb
        WHERE name IN ('owner', 'admin')
          AND NOT permissions @> '["audit:read"]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET permissions = permissions - 'audit:read'
        WHERE name IN ('owner', 'admin')
        """
    )

    op.drop_index('ix_audit_logs_action_created', table_name='audit_logs')
    op.drop_index('ix_audit_logs_user_created', table_name='audit_logs')
    op.drop_index('ix_audit_logs_workspace_created', table_name='audit_logs')
    op.drop_table('audit_logs')
