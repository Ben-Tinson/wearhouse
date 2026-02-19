"""Add sku to Sneaker model

Revision ID: c2f4d6f4b2a1
Revises: b7a1b9b0c6d2
Create Date: 2025-08-01 12:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2f4d6f4b2a1'
down_revision = 'b7a1b9b0c6d2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sku', sa.String(length=50), nullable=True))
        batch_op.create_index(batch_op.f('ix_sneaker_sku'), ['sku'], unique=False)


def downgrade():
    with op.batch_alter_table('sneaker', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sneaker_sku'))
        batch_op.drop_column('sku')
