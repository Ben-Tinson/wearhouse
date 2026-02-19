"""add user api usage table

Revision ID: 3a6b9c2d1e4f
Revises: 2f8b7c1d4e5a
Create Date: 2026-01-20 18:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3a6b9c2d1e4f'
down_revision = '2f8b7c1d4e5a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_api_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.UniqueConstraint('user_id', 'action', 'usage_date', name='uq_user_api_usage'),
    )
    op.create_index('ix_user_api_usage_user_id', 'user_api_usage', ['user_id'])
    op.create_index('ix_user_api_usage_action', 'user_api_usage', ['action'])
    op.create_index('ix_user_api_usage_usage_date', 'user_api_usage', ['usage_date'])


def downgrade():
    op.drop_index('ix_user_api_usage_usage_date', table_name='user_api_usage')
    op.drop_index('ix_user_api_usage_action', table_name='user_api_usage')
    op.drop_index('ix_user_api_usage_user_id', table_name='user_api_usage')
    op.drop_table('user_api_usage')
