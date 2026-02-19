"""Add release_price table for region-specific MSRP.

Revision ID: 7d0f3e2a6b1c
Revises: 4f2b6d8e1c7a
Create Date: 2026-01-14 11:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "7d0f3e2a6b1c"
down_revision = "4f2b6d8e1c7a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_price",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("region", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["release_id"], ["release.id"]),
        sa.UniqueConstraint("release_id", "currency", "region", name="uq_release_price_currency_region"),
    )
    op.create_index("ix_release_price_release_id", "release_price", ["release_id"])


def downgrade():
    op.drop_index("ix_release_price_release_id", table_name="release_price")
    op.drop_table("release_price")
