"""add damage repair expense

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3f4a5b6c7d8'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker') as batch_op:
        batch_op.add_column(sa.Column('persistent_structural_damage_points', sa.Float(), nullable=False, server_default='0'))

    op.create_table(
        'sneaker_damage_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False, index=True),
        sa.Column('sneaker_id', sa.Integer(), sa.ForeignKey('sneaker.id'), nullable=False, index=True),
        sa.Column('reported_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('damage_type', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('notes', sa.String(length=280), nullable=True),
        sa.Column('photo_url', sa.String(length=1024), nullable=True),
        sa.Column('health_penalty_points', sa.Float(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'sneaker_repair_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False, index=True),
        sa.Column('sneaker_id', sa.Integer(), sa.ForeignKey('sneaker.id'), nullable=False, index=True),
        sa.Column('repaired_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('repair_kind', sa.String(length=20), nullable=False),
        sa.Column('repair_type', sa.String(length=100), nullable=False),
        sa.Column('provider', sa.String(length=120), nullable=True),
        sa.Column('cost_amount', sa.Numeric(10, 2), nullable=True),
        sa.Column('cost_currency', sa.String(length=3), nullable=True),
        sa.Column('notes', sa.String(length=280), nullable=True),
        sa.Column('resolved_all_active_damage', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'sneaker_expense',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False, index=True),
        sa.Column('sneaker_id', sa.Integer(), sa.ForeignKey('sneaker.id'), nullable=False, index=True),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('expense_date', sa.DateTime(), nullable=False, index=True),
        sa.Column('notes', sa.String(length=280), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    op.execute("UPDATE sneaker SET persistent_structural_damage_points = 0 WHERE persistent_structural_damage_points IS NULL")


def downgrade():
    op.drop_table('sneaker_expense')
    op.drop_table('sneaker_repair_event')
    op.drop_table('sneaker_damage_event')

    with op.batch_alter_table('sneaker') as batch_op:
        batch_op.drop_column('persistent_structural_damage_points')
