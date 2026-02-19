"""add author fields to articles

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-01-29 11:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("article", sa.Column("author_name", sa.String(length=120), nullable=True))
    op.add_column("article", sa.Column("author_title", sa.String(length=120), nullable=True))
    op.add_column("article", sa.Column("author_bio", sa.Text(), nullable=True))
    op.add_column("article", sa.Column("author_image_url", sa.String(length=1024), nullable=True))


def downgrade():
    op.drop_column("article", "author_image_url")
    op.drop_column("article", "author_bio")
    op.drop_column("article", "author_title")
    op.drop_column("article", "author_name")
