"""add health breakdown columns

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5c6d7e8f9a0'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker_health_snapshot') as batch_op:
        batch_op.add_column(sa.Column('wear_penalty', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('cosmetic_penalty', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('structural_penalty', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('hygiene_penalty', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('steps_total_used', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('confidence_score', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('confidence_label', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('sneaker_health_snapshot') as batch_op:
        batch_op.drop_column('confidence_label')
        batch_op.drop_column('confidence_score')
        batch_op.drop_column('steps_total_used')
        batch_op.drop_column('hygiene_penalty')
        batch_op.drop_column('structural_penalty')
        batch_op.drop_column('cosmetic_penalty')
        batch_op.drop_column('wear_penalty')
