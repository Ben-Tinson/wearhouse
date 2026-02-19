"""add health history and stains

Revision ID: d2e3f4a5b6c7
Revises: 7c8d9e0f1a2b
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2e3f4a5b6c7'
down_revision = '7c8d9e0f1a2b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sneaker') as batch_op:
        batch_op.add_column(sa.Column('persistent_stain_points', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('persistent_material_damage_points', sa.Float(), nullable=False, server_default='0'))

    with op.batch_alter_table('exposure_event') as batch_op:
        batch_op.add_column(sa.Column('stain_flag', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('stain_severity', sa.Integer(), nullable=True))

    op.create_table(
        'sneaker_clean_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False, index=True),
        sa.Column('sneaker_id', sa.Integer(), sa.ForeignKey('sneaker.id'), nullable=False, index=True),
        sa.Column('cleaned_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('stain_removed', sa.Boolean(), nullable=True),
        sa.Column('lasting_material_impact', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('notes', sa.String(length=280), nullable=True),
    )

    op.create_table(
        'sneaker_health_snapshot',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sneaker_id', sa.Integer(), sa.ForeignKey('sneaker.id'), nullable=False, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False, index=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('health_score', sa.Float(), nullable=False),
        sa.Column('reason', sa.String(length=40), nullable=False),
    )

    op.execute("UPDATE sneaker SET persistent_stain_points = 0 WHERE persistent_stain_points IS NULL")
    op.execute("UPDATE sneaker SET persistent_material_damage_points = 0 WHERE persistent_material_damage_points IS NULL")


def downgrade():
    op.drop_table('sneaker_health_snapshot')
    op.drop_table('sneaker_clean_event')

    with op.batch_alter_table('exposure_event') as batch_op:
        batch_op.drop_column('stain_severity')
        batch_op.drop_column('stain_flag')

    with op.batch_alter_table('sneaker') as batch_op:
        batch_op.drop_column('persistent_material_damage_points')
        batch_op.drop_column('persistent_stain_points')
