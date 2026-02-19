"""add purchase fields to sneaker_sale

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-25 10:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sneaker_sale") as batch_op:
        batch_op.add_column(sa.Column("purchase_price", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("purchase_currency", sa.String(length=3), nullable=True))


def downgrade():
    with op.batch_alter_table("sneaker_sale") as batch_op:
        batch_op.drop_column("purchase_currency")
        batch_op.drop_column("purchase_price")
