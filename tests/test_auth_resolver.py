"""Tests for the Phase 1 auth resolver shim.

Phase 1 contract: the resolver is exactly equivalent to
``current_user if current_user.is_authenticated else None`` and must not
introduce any behavioural change. These tests pin that contract so a future
Phase 2 extension cannot silently regress it.
"""

from flask import Blueprint, jsonify

from extensions import db
from models import User
from services.auth_resolver import (
    get_current_app_user,
    get_current_app_user_id,
    is_current_app_user_admin,
)


def _register_probe_blueprint(app):
    """Register routes that expose resolver state for assertions."""
    if "auth_resolver_probe" in app.blueprints:
        return
    bp = Blueprint("auth_resolver_probe", __name__)

    @bp.route("/__probe/current_user")
    def probe_current_user():
        user = get_current_app_user()
        return jsonify(
            {
                "user_id": user.id if user is not None else None,
                "resolver_id": get_current_app_user_id(),
                "is_admin": is_current_app_user_admin(),
            }
        )

    app.register_blueprint(bp)


def test_resolver_returns_none_for_anonymous_request(test_app, test_client):
    _register_probe_blueprint(test_app)
    response = test_client.get("/__probe/current_user")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"user_id": None, "resolver_id": None, "is_admin": False}


def test_resolver_matches_current_user_for_authenticated_user(
    test_app, test_client, init_database, auth
):
    _register_probe_blueprint(test_app)
    auth.login(username="testuser", password="password123")

    response = test_client.get("/__probe/current_user")
    assert response.status_code == 200
    payload = response.get_json()

    with test_app.app_context():
        expected_user = User.query.filter_by(username="testuser").one()
        assert payload["user_id"] == expected_user.id
        assert payload["resolver_id"] == expected_user.id
        assert payload["is_admin"] is False


def test_resolver_reports_admin_for_admin_user(test_app, test_client, admin_user, auth):
    _register_probe_blueprint(test_app)
    auth.login(username="adminuser", password="password123")

    response = test_client.get("/__probe/current_user")
    assert response.status_code == 200
    payload = response.get_json()

    with test_app.app_context():
        admin = User.query.filter_by(username="adminuser").one()
        assert payload["user_id"] == admin.id
        assert payload["resolver_id"] == admin.id
        assert payload["is_admin"] is True


def test_resolver_returns_none_after_logout(test_app, test_client, init_database, auth):
    _register_probe_blueprint(test_app)
    auth.login(username="testuser", password="password123")
    auth.logout()

    response = test_client.get("/__probe/current_user")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"user_id": None, "resolver_id": None, "is_admin": False}


def test_supabase_auth_user_id_column_exists_and_defaults_null(test_app, init_database):
    """Phase 1 sentinel: the dormant linkage column exists and is NULL for all rows."""
    with test_app.app_context():
        users = User.query.all()
        assert users, "expected the init_database fixture to seed at least one user"
        for user in users:
            assert hasattr(user, "supabase_auth_user_id")
            assert user.supabase_auth_user_id is None

        # Round-trip: writing a UUID to the column persists and reloads cleanly.
        import uuid

        target = users[0]
        new_uuid = uuid.uuid4()
        target.supabase_auth_user_id = new_uuid
        db.session.commit()

        reloaded = db.session.get(User, target.id)
        assert reloaded.supabase_auth_user_id == new_uuid
