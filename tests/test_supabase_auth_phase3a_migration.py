"""Phase 3a PR 1 — schema migration round-trip + model behaviour tests.

Pins:
    - The Phase 3a migration (``c4d5e6f7a8b9``) round-trips cleanly while
      every ``user`` row still has a non-NULL ``password_hash`` — the
      pre-flag-flip downgrade window documented in
      ``docs/SUPABASE_AUTH_PHASE3A_EXECUTION_PLAN.md`` §10.
    - The User model exposes the two new columns (``last_login_at``,
      ``auth_provider``) and ``password_hash`` is now nullable.
    - ``User.check_password`` returns False when ``password_hash`` is
      None (Supabase-first user defence) and continues to behave
      correctly for legacy users.

These tests are inert with respect to live behaviour; the new columns
have no writers in this slice.
"""

from __future__ import annotations

import os
import tempfile

from flask import Flask
from flask_migrate import downgrade as migrate_downgrade
from flask_migrate import upgrade as migrate_upgrade

from config import Config
from extensions import db, migrate as migrate_extension
from models import User


PHASE3A_REVISION = "c4d5e6f7a8b9"


def _build_migration_app(db_path: str) -> Flask:
    """Minimal Flask app for schema-only migration tests.

    Mirrors the pattern from ``tests/test_supabase_auth_migration.py``.
    Avoids ``app.create_app`` because we do not need blueprint /
    template / Jinja-globals wiring for a schema round-trip.
    """
    app = Flask(__name__)

    class _MigrationTestConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        TESTING = True

    app.config.from_object(_MigrationTestConfig)
    db.init_app(app)
    migrate_extension.init_app(app, db, render_as_batch=True)
    return app


def _column_names(table: str) -> list:
    return [c["name"] for c in db.inspect(db.engine).get_columns(table)]


def _column(table: str, name: str) -> dict:
    for c in db.inspect(db.engine).get_columns(table):
        if c["name"] == name:
            return c
    raise AssertionError(f"column {table}.{name} not present")


def test_phase3a_migration_round_trip_when_no_supabase_users():
    """Upgrade → downgrade -1 → upgrade succeeds while no row has NULL hash."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "phase3a.db")
        app = _build_migration_app(db_path)
        with app.app_context():
            migrate_upgrade()  # all the way to head, including Phase 3a

            cols = _column_names("user")
            assert "last_login_at" in cols
            assert "auth_provider" in cols

            password_hash_col = _column("user", "password_hash")
            assert password_hash_col["nullable"] is True

            last_login_col = _column("user", "last_login_at")
            assert last_login_col["nullable"] is True

            auth_provider_col = _column("user", "auth_provider")
            assert auth_provider_col["nullable"] is True

            # Downgrade exactly the Phase 3a revision. With no Supabase-
            # first users present (every row has a non-NULL hash, which
            # is true here because the table is empty) this must succeed.
            migrate_downgrade(revision="-1")

            cols_after_downgrade = _column_names("user")
            assert "last_login_at" not in cols_after_downgrade
            assert "auth_provider" not in cols_after_downgrade
            password_hash_col_after = _column("user", "password_hash")
            assert password_hash_col_after["nullable"] is False

            # Re-upgrade and confirm head state again.
            migrate_upgrade()
            cols_after_reupgrade = _column_names("user")
            assert "last_login_at" in cols_after_reupgrade
            assert "auth_provider" in cols_after_reupgrade
            assert _column("user", "password_hash")["nullable"] is True


def test_phase3a_migration_preserves_phase1_supabase_link_column():
    """Phase 1's ``supabase_auth_user_id`` column and its partial unique
    index must be untouched by the Phase 3a migration."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "phase3a_link.db")
        app = _build_migration_app(db_path)
        with app.app_context():
            migrate_upgrade()
            cols = _column_names("user")
            assert "supabase_auth_user_id" in cols

            indexes = {ix["name"] for ix in db.inspect(db.engine).get_indexes("user")}
            assert "uq_user_supabase_auth_user_id" in indexes


def test_user_model_check_password_returns_false_for_null_hash(test_app):
    """A Supabase-first row (password_hash IS NULL) must not authenticate
    via the legacy ``LoginForm`` path."""
    with test_app.app_context():
        user = User(
            username="supabase_first",
            email="supabase_first@example.com",
            first_name="SF",
            last_name="User",
            is_email_confirmed=True,
            auth_provider="supabase_email",
        )
        # Deliberately do NOT call set_password — leave password_hash NULL,
        # the way the (forthcoming) bridge will create rows.
        db.session.add(user)
        db.session.commit()

        reloaded = db.session.get(User, user.id)
        assert reloaded.password_hash is None
        assert reloaded.check_password("anything") is False
        assert reloaded.check_password("") is False


def test_user_model_check_password_still_works_for_legacy_user(test_app):
    """A legacy row (password_hash set via ``set_password``) keeps
    working exactly as before."""
    with test_app.app_context():
        user = User(
            username="legacy_user",
            email="legacy@example.com",
            first_name="Legacy",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("correct-password-123")
        db.session.add(user)
        db.session.commit()

        reloaded = db.session.get(User, user.id)
        assert reloaded.password_hash is not None
        assert reloaded.check_password("correct-password-123") is True
        assert reloaded.check_password("wrong-password") is False


def test_user_model_exposes_new_phase3a_columns(test_app):
    """``last_login_at`` and ``auth_provider`` are queryable on the model
    and default to None for new rows."""
    from datetime import datetime

    with test_app.app_context():
        user = User(
            username="phase3a_cols",
            email="phase3a_cols@example.com",
            first_name="P3A",
            last_name="Cols",
            is_email_confirmed=True,
        )
        user.set_password("p")
        db.session.add(user)
        db.session.commit()

        reloaded = db.session.get(User, user.id)
        assert reloaded.last_login_at is None
        assert reloaded.auth_provider is None

        # Round-trip writes.
        now = datetime.utcnow()
        reloaded.last_login_at = now
        reloaded.auth_provider = "supabase_email"
        db.session.commit()

        re_reloaded = db.session.get(User, user.id)
        assert re_reloaded.last_login_at == now
        assert re_reloaded.auth_provider == "supabase_email"
