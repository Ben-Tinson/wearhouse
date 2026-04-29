"""Add release fields and affiliate offer table

Revision ID: e5c1d9f1a2b3
Revises: c2f4d6f4b2a1
Create Date: 2025-08-01 13:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5c1d9f1a2b3'
down_revision = 'c2f4d6f4b2a1'
branch_labels = None
depends_on = None


def upgrade():
    dialect_name = op.get_bind().dialect.name
    active_default = sa.true() if dialect_name == "postgresql" else sa.text('1')

    with op.batch_alter_table('release', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sku', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('model_name', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('colorway', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('source_product_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('source_slug', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('source_updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_synced_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_release_sku'), ['sku'], unique=False)
        batch_op.create_index(batch_op.f('ix_release_source_product_id'), ['source_product_id'], unique=False)
        batch_op.create_unique_constraint('uq_release_source_source_product_id', ['source', 'source_product_id'])

    op.create_table(
        'affiliate_offer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('release_id', sa.Integer(), nullable=False),
        sa.Column('retailer', sa.String(length=50), nullable=False),
        sa.Column('region', sa.String(length=10), nullable=True),
        sa.Column('base_url', sa.String(length=1024), nullable=False),
        sa.Column('affiliate_url', sa.String(length=1024), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=active_default),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['release_id'], ['release.id']),
        sa.UniqueConstraint('release_id', 'retailer', 'region', name='uq_offer_release_retailer_region'),
    )
    with op.batch_alter_table('affiliate_offer', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_affiliate_offer_release_id'), ['release_id'], unique=False)


def downgrade():
    with op.batch_alter_table('affiliate_offer', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_affiliate_offer_release_id'))
    op.drop_table('affiliate_offer')

    with op.batch_alter_table('release', schema=None) as batch_op:
        batch_op.drop_constraint('uq_release_source_source_product_id', type_='unique')
        batch_op.drop_index(batch_op.f('ix_release_source_product_id'))
        batch_op.drop_index(batch_op.f('ix_release_sku'))
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('last_synced_at')
        batch_op.drop_column('source_updated_at')
        batch_op.drop_column('source_slug')
        batch_op.drop_column('source_product_id')
        batch_op.drop_column('source')
        batch_op.drop_column('colorway')
        batch_op.drop_column('model_name')
        batch_op.drop_column('sku')
