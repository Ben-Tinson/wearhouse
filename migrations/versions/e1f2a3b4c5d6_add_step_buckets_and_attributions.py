"""Add step buckets and attributions.

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-01-26 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "step_bucket",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("granularity", sa.String(length=10), nullable=False),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("bucket_end", sa.DateTime(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "granularity",
            "bucket_start",
            name="uq_step_bucket_user_source_start",
        ),
    )
    op.create_index("ix_step_bucket_user_id", "step_bucket", ["user_id"])
    op.create_index("ix_step_bucket_bucket_start", "step_bucket", ["bucket_start"])

    op.create_table(
        "step_attribution",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("sneaker_id", sa.Integer(), sa.ForeignKey("sneaker.id"), nullable=False),
        sa.Column("bucket_granularity", sa.String(length=10), nullable=False),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("steps_attributed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("algorithm_version", sa.String(length=50), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "user_id",
            "sneaker_id",
            "bucket_granularity",
            "bucket_start",
            "algorithm_version",
            name="uq_step_attr_user_sneaker_bucket_algo",
        ),
    )
    op.create_index("ix_step_attribution_user_id", "step_attribution", ["user_id"])
    op.create_index("ix_step_attribution_sneaker_id", "step_attribution", ["sneaker_id"])
    op.create_index("ix_step_attribution_bucket_start", "step_attribution", ["bucket_start"])


def downgrade():
    op.drop_index("ix_step_attribution_bucket_start", table_name="step_attribution")
    op.drop_index("ix_step_attribution_sneaker_id", table_name="step_attribution")
    op.drop_index("ix_step_attribution_user_id", table_name="step_attribution")
    op.drop_table("step_attribution")

    op.drop_index("ix_step_bucket_bucket_start", table_name="step_bucket")
    op.drop_index("ix_step_bucket_user_id", table_name="step_bucket")
    op.drop_table("step_bucket")
