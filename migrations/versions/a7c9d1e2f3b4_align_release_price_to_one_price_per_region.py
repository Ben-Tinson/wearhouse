"""align release_price to one price per region

Revision ID: a7c9d1e2f3b4
Revises: 9b4c6d8e1f2a
Create Date: 2026-04-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c9d1e2f3b4"
down_revision = "9b4c6d8e1f2a"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy schema allowed multiple currencies per release+region. Keep the
    # newest row by id for any non-null regional duplicates before tightening
    # the uniqueness rule.
    op.execute(
        sa.text(
            """
            DELETE FROM release_price
            WHERE region IS NOT NULL
              AND id NOT IN (
                  SELECT MAX(id)
                  FROM release_price
                  WHERE region IS NOT NULL
                  GROUP BY release_id, region
              )
            """
        )
    )

    with op.batch_alter_table("release_price") as batch_op:
        batch_op.drop_constraint("uq_release_price_currency_region", type_="unique")
        batch_op.create_unique_constraint(
            "uq_release_price_region",
            ["release_id", "region"],
        )


def downgrade():
    with op.batch_alter_table("release_price") as batch_op:
        batch_op.drop_constraint("uq_release_price_region", type_="unique")
        batch_op.create_unique_constraint(
            "uq_release_price_currency_region",
            ["release_id", "currency", "region"],
        )
