"""phase 3a user column additions: password_hash nullable, last_login_at, auth_provider

Phase 3a of the Supabase Auth migration. Adds the schema surface required
by the (forthcoming) session bridge so that a brand-new Supabase-first
user can be persisted with no app-managed password while existing rows
keep their hashes.

Three changes, all on the ``user`` table:

    1. ``password_hash`` is relaxed to ``NULLABLE``. Existing rows keep
       their hashes; rows created by the Supabase bridge land with NULL.
       A defensive guard in ``User.check_password`` ensures a NULL hash
       never authenticates.

    2. ``last_login_at`` (TIMESTAMP NULL) is added. Written by the
       bridge on each Supabase sign-in and (optionally) by the legacy
       login path. Useful for cohort backfill and Phase 4 sunset
       planning. NULL for existing rows on this migration; populated
       organically as users sign in.

    3. ``auth_provider`` (VARCHAR(40) NULL) is added. Records how each
       user authenticates. Values: ``legacy``, ``supabase_email``,
       ``supabase_oauth_google``, ``supabase_oauth_apple``, etc. NULL
       for existing rows on this migration; the bridge sets it on
       create / link.

Migration safety:

    - No data is rewritten. All three changes apply to schema only.
    - Postgres 12+: relaxing NOT NULL is metadata-only. Column adds
      without a server default are also metadata-only.
    - SQLite (test DB): handled via ``batch_alter_table`` which is
      already enabled by ``render_as_batch=True`` in ``app.py``.
    - The Phase 1 ``supabase_auth_user_id`` column and its partial
      unique index are untouched.

Downgrade caveat (one-way door once Phase 3a flag flips):

    The ``downgrade()`` step re-tightens ``password_hash`` to
    NOT NULL. This succeeds only while every row still has a non-NULL
    hash — which is true until the bridge endpoint creates the first
    Supabase-first user with ``password_hash=NULL``. After that point
    the migration is effectively one-way. Operators must keep at least
    24 hours between this migration's deploy and the
    ``SUPABASE_NEW_USER_SIGNUP_ENABLED`` flag flip in production so the
    downgrade window remains usable; see
    ``docs/SUPABASE_AUTH_PHASE3A_EXECUTION_PLAN.md`` §10.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-05-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'password_hash',
            existing_type=sa.String(length=256),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column('last_login_at', sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('auth_provider', sa.String(length=40), nullable=True)
        )


def downgrade():
    # NOTE: re-tightening password_hash to NOT NULL will fail if any row
    # has NULL — i.e. once the Phase 3a bridge has created its first
    # Supabase-first user. This downgrade is intentionally best-effort:
    # operators are expected to use the 24-hour pre-flag-flip window.
    # If the downgrade is attempted post-flip, the failure is loud and
    # the operator must either backfill ``password_hash`` for affected
    # rows or accept that the schema rollback path is closed.
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('auth_provider')
        batch_op.drop_column('last_login_at')
        batch_op.alter_column(
            'password_hash',
            existing_type=sa.String(length=256),
            nullable=False,
        )
