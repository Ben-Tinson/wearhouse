"""add supabase_auth_user_id linkage column to user

Phase 1 of the Supabase Auth migration. Adds a dormant nullable UUID column
to the app-owned ``user`` table plus a partial unique index covering only
non-null values. No data is rewritten; no existing behaviour is changed.

Revision ID: b3c4d5e6f7a8
Revises: a7c3d9e4f1b2
Create Date: 2026-04-28 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'a7c3d9e4f1b2'
branch_labels = None
depends_on = None


INDEX_NAME = 'uq_user_supabase_auth_user_id'


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('supabase_auth_user_id', sa.Uuid(), nullable=True)
        )

    # Partial unique index: enforce uniqueness only for rows that have been
    # linked to a Supabase Auth identity. Existing rows land NULL and are
    # ignored by the index. Both Postgres and SQLite support the partial
    # form via dialect-specific ``*_where`` kwargs.
    op.create_index(
        INDEX_NAME,
        'user',
        ['supabase_auth_user_id'],
        unique=True,
        postgresql_where=sa.text('supabase_auth_user_id IS NOT NULL'),
        sqlite_where=sa.text('supabase_auth_user_id IS NOT NULL'),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name='user')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('supabase_auth_user_id')
