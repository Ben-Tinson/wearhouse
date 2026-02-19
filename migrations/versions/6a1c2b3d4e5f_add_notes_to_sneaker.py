"""Add notes to Sneaker model.

Revision ID: 6a1c2b3d4e5f
Revises: 2f8b7c1d4e5a
Create Date: 2026-01-22 17:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "6a1c2b3d4e5f"
down_revision = "2f8b7c1d4e5a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sneaker", sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("sneaker", "notes")
