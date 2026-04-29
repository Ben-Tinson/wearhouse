"""Add currency to Release model and update price type

Revision ID: ff06c86943a8
Revises: 51c81389f093
Create Date: 2025-06-17 16:50:37.704523

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ff06c86943a8'
down_revision = '51c81389f093'
branch_labels = None
depends_on = None


def upgrade():
    dialect_name = op.get_bind().dialect.name

    if dialect_name == "postgresql":
        op.add_column('release', sa.Column('retail_currency', sa.String(length=10), nullable=True))
        op.alter_column(
            'release',
            'retail_price',
            existing_type=sa.VARCHAR(length=20),
            type_=sa.Numeric(precision=10, scale=2),
            existing_nullable=True,
            postgresql_using='retail_price::numeric(10,2)',
        )
        return

    with op.batch_alter_table('release', schema=None) as batch_op:
        batch_op.add_column(sa.Column('retail_currency', sa.String(length=10), nullable=True))
        batch_op.alter_column(
            'retail_price',
            existing_type=sa.VARCHAR(length=20),
            type_=sa.Numeric(precision=10, scale=2),
            existing_nullable=True,
        )


def downgrade():
    dialect_name = op.get_bind().dialect.name

    if dialect_name == "postgresql":
        op.alter_column(
            'release',
            'retail_price',
            existing_type=sa.Numeric(precision=10, scale=2),
            type_=sa.VARCHAR(length=20),
            existing_nullable=True,
            postgresql_using='retail_price::varchar(20)',
        )
        op.drop_column('release', 'retail_currency')
        return

    with op.batch_alter_table('release', schema=None) as batch_op:
        batch_op.alter_column(
            'retail_price',
            existing_type=sa.Numeric(precision=10, scale=2),
            type_=sa.VARCHAR(length=20),
            existing_nullable=True,
        )
        batch_op.drop_column('retail_currency')
