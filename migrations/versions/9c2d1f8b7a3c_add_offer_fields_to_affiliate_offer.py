"""Add offer metadata fields to affiliate_offer.

Revision ID: 9c2d1f8b7a3c
Revises: e5c1d9f1a2b3
Create Date: 2026-01-14 10:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9c2d1f8b7a3c"
down_revision = "e5c1d9f1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "affiliate_offer",
        sa.Column("offer_type", sa.String(length=20), nullable=False, server_default="aftermarket"),
    )
    op.add_column("affiliate_offer", sa.Column("price", sa.Numeric(10, 2), nullable=True))
    op.add_column("affiliate_offer", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column("affiliate_offer", sa.Column("status", sa.String(length=50), nullable=True))
    op.add_column(
        "affiliate_offer",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )


def downgrade():
    op.drop_column("affiliate_offer", "priority")
    op.drop_column("affiliate_offer", "status")
    op.drop_column("affiliate_offer", "currency")
    op.drop_column("affiliate_offer", "price")
    op.drop_column("affiliate_offer", "offer_type")
