"""make sneaker_sale.sneaker_id nullable

Revision ID: a1b2c3d4e5f6
Revises: 9d0e1f2a3b4c
Create Date: 2026-01-24 23:59:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9d0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sneaker_sale") as batch_op:
        batch_op.alter_column(
            "sneaker_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade():
    with op.batch_alter_table("sneaker_sale") as batch_op:
        batch_op.alter_column(
            "sneaker_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
