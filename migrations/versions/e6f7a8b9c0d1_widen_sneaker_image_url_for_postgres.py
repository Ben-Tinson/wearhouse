"""widen sneaker image_url for postgres-safe imports

Revision ID: e6f7a8b9c0d1
Revises: c1d2e3f4a5b6
Create Date: 2026-04-13 16:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6f7a8b9c0d1'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker', schema=None) as batch_op:
        batch_op.alter_column(
            'image_url',
            existing_type=sa.String(length=255),
            type_=sa.String(length=500),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table('sneaker', schema=None) as batch_op:
        batch_op.alter_column(
            'image_url',
            existing_type=sa.String(length=500),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
