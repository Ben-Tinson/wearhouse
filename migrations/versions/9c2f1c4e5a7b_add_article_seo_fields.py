"""add article seo fields

Revision ID: 9c2f1c4e5a7b
Revises: d7e8f9a0b1c2
Create Date: 2026-02-10 17:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c2f1c4e5a7b'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('article') as batch_op:
        batch_op.add_column(sa.Column('hero_image_alt', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('author_image_alt', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('meta_title', sa.String(length=70), nullable=True))
        batch_op.add_column(sa.Column('meta_description', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('canonical_url', sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column('robots', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('og_title', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('og_description', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('og_image_url', sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column('twitter_card', sa.String(length=40), nullable=True))

    with op.batch_alter_table('article_block') as batch_op:
        batch_op.add_column(sa.Column('heading_level', sa.String(length=4), nullable=True))
        batch_op.add_column(sa.Column('image_alt', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('article_block') as batch_op:
        batch_op.drop_column('image_alt')
        batch_op.drop_column('heading_level')

    with op.batch_alter_table('article') as batch_op:
        batch_op.drop_column('twitter_card')
        batch_op.drop_column('og_image_url')
        batch_op.drop_column('og_description')
        batch_op.drop_column('og_title')
        batch_op.drop_column('robots')
        batch_op.drop_column('canonical_url')
        batch_op.drop_column('meta_description')
        batch_op.drop_column('meta_title')
        batch_op.drop_column('author_image_alt')
        batch_op.drop_column('hero_image_alt')
