"""add sneaker wears table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-01-25 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sneaker_wear",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sneaker_id", sa.Integer(), sa.ForeignKey("sneaker.id"), nullable=False, index=True),
        sa.Column("worn_at", sa.Date(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("sneaker_wear")
