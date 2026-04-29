"""add exposure events and sneaker exposure attribution

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-01-28 12:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade():
    false_default = sa.false() if op.get_bind().dialect.name == "postgresql" else sa.text("0")
    op.add_column("sneaker", sa.Column("last_cleaned_at", sa.DateTime(), nullable=True))

    op.create_table(
        "exposure_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date_local", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("got_wet", sa.Boolean(), nullable=False, server_default=false_default),
        sa.Column("got_dirty", sa.Boolean(), nullable=False, server_default=false_default),
        sa.Column("wet_severity", sa.Integer(), nullable=True),
        sa.Column("dirty_severity", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=140), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date_local", name="uq_exposure_event_user_date"),
    )
    op.create_index(op.f("ix_exposure_event_user_id"), "exposure_event", ["user_id"], unique=False)
    op.create_index(op.f("ix_exposure_event_date_local"), "exposure_event", ["date_local"], unique=False)

    op.create_table(
        "sneaker_exposure_attribution",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sneaker_id", sa.Integer(), nullable=False),
        sa.Column("date_local", sa.Date(), nullable=False),
        sa.Column("wet_points", sa.Float(), nullable=False, server_default="0"),
        sa.Column("dirty_points", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "sneaker_id", "date_local", name="uq_exposure_attr_user_sneaker_date"
        ),
    )
    op.create_index(
        op.f("ix_sneaker_exposure_attribution_user_id"),
        "sneaker_exposure_attribution",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sneaker_exposure_attribution_sneaker_id"),
        "sneaker_exposure_attribution",
        ["sneaker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sneaker_exposure_attribution_date_local"),
        "sneaker_exposure_attribution",
        ["date_local"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_sneaker_exposure_attribution_date_local"), table_name="sneaker_exposure_attribution")
    op.drop_index(op.f("ix_sneaker_exposure_attribution_sneaker_id"), table_name="sneaker_exposure_attribution")
    op.drop_index(op.f("ix_sneaker_exposure_attribution_user_id"), table_name="sneaker_exposure_attribution")
    op.drop_table("sneaker_exposure_attribution")

    op.drop_index(op.f("ix_exposure_event_date_local"), table_name="exposure_event")
    op.drop_index(op.f("ix_exposure_event_user_id"), table_name="exposure_event")
    op.drop_table("exposure_event")

    op.drop_column("sneaker", "last_cleaned_at")
