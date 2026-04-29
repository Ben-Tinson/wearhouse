"""add release description and notes

Revision ID: aa12bb34cc56
Revises: f2a3b4c5d6e7
Create Date: 2026-04-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "aa12bb34cc56"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("release") as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("release") as batch_op:
        batch_op.drop_column("notes")
        batch_op.drop_column("description")
