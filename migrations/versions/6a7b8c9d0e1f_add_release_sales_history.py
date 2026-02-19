"""Add release sales history table and fetch timestamp.

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
Create Date: 2026-01-23 16:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a7b8c9d0e1f'
down_revision = '5f6a7b8c9d0e'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('release', sa.Column('sales_last_fetched_at', sa.DateTime(), nullable=True))
    op.create_table(
        'release_sales_monthly',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('release_id', sa.Integer(), sa.ForeignKey('release.id'), nullable=False),
        sa.Column('month_start', sa.Date(), nullable=False),
        sa.Column('avg_price', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('fetched_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('release_id', 'month_start', name='uq_release_monthly_sales'),
    )
    op.create_index('ix_release_sales_monthly_release_id', 'release_sales_monthly', ['release_id'])
    op.create_index('ix_release_sales_monthly_month_start', 'release_sales_monthly', ['month_start'])


def downgrade():
    op.drop_index('ix_release_sales_monthly_month_start', table_name='release_sales_monthly')
    op.drop_index('ix_release_sales_monthly_release_id', table_name='release_sales_monthly')
    op.drop_table('release_sales_monthly')
    op.drop_column('release', 'sales_last_fetched_at')
