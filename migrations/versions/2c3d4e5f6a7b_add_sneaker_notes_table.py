"""add sneaker notes table

Revision ID: 2c3d4e5f6a7b
Revises: 9a4aecee313e
Create Date: 2026-01-22 18:25:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2c3d4e5f6a7b'
down_revision = '9a4aecee313e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sneaker_note',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sneaker_id', sa.Integer(), sa.ForeignKey('sneaker.id'), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_sneaker_note_sneaker_id', 'sneaker_note', ['sneaker_id'])

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, notes FROM sneaker WHERE notes IS NOT NULL AND notes != ''"))
    for sneaker_id, notes in rows:
        conn.execute(
            sa.text(
                "INSERT INTO sneaker_note (sneaker_id, body, created_at) "
                "VALUES (:sneaker_id, :body, CURRENT_TIMESTAMP)"
            ),
            {"sneaker_id": sneaker_id, "body": notes},
        )


def downgrade():
    op.drop_index('ix_sneaker_note_sneaker_id', table_name='sneaker_note')
    op.drop_table('sneaker_note')
