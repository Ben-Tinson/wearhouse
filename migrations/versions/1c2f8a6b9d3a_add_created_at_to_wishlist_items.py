"""add created_at to wishlist_items

Revision ID: 1c2f8a6b9d3a
Revises: e5c1d9f1a2b3
Create Date: 2026-01-20 16:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1c2f8a6b9d3a'
down_revision = 'e5c1d9f1a2b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('wishlist_items') as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))

    op.execute("UPDATE wishlist_items SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

    with op.batch_alter_table('wishlist_items') as batch_op:
        batch_op.alter_column('created_at', nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'))


def downgrade():
    op.drop_column('wishlist_items', 'created_at')
