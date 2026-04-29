"""add release calendar visible flag

Revision ID: 4c7d9e2f1a6b
Revises: 3a6b9c2d1e4f
Create Date: 2026-01-20 19:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4c7d9e2f1a6b'
down_revision = '3a6b9c2d1e4f'
branch_labels = None
depends_on = None


def upgrade():
    calendar_visible_default = sa.true() if op.get_bind().dialect.name == 'postgresql' else sa.text('1')
    with op.batch_alter_table('release') as batch_op:
        batch_op.add_column(
            sa.Column(
                'is_calendar_visible',
                sa.Boolean(),
                nullable=False,
                server_default=calendar_visible_default,
            )
        )


def downgrade():
    with op.batch_alter_table('release') as batch_op:
        batch_op.drop_column('is_calendar_visible')
