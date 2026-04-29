"""Migration round-trip test for the Phase 1 Supabase Auth linkage column.

This test exercises ``flask db upgrade`` → ``flask db downgrade`` against a
disposable on-disk SQLite database. We do not use the in-memory ``TestConfig``
DB here because Alembic needs a stable connection across upgrade/downgrade
calls, and the test app context's auto ``db.create_all()`` must not be
applied (the migration is the schema source for this test).
"""

from __future__ import annotations

import os
import tempfile

from flask import Flask
from flask_migrate import Migrate, downgrade as migrate_downgrade, upgrade as migrate_upgrade

from config import Config
from extensions import db, migrate as migrate_extension


PHASE1_REVISION = "b3c4d5e6f7a8"
INDEX_NAME = "uq_user_supabase_auth_user_id"


def _build_app(db_path: str) -> Flask:
    """Build a minimal Flask app pointed at a disposable SQLite file.

    We avoid ``app.create_app`` here because that path runs blueprint
    registration and Jinja-globals wiring that pull in many services we do
    not need for a schema-only round-trip. The migration only needs ``db``
    and Flask-Migrate.
    """
    app = Flask(__name__)

    class _MigrationTestConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        TESTING = True

    app.config.from_object(_MigrationTestConfig)

    # Use the existing extension instances; their ``init_app`` is idempotent
    # for our purposes (each test creates a fresh ``Flask`` app).
    db.init_app(app)
    migrate_extension.init_app(app, db, render_as_batch=True)
    return app


def _column_names(table: str) -> list:
    return [c["name"] for c in db.inspect(db.engine).get_columns(table)]


def _index_names(table: str) -> list:
    return [ix["name"] for ix in db.inspect(db.engine).get_indexes(table)]


def test_phase1_migration_round_trip():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "phase1_roundtrip.db")
        app = _build_app(db_path)
        with app.app_context():
            # Upgrade from base to current head; the Phase 1 revision should
            # already be the head if this test ships in the same commit.
            migrate_upgrade()

            assert "supabase_auth_user_id" in _column_names("user")
            assert INDEX_NAME in _index_names("user")

            indexes = {ix["name"]: ix for ix in db.inspect(db.engine).get_indexes("user")}
            assert bool(indexes[INDEX_NAME]["unique"]) is True
            assert indexes[INDEX_NAME]["column_names"] == ["supabase_auth_user_id"]

            # Downgrade exactly the Phase 1 revision and verify both the
            # column and the partial unique index disappear.
            migrate_downgrade(revision="-1")
            assert "supabase_auth_user_id" not in _column_names("user")
            assert INDEX_NAME not in _index_names("user")

            # Re-applying the migration leaves the schema in the head state.
            migrate_upgrade()
            assert "supabase_auth_user_id" in _column_names("user")
            assert INDEX_NAME in _index_names("user")
