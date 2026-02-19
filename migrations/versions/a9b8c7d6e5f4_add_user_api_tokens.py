"""add user api tokens

Revision ID: a9b8c7d6e5f4
Revises: f1e2d3c4b5a6
Create Date: 2026-01-27 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9b8c7d6e5f4'
down_revision = 'f1e2d3c4b5a6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_api_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scopes', sa.String(length=200), nullable=False, server_default='steps:write'),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
    )
    op.create_index('ix_user_api_token_user_id', 'user_api_token', ['user_id'], unique=False)
    op.create_index('ix_user_api_token_token_hash', 'user_api_token', ['token_hash'], unique=True)
    op.create_index('ix_user_api_token_user_revoked', 'user_api_token', ['user_id', 'revoked_at'], unique=False)


def downgrade():
    op.drop_index('ix_user_api_token_user_revoked', table_name='user_api_token')
    op.drop_index('ix_user_api_token_token_hash', table_name='user_api_token')
    op.drop_index('ix_user_api_token_user_id', table_name='user_api_token')
    op.drop_table('user_api_token')
