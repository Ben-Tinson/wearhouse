"""add repair other fields

Revision ID: a4b5c6d7e8f9
Revises: f4a5b6c7d8e9
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4b5c6d7e8f9'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker_repair_event') as batch_op:
        batch_op.add_column(sa.Column('repair_type_other', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('provider_other', sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table('sneaker_repair_event') as batch_op:
        batch_op.drop_column('provider_other')
        batch_op.drop_column('repair_type_other')
