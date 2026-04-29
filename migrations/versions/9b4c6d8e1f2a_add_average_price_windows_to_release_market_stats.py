"""add average price windows to release market stats

Revision ID: 9b4c6d8e1f2a
Revises: b1c2d3e4f5g6
Create Date: 2026-04-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9b4c6d8e1f2a"
down_revision = "b1c2d3e4f5g6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("release_market_stats") as batch_op:
        batch_op.add_column(sa.Column("average_price_1m", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("average_price_3m", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("average_price_1y", sa.Numeric(10, 2), nullable=True))


def downgrade():
    with op.batch_alter_table("release_market_stats") as batch_op:
        batch_op.drop_column("average_price_1y")
        batch_op.drop_column("average_price_3m")
        batch_op.drop_column("average_price_1m")
