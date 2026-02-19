"""add starting health

Revision ID: c6d7e8f9a0b2
Revises: b5c6d7e8f9a0
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = 'c6d7e8f9a0b2'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker') as batch_op:
        batch_op.add_column(sa.Column('starting_health', sa.Float(), nullable=False, server_default='100'))

    op.execute(
        """
        UPDATE sneaker
        SET starting_health = CASE
            WHEN condition = 'Deadstock' THEN 100
            WHEN condition = 'Near New' THEN 98
            WHEN condition = 'Nearly New' THEN 98
            WHEN condition = 'Lightly Worn' THEN 95
            WHEN condition = 'Heavily Worn' THEN 85
            WHEN condition = 'Beater' THEN 70
            ELSE 100
        END
        """
    )


def downgrade():
    with op.batch_alter_table('sneaker') as batch_op:
        batch_op.drop_column('starting_health')
