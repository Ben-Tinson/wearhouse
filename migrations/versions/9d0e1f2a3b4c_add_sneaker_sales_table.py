"""add sneaker sales table

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-01-24 22:28:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d0e1f2a3b4c"
down_revision = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sneaker_sale",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sneaker_id", sa.Integer(), sa.ForeignKey("sneaker.id"), nullable=False, index=True),
        sa.Column("release_id", sa.Integer(), sa.ForeignKey("release.id"), nullable=True, index=True),
        sa.Column("size_label", sa.String(length=50), nullable=True),
        sa.Column("size_type", sa.String(length=20), nullable=True),
        sa.Column("sold_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("sold_currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("sold_at", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("sneaker_sale")
