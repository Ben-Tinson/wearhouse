"""add user preferred region

Revision ID: f2a3b4c5d6e7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    user_table = sa.table(
        "user",
        sa.column("preferred_region", sa.String(length=3)),
    )

    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("preferred_region", sa.String(length=3), nullable=True))

    op.execute(
        user_table.update()
        .where(user_table.c.preferred_region.is_(None))
        .values(preferred_region="UK")
    )

    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column("preferred_region", nullable=False)


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("preferred_region")
