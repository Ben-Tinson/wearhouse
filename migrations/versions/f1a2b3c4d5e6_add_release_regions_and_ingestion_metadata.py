"""add release regions and ingestion metadata

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-04-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("release") as batch_op:
        batch_op.add_column(sa.Column("release_slug", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("ingestion_source", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("ingestion_batch_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("ingested_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("ingested_by_user_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_release_release_slug", ["release_slug"], unique=False)
        batch_op.create_index("ix_release_ingested_by_user_id", ["ingested_by_user_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_release_ingested_by_user_id_user",
            "user",
            ["ingested_by_user_id"],
            ["id"],
        )

    op.create_table(
        "release_region",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_id", sa.Integer(), nullable=False, index=True),
        sa.Column("region", sa.String(length=10), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=False),
        sa.Column("release_time", sa.Time(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["release_id"], ["release.id"], name="fk_release_region_release_id_release"),
        sa.UniqueConstraint("release_id", "region", name="uq_release_region_release_id_region"),
    )


def downgrade():
    op.drop_table("release_region")

    with op.batch_alter_table("release") as batch_op:
        batch_op.drop_constraint("fk_release_ingested_by_user_id_user", type_="foreignkey")
        batch_op.drop_index("ix_release_ingested_by_user_id")
        batch_op.drop_index("ix_release_release_slug")
        batch_op.drop_column("ingested_by_user_id")
        batch_op.drop_column("ingested_at")
        batch_op.drop_column("ingestion_batch_id")
        batch_op.drop_column("ingestion_source")
        batch_op.drop_column("release_slug")
