"""add missing server defaults for user region and api token created at

Revision ID: c1d2e3f4a5b6
Revises: b8d1e2f3a4c5
Create Date: 2026-04-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "b8d1e2f3a4c5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "preferred_region",
            existing_type=sa.String(length=3),
            nullable=False,
            server_default="UK",
        )

    with op.batch_alter_table("user_api_token") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )


def downgrade():
    with op.batch_alter_table("user_api_token") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            nullable=False,
            server_default=None,
        )

    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "preferred_region",
            existing_type=sa.String(length=3),
            nullable=False,
            server_default=None,
        )
