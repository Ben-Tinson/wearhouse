"""Add size bids fetched timestamp to release.

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
Create Date: 2026-01-23 15:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4e5f6a7b8c9d'
down_revision = '3d4e5f6a7b8c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('release', sa.Column('size_bids_last_fetched_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('release', 'size_bids_last_fetched_at')
