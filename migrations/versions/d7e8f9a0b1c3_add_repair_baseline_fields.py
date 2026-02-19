"""add repair baseline fields

Revision ID: d7e8f9a0b1c3
Revises: c6d7e8f9a0b2
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = 'd7e8f9a0b1c3'
down_revision = 'c6d7e8f9a0b2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker_repair_event') as batch_op:
        batch_op.add_column(sa.Column('repair_area', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('baseline_delta_applied', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('sneaker_repair_event') as batch_op:
        batch_op.drop_column('baseline_delta_applied')
        batch_op.drop_column('repair_area')
