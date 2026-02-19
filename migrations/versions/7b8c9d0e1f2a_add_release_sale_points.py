"""Add release sale points table.

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f
Create Date: 2026-01-23 16:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7b8c9d0e1f2a'
down_revision = '6a7b8c9d0e1f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'release_sale_point',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('release_id', sa.Integer(), sa.ForeignKey('release.id'), nullable=False),
        sa.Column('sale_at', sa.DateTime(), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('fetched_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('release_id', 'sale_at', name='uq_release_sale_point'),
    )
    op.create_index('ix_release_sale_point_release_id', 'release_sale_point', ['release_id'])
    op.create_index('ix_release_sale_point_sale_at', 'release_sale_point', ['sale_at'])


def downgrade():
    op.drop_index('ix_release_sale_point_sale_at', table_name='release_sale_point')
    op.drop_index('ix_release_sale_point_release_id', table_name='release_sale_point')
    op.drop_table('release_sale_point')
