"""add size_type to release_size_bid

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-01-24 22:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8c9d0e1f2a3b"
down_revision = "7b8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("release_size_bid") as batch_op:
        batch_op.add_column(sa.Column("size_type", sa.String(length=20), nullable=True))
        batch_op.drop_constraint("uq_release_size_bid", type_="unique")
        batch_op.create_unique_constraint(
            "uq_release_size_bid",
            ["release_id", "size_label", "size_type"],
        )
    op.execute("UPDATE release_size_bid SET size_type = 'US' WHERE size_type IS NULL")


def downgrade():
    with op.batch_alter_table("release_size_bid") as batch_op:
        batch_op.drop_constraint("uq_release_size_bid", type_="unique")
        batch_op.create_unique_constraint(
            "uq_release_size_bid",
            ["release_id", "size_label"],
        )
        batch_op.drop_column("size_type")
