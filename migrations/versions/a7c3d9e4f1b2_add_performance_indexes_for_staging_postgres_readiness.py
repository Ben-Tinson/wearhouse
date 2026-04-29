"""add performance indexes for staging postgres readiness

Revision ID: a7c3d9e4f1b2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-14 12:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3d9e4f1b2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_sneaker_user_id', 'sneaker', ['user_id'], unique=False)
    op.create_index('ix_sneaker_user_rotation', 'sneaker', ['user_id', 'in_rotation'], unique=False)
    op.create_index('ix_sneaker_user_brand', 'sneaker', ['user_id', 'brand'], unique=False)
    op.create_index('ix_sneaker_wear_sneaker_worn', 'sneaker_wear', ['sneaker_id', 'worn_at'], unique=False)
    op.create_index(
        'ix_step_attr_user_sneaker_gran_algo_start',
        'step_attribution',
        ['user_id', 'sneaker_id', 'bucket_granularity', 'algorithm_version', 'bucket_start'],
        unique=False,
    )
    op.create_index(
        'ix_damage_event_user_sneaker_active',
        'sneaker_damage_event',
        ['user_id', 'sneaker_id', 'is_active'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_damage_event_user_sneaker_active', table_name='sneaker_damage_event')
    op.drop_index('ix_step_attr_user_sneaker_gran_algo_start', table_name='step_attribution')
    op.drop_index('ix_sneaker_wear_sneaker_worn', table_name='sneaker_wear')
    op.drop_index('ix_sneaker_user_brand', table_name='sneaker')
    op.drop_index('ix_sneaker_user_rotation', table_name='sneaker')
    op.drop_index('ix_sneaker_user_id', table_name='sneaker')
