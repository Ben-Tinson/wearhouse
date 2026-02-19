"""merge heads 4c7d9e2f1a6b and 6a1c2b3d4e5f

Revision ID: 9a4aecee313e
Revises: 4c7d9e2f1a6b, 6a1c2b3d4e5f
Create Date: 2026-01-22 17:50:54.235064

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9a4aecee313e'
down_revision = ('4c7d9e2f1a6b', '6a1c2b3d4e5f')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
