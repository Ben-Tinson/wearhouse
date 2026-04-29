"""add release heat fields

Revision ID: e9f0a1b2c3d4
Revises: d7e8f9a0b1c3
Create Date: 2026-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e9f0a1b2c3d4"
down_revision = "d7e8f9a0b1c3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("release") as batch_op:
        batch_op.add_column(sa.Column("heat_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("heat_confidence", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("heat_premium_ratio", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("heat_basis", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("heat_updated_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("release") as batch_op:
        batch_op.drop_column("heat_updated_at")
        batch_op.drop_column("heat_basis")
        batch_op.drop_column("heat_premium_ratio")
        batch_op.drop_column("heat_confidence")
        batch_op.drop_column("heat_score")
