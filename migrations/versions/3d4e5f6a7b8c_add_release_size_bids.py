"""Add release size bids table.

Revision ID: 3d4e5f6a7b8c
Revises: 8b9c0d1e2f3a
Create Date: 2026-01-22 19:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3d4e5f6a7b8c'
down_revision = '8b9c0d1e2f3a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'release_size_bid',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('release_id', sa.Integer(), sa.ForeignKey('release.id'), nullable=False),
        sa.Column('size_label', sa.String(length=50), nullable=False),
        sa.Column('highest_bid', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('fetched_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('release_id', 'size_label', name='uq_release_size_bid'),
    )
    op.create_index('ix_release_size_bid_release_id', 'release_size_bid', ['release_id'])


def downgrade():
    op.drop_index('ix_release_size_bid_release_id', table_name='release_size_bid')
    op.drop_table('release_size_bid')
