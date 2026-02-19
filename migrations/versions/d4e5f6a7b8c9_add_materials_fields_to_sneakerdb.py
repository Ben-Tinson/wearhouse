"""add materials fields to sneakerdb

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-01-26 20:12:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sneaker_db", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("sneaker_db", sa.Column("primary_material", sa.String(length=100), nullable=True))
    op.add_column("sneaker_db", sa.Column("materials_json", sa.Text(), nullable=True))
    op.add_column("sneaker_db", sa.Column("materials_source", sa.String(length=50), nullable=True))
    op.add_column("sneaker_db", sa.Column("materials_confidence", sa.Float(), nullable=True))
    op.add_column("sneaker_db", sa.Column("materials_updated_at", sa.DateTime(), nullable=True))
    op.add_column("sneaker_db", sa.Column("description_last_seen", sa.DateTime(), nullable=True))
    op.add_column("sneaker_db", sa.Column("source_updated_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("sneaker_db", "source_updated_at")
    op.drop_column("sneaker_db", "description_last_seen")
    op.drop_column("sneaker_db", "materials_updated_at")
    op.drop_column("sneaker_db", "materials_confidence")
    op.drop_column("sneaker_db", "materials_source")
    op.drop_column("sneaker_db", "materials_json")
    op.drop_column("sneaker_db", "primary_material")
    op.drop_column("sneaker_db", "description")
