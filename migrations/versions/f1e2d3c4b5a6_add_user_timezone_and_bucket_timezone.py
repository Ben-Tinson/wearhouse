"""Add user timezone and enforce step bucket timezone.

Revision ID: f1e2d3c4b5a6
Revises: e1f2a3b4c5d6
Create Date: 2026-01-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f1e2d3c4b5a6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    user_table = sa.table(
        "user",
        sa.column("timezone", sa.String(length=64)),
    )
    step_bucket_table = sa.table(
        "step_bucket",
        sa.column("timezone", sa.String(length=64)),
    )

    op.add_column(
        "user",
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/London"),
    )
    op.execute(
        user_table.update()
        .where(user_table.c.timezone.is_(None))
        .values(timezone="Europe/London")
    )

    op.execute(
        step_bucket_table.update()
        .where(step_bucket_table.c.timezone.is_(None))
        .values(timezone="Europe/London")
    )
    with op.batch_alter_table("step_bucket") as batch:
        batch.alter_column(
            "timezone",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="Europe/London",
        )


def downgrade():
    with op.batch_alter_table("step_bucket") as batch:
        batch.alter_column(
            "timezone",
            existing_type=sa.String(length=64),
            nullable=True,
            server_default=None,
        )

    op.drop_column("user", "timezone")
