"""add article schema fields and site schema table

Revision ID: 3a7c1e8f9b2d
Revises: 9c2f1c4e5a7b
Create Date: 2026-02-10 18:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '3a7c1e8f9b2d'
down_revision = '9c2f1c4e5a7b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('article') as batch_op:
        batch_op.add_column(sa.Column('product_schema_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('faq_schema_json', sa.Text(), nullable=True))

    op.create_table(
        'site_schema',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('schema_type', sa.String(length=50), nullable=False),
        sa.Column('json_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_site_schema_schema_type', 'site_schema', ['schema_type'], unique=True)


def downgrade():
    op.drop_index('ix_site_schema_schema_type', table_name='site_schema')
    op.drop_table('site_schema')

    with op.batch_alter_table('article') as batch_op:
        batch_op.drop_column('faq_schema_json')
        batch_op.drop_column('product_schema_json')
