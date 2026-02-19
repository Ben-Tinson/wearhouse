"""add carousel images json to article_block

Revision ID: 7c8d9e0f1a2b
Revises: 1b2c3d4e5f6a
Create Date: 2026-02-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '7c8d9e0f1a2b'
down_revision = '1b2c3d4e5f6a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('article_block') as batch_op:
        batch_op.add_column(sa.Column('carousel_images_json', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('article_block') as batch_op:
        batch_op.drop_column('carousel_images_json')
