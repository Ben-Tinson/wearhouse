"""Phase 2 resolver tests.

These tests pin two contracts:

1. **Flag-off parity.** With ``SUPABASE_AUTH_ENABLED=False`` the resolver
   is byte-for-byte equivalent to the Phase 1 shape (Flask-Login or None).
2. **Flag-on Supabase JWT branch.** With the flag on, an Authorization
   bearer JWT for a linked app user resolves to that app user; for an
   unlinked email it returns ``None`` and **does not** auto-link.
"""

from __future__ import annotations

import time
import uuid

import jwt
from flask import Blueprint, jsonify

from extensions import db
from models import User
from services.auth_resolver import (
    get_current_app_user,
    get_current_app_user_id,
    is_current_app_user_admin,
)
from services.supabase_auth_linkage import link_app_user_to_supabase


JWT_SECRET = "phase2-resolver-test-secret"


def _register_resolver_probe(app):
    if "resolver_phase2_probe" in app.blueprints:
        return
    bp = Blueprint("resolver_phase2_probe", __name__)

    @bp.route("/__probe/resolver")
    def probe():
        user = get_current_app_user()
        return jsonify(
            {
                "user_id": user.id if user is not None else None,
                "resolver_id": get_current_app_user_id(),
                "is_admin": is_current_app_user_admin(),
                "supabase_link": str(user.supabase_auth_user_id)
                if user is not None and user.supabase_auth_user_id is not None
                else None,
            }
        )

    app.register_blueprint(bp)


def _enable_supabase(app):
    app.config["SUPABASE_AUTH_ENABLED"] = True
    app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET


def _disable_supabase(app):
    app.config["SUPABASE_AUTH_ENABLED"] = False


def _make_jwt(secret: str = JWT_SECRET, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": "user@example.com",
        "iat": now,
        "exp": now + 60,
        "aud": "authenticated",
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Flag-off parity (must equal Phase 1 behaviour exactly)
# ---------------------------------------------------------------------------


def test_flag_off_anonymous_request_returns_none(test_app, test_client):
    _disable_supabase(test_app)
    _register_resolver_probe(test_app)
    response = test_client.get("/__probe/resolver")
    assert response.status_code == 200
    assert response.get_json() == {
        "user_id": None,
        "resolver_id": None,
        "is_admin": False,
        "supabase_link": None,
    }


def test_flag_off_authenticated_user_resolves_via_flask_login(
    test_app, test_client, init_database, auth
):
    _disable_supabase(test_app)
    _register_resolver_probe(test_app)
    auth.login(username="testuser", password="password123")

    response = test_client.get("/__probe/resolver")
    payload = response.get_json()

    with test_app.app_context():
        expected = User.query.filter_by(username="testuser").one()
        assert payload["user_id"] == expected.id
        assert payload["resolver_id"] == expected.id
        assert payload["is_admin"] is False


def test_flag_off_admin_resolves_with_is_admin_true(
    test_app, test_client, admin_user, auth
):
    _disable_supabase(test_app)
    _register_resolver_probe(test_app)
    auth.login(username="adminuser", password="password123")

    response = test_client.get("/__probe/resolver")
    payload = response.get_json()
    assert payload["is_admin"] is True


def test_flag_off_ignores_jwt_in_authorization_header(
    test_app, test_client, init_database
):
    """A Supabase JWT presented while the flag is off must be ignored.

    This locks in the rule that flag-off behaviour cannot be circumvented
    by sending a JWT — the resolver path returns Phase 1 semantics.
    """
    _disable_supabase(test_app)
    _register_resolver_probe(test_app)

    response = test_client.get(
        "/__probe/resolver",
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    payload = response.get_json()
    assert payload == {
        "user_id": None,
        "resolver_id": None,
        "is_admin": False,
        "supabase_link": None,
    }


# ---------------------------------------------------------------------------
# Flag-on Supabase JWT branch
# ---------------------------------------------------------------------------


def test_flag_on_resolves_linked_user_via_jwt(test_app, test_client):
    _enable_supabase(test_app)
    _register_resolver_probe(test_app)

    target_uuid = uuid.uuid4()
    with test_app.app_context():
        user = User(
            username="linked_user",
            email="linked@example.com",
            first_name="Linked",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        link_app_user_to_supabase(user.id, target_uuid)
        expected_id = user.id

    token = _make_jwt(sub=str(target_uuid), email="linked@example.com")
    response = test_client.get(
        "/__probe/resolver", headers={"Authorization": f"Bearer {token}"}
    )
    payload = response.get_json()
    assert payload["user_id"] == expected_id
    assert payload["resolver_id"] == expected_id
    assert payload["supabase_link"] == str(target_uuid)


def test_flag_on_does_not_auto_link_unlinked_email(test_app, test_client):
    """JWT for an email that maps to an unlinked app user must NOT auto-link.

    This is the central write-safety guard: even though the resolver could
    in principle link the user, the accepted decision says only the
    explicit linkage tooling may write ``supabase_auth_user_id``.
    """
    _enable_supabase(test_app)
    _register_resolver_probe(test_app)

    with test_app.app_context():
        user = User(
            username="unlinked_user",
            email="unlinked@example.com",
            first_name="Unlinked",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    token = _make_jwt(sub=str(uuid.uuid4()), email="unlinked@example.com")
    response = test_client.get(
        "/__probe/resolver", headers={"Authorization": f"Bearer {token}"}
    )
    payload = response.get_json()
    assert payload["user_id"] is None  # not auto-linked

    # The DB row remains unlinked.
    with test_app.app_context():
        reloaded = db.session.get(User, user_id)
        assert reloaded.supabase_auth_user_id is None


def test_flag_on_invalid_jwt_resolves_to_none(test_app, test_client):
    _enable_supabase(test_app)
    _register_resolver_probe(test_app)
    response = test_client.get(
        "/__probe/resolver",
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert response.get_json()["user_id"] is None


def test_flag_on_flask_login_still_wins_over_jwt(
    test_app, test_client, init_database, auth
):
    """Resolution order rule: Flask-Login session takes precedence over JWT."""
    _enable_supabase(test_app)
    _register_resolver_probe(test_app)
    auth.login(username="testuser", password="password123")

    response = test_client.get(
        "/__probe/resolver",
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    payload = response.get_json()
    with test_app.app_context():
        expected = User.query.filter_by(username="testuser").one()
        assert payload["user_id"] == expected.id


def test_resolver_outside_request_returns_none(test_app):
    """Calling the resolver outside a request context must not crash."""
    _enable_supabase(test_app)
    with test_app.app_context():
        # No active request → JWT branch must be skipped, Flask-Login not
        # available → result is None.
        assert get_current_app_user() is None
        assert get_current_app_user_id() is None
        assert is_current_app_user_admin() is False
