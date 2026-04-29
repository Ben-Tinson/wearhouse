"""allow release size bid ask and bid rows

Revision ID: b8d1e2f3a4c5
Revises: a7c9d1e2f3b4
Create Date: 2026-04-12 00:00:00.000000
"""

from alembic import op


revision = "b8d1e2f3a4c5"
down_revision = "a7c9d1e2f3b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("release_size_bid") as batch_op:
        batch_op.drop_constraint("uq_release_size_bid", type_="unique")
        batch_op.create_unique_constraint(
            "uq_release_size_bid",
            ["release_id", "size_label", "size_type", "price_type"],
        )


def downgrade():
    with op.batch_alter_table("release_size_bid") as batch_op:
        batch_op.drop_constraint("uq_release_size_bid", type_="unique")
        batch_op.create_unique_constraint(
            "uq_release_size_bid",
            ["release_id", "size_label", "size_type"],
        )
