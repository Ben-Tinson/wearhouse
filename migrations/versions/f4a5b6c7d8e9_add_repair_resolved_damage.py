"""add repair resolved damage

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4a5b6c7d8e9'
down_revision = 'e3f4a5b6c7d8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sneaker_repair_resolved_damage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('repair_event_id', sa.Integer(), sa.ForeignKey('sneaker_repair_event.id'), nullable=False, index=True),
        sa.Column('damage_event_id', sa.Integer(), sa.ForeignKey('sneaker_damage_event.id'), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('repair_event_id', 'damage_event_id', name='uq_repair_resolved_damage'),
    )


def downgrade():
    op.drop_table('sneaker_repair_resolved_damage')
