"""add release market stats

Revision ID: b1c2d3e4f5g6
Revises: aa12bb34cc56
Create Date: 2026-04-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5g6"
down_revision = "aa12bb34cc56"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_market_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_id", sa.Integer(), sa.ForeignKey("release.id"), nullable=False, index=True, unique=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("volatility", sa.Float(), nullable=True),
        sa.Column("price_range_low", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_range_high", sa.Numeric(10, 2), nullable=True),
        sa.Column("sales_price_range_low", sa.Numeric(10, 2), nullable=True),
        sa.Column("sales_price_range_high", sa.Numeric(10, 2), nullable=True),
        sa.Column("sales_volume", sa.Integer(), nullable=True),
        sa.Column("gmv", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("release_market_stats")
