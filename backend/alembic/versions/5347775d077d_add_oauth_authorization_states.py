"""add oauth authorization states

Phase 6 persistence. One table joins the schema:

* ``oauth_authorization_states`` — one row per in-flight OAuth authorization
  attempt. Only the **SHA-256 digest** of the ``state`` value is stored, so a
  database dump cannot be replayed as a valid ``state``; the PKCE code verifier
  is stored **encrypted** because it must stay secret from the browser. The row
  is single-use (``consumed_at``) and expires after
  ``OAUTH_STATE_TTL_SECONDS``.

Revision ID: 5347775d077d
Revises: 14ae69daee8c
Create Date: 2026-11-18 14:53:21.420737
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5347775d077d'
down_revision: Union[str, None] = '14ae69daee8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'oauth_authorization_states',
        sa.Column('state_hash', sa.String(length=64), nullable=False),
        sa.Column('platform', sa.Enum('instagram', 'facebook', 'youtube', 'tiktok', name='social_platform', native_enum=False, create_constraint=True, length=32), nullable=False),
        sa.Column('created_by_id', sa.Uuid(), nullable=True),
        sa.Column('redirect_uri', sa.String(length=500), nullable=False),
        sa.Column('code_verifier_encrypted', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_oauth_authorization_states_created_by_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_oauth_authorization_states_workspace_id_workspaces'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_authorization_states')),
        sa.UniqueConstraint('state_hash', name=op.f('uq_oauth_authorization_states_state_hash')),
    )
    op.create_index('ix_oauth_authorization_states_expires_at', 'oauth_authorization_states', ['expires_at'], unique=False)
    op.create_index(op.f('ix_oauth_authorization_states_workspace_id'), 'oauth_authorization_states', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_oauth_authorization_states_workspace_id'), table_name='oauth_authorization_states')
    op.drop_index('ix_oauth_authorization_states_expires_at', table_name='oauth_authorization_states')
    op.drop_table('oauth_authorization_states')
