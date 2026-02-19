"""add video schema json to article

Revision ID: 1b2c3d4e5f6a
Revises: 3a7c1e8f9b2d
Create Date: 2026-02-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '1b2c3d4e5f6a'
down_revision = '3a7c1e8f9b2d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('article') as batch_op:
        batch_op.add_column(sa.Column('video_schema_json', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('article') as batch_op:
        batch_op.drop_column('video_schema_json')
