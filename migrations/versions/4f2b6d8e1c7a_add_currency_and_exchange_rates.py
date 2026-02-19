"""Add preferred currency, price currency, and exchange rates.

Revision ID: 4f2b6d8e1c7a
Revises: 9c2d1f8b7a3c
Create Date: 2026-01-14 11:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "4f2b6d8e1c7a"
down_revision = "9c2d1f8b7a3c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("preferred_currency", sa.String(length=3), nullable=False, server_default="GBP"),
    )
    op.add_column(
        "sneaker",
        sa.Column("price_paid_currency", sa.String(length=3), nullable=True, server_default="GBP"),
    )

    op.execute("UPDATE user SET preferred_currency = 'GBP' WHERE preferred_currency IS NULL")
    op.execute("UPDATE sneaker SET purchase_currency = 'GBP' WHERE purchase_currency IS NULL")
    op.execute(
        "UPDATE sneaker SET price_paid_currency = COALESCE(purchase_currency, 'GBP') "
        "WHERE price_paid_currency IS NULL"
    )
    op.execute("UPDATE release SET retail_currency = 'GBP' WHERE retail_currency IS NULL")
    op.execute(
        "UPDATE release SET retail_currency = 'USD' "
        "WHERE retail_currency = 'GBP' AND retail_price IS NOT NULL AND source = 'kicksdb_stockx'"
    )

    op.create_table(
        "exchange_rate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 6), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("base_currency", "quote_currency", name="uq_exchange_rate_pair"),
    )
    op.create_index("ix_exchange_rate_base_currency", "exchange_rate", ["base_currency"])
    op.create_index("ix_exchange_rate_quote_currency", "exchange_rate", ["quote_currency"])


def downgrade():
    op.drop_index("ix_exchange_rate_quote_currency", table_name="exchange_rate")
    op.drop_index("ix_exchange_rate_base_currency", table_name="exchange_rate")
    op.drop_table("exchange_rate")
    op.drop_column("sneaker", "price_paid_currency")
    op.drop_column("user", "preferred_currency")
