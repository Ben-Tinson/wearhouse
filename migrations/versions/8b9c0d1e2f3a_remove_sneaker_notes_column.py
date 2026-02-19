"""Remove notes column from sneaker.

Revision ID: 8b9c0d1e2f3a
Revises: 2c3d4e5f6a7b
Create Date: 2026-01-22 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b9c0d1e2f3a'
down_revision = '2c3d4e5f6a7b'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('sneaker', 'notes')


def downgrade():
    op.add_column('sneaker', sa.Column('notes', sa.Text(), nullable=True))
