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
    user_table = sa.table(
        "user",
        sa.column("preferred_currency", sa.String(length=3)),
    )
    sneaker_table = sa.table(
        "sneaker",
        sa.column("purchase_currency", sa.String(length=3)),
        sa.column("price_paid_currency", sa.String(length=3)),
    )
    release_table = sa.table(
        "release",
        sa.column("retail_currency", sa.String(length=10)),
        sa.column("retail_price", sa.Numeric(10, 2)),
        sa.column("source", sa.String(length=50)),
    )

    op.add_column(
        "user",
        sa.Column("preferred_currency", sa.String(length=3), nullable=False, server_default="GBP"),
    )
    op.add_column(
        "sneaker",
        sa.Column("price_paid_currency", sa.String(length=3), nullable=True, server_default="GBP"),
    )

    op.execute(
        user_table.update()
        .where(user_table.c.preferred_currency.is_(None))
        .values(preferred_currency="GBP")
    )
    op.execute(
        sneaker_table.update()
        .where(sneaker_table.c.purchase_currency.is_(None))
        .values(purchase_currency="GBP")
    )
    op.execute(
        sneaker_table.update()
        .where(sneaker_table.c.price_paid_currency.is_(None))
        .values(price_paid_currency=sa.func.coalesce(sneaker_table.c.purchase_currency, "GBP"))
    )
    op.execute(
        release_table.update()
        .where(release_table.c.retail_currency.is_(None))
        .values(retail_currency="GBP")
    )
    op.execute(
        release_table.update()
        .where(
            sa.and_(
                release_table.c.retail_currency == "GBP",
                release_table.c.retail_price.is_not(None),
                release_table.c.source == "kicksdb_stockx",
            )
        )
        .values(retail_currency="USD")
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
