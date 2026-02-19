"""Add KicksDB fields to SneakerDB

Revision ID: b7a1b9b0c6d2
Revises: de1c01809750
Create Date: 2025-08-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7a1b9b0c6d2'
down_revision = 'de1c01809750'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker_db', schema=None) as batch_op:
        batch_op.add_column(sa.Column('model_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('retail_currency', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('stockx_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('stockx_slug', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('goat_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('goat_slug', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('current_lowest_ask_stockx', sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column('current_lowest_ask_goat', sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column('last_synced_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        batch_op.alter_column('sku', existing_type=sa.String(length=50), nullable=False)
        batch_op.create_unique_constraint('uq_sneaker_db_sku', ['sku'])
        batch_op.create_index(batch_op.f('ix_sneaker_db_model_name'), ['model_name'], unique=False)


def downgrade():
    with op.batch_alter_table('sneaker_db', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sneaker_db_model_name'))
        batch_op.drop_constraint('uq_sneaker_db_sku', type_='unique')
        batch_op.alter_column('sku', existing_type=sa.String(length=50), nullable=True)
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('last_synced_at')
        batch_op.drop_column('current_lowest_ask_goat')
        batch_op.drop_column('current_lowest_ask_stockx')
        batch_op.drop_column('goat_slug')
        batch_op.drop_column('goat_id')
        batch_op.drop_column('stockx_slug')
        batch_op.drop_column('stockx_id')
        batch_op.drop_column('retail_currency')
        batch_op.drop_column('model_name')
