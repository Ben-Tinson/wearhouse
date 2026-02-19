"""Add price type to release size bids.

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
Create Date: 2026-01-23 15:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f6a7b8c9d0e'
down_revision = '4e5f6a7b8c9d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'release_size_bid',
        sa.Column('price_type', sa.String(length=10), nullable=False, server_default='bid'),
    )


def downgrade():
    op.drop_column('release_size_bid', 'price_type')
