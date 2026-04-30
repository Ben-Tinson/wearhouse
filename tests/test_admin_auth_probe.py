"""Tests for the Phase 2 ``/admin/auth/probe`` endpoint.

The probe is the first production surface that exercises the resolver's
Supabase JWT branch against real data. It must be:

    - admin-only (404 / 403 / 401 otherwise)
    - flag-gated (404 when SUPABASE_AUTH_ENABLED is False)
    - read-only (no DB writes; no Flask-Login session creation)
    - no-auto-link (a JWT for an unlinked identity must NOT write
      ``user.supabase_auth_user_id``)
"""

from __future__ import annotations

import time
import uuid

import jwt

from extensions import db
from models import User, UserApiToken
from services.supabase_auth_linkage import link_app_user_to_supabase


PROBE_PATH = "/admin/auth/probe"
JWT_SECRET = "phase2-probe-test-secret"


def _enable(app):
    app.config["SUPABASE_AUTH_ENABLED"] = True
    app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET


def _disable(app):
    app.config["SUPABASE_AUTH_ENABLED"] = False


def _make_jwt(*, sub: str, email: str = "linked@example.com", secret: str = JWT_SECRET) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "email": email, "iat": now, "exp": now + 60},
        secret,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Flag-gating
# ---------------------------------------------------------------------------


def test_probe_returns_404_when_flag_off(test_app, test_client, admin_user, auth):
    _disable(test_app)
    auth.login(username="adminuser", password="password123")
    response = test_client.get(PROBE_PATH)
    assert response.status_code == 404


def test_probe_returns_404_when_flag_off_even_with_jwt(test_app, test_client, admin_user, auth):
    """Flag-off must override any JWT presented in the header."""
    _disable(test_app)
    auth.login(username="adminuser", password="password123")
    response = test_client.get(
        PROBE_PATH,
        headers={"Authorization": f"Bearer {_make_jwt(sub=str(uuid.uuid4()))}"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Admin-gating (with flag on)
# ---------------------------------------------------------------------------


def test_probe_redirects_anonymous_to_login(test_app, test_client):
    _enable(test_app)
    response = test_client.get(PROBE_PATH, follow_redirects=False)
    # @login_required redirects unauthenticated requests to the login view.
    assert response.status_code in (301, 302)
    assert "/login" in response.headers.get("Location", "")


def test_probe_returns_403_for_non_admin_user(test_app, test_client, init_database, auth):
    _enable(test_app)
    auth.login(username="testuser", password="password123")
    response = test_client.get(PROBE_PATH)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Admin success paths
# ---------------------------------------------------------------------------


def test_probe_returns_admin_identity_via_flask_login_when_no_jwt(
    test_app, test_client, admin_user, auth
):
    _enable(test_app)
    auth.login(username="adminuser", password="password123")

    response = test_client.get(PROBE_PATH)
    assert response.status_code == 200
    payload = response.get_json()

    with test_app.app_context():
        admin = User.query.filter_by(username="adminuser").one()
        assert payload == {
            "ok": True,
            "via": "flask_login",
            "user_id": admin.id,
            "is_admin": True,
            "supabase_user_id": None,
        }


def test_probe_resolves_linked_user_via_jwt(test_app, test_client, admin_user, auth):
    """A valid JWT for a linked app user resolves through the Supabase branch."""
    _enable(test_app)
    auth.login(username="adminuser", password="password123")

    target_uuid = uuid.uuid4()
    with test_app.app_context():
        # Create a separate linked user (not the admin) so we can confirm
        # the probe really resolved via the JWT, not via the session.
        linked = User(
            username="linked_via_jwt",
            email="linked_via_jwt@example.com",
            first_name="Linked",
            last_name="JWT",
            is_email_confirmed=True,
        )
        linked.set_password("password123")
        db.session.add(linked)
        db.session.commit()
        link_app_user_to_supabase(linked.id, target_uuid)
        linked_id = linked.id

    token = _make_jwt(sub=str(target_uuid), email="linked_via_jwt@example.com")
    response = test_client.get(PROBE_PATH, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["via"] == "supabase"
    assert payload["user_id"] == linked_id
    assert payload["supabase_user_id"] == str(target_uuid)
    assert payload["is_admin"] is False


def test_probe_reports_unlinked_jwt_without_authorising(test_app, test_client, admin_user, auth):
    """JWT verifies but no user is linked → ok=false; no auto-link occurs."""
    _enable(test_app)
    auth.login(username="adminuser", password="password123")

    unlinked_sub = str(uuid.uuid4())

    with test_app.app_context():
        # Existing user with a matching email but no linkage. The probe
        # MUST NOT silently link this user.
        ghost = User(
            username="ghost_user",
            email="ghost@example.com",
            first_name="Ghost",
            last_name="User",
            is_email_confirmed=True,
        )
        ghost.set_password("password123")
        db.session.add(ghost)
        db.session.commit()
        ghost_id = ghost.id

    token = _make_jwt(sub=unlinked_sub, email="ghost@example.com")
    response = test_client.get(PROBE_PATH, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["via"] == "supabase"
    assert payload["supabase_user_id"] == unlinked_sub
    assert "not linked" in payload["error"]

    # Crucial: no auto-link.
    with test_app.app_context():
        assert db.session.get(User, ghost_id).supabase_auth_user_id is None


def test_probe_returns_401_for_invalid_jwt(test_app, test_client, admin_user, auth):
    _enable(test_app)
    auth.login(username="adminuser", password="password123")
    response = test_client.get(
        PROBE_PATH,
        headers={"Authorization": "Bearer invalid.jwt.value"},
    )
    assert response.status_code == 401
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["via"] == "supabase"


def test_probe_returns_400_for_non_jwt_bearer(test_app, test_client, admin_user, auth):
    """An opaque (non-JWT) bearer is a client error in the probe context."""
    _enable(test_app)
    auth.login(username="adminuser", password="password123")
    response = test_client.get(
        PROBE_PATH,
        headers={"Authorization": "Bearer opaque-no-dots-here"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------


def test_probe_resolves_linked_user_via_es256_jwt(
    test_app, test_client, admin_user, auth, monkeypatch
):
    """End-to-end ES256 path: a Supabase asymmetric token resolves through
    the probe via JWKS, mirroring the real staging-rehearsal flow.

    This test pins the fix for the staging probe rejection
    ("The specified alg value is not allowed"). With the JWKS-aware
    verifier, a real ES256 access token signed by Supabase's project key
    now verifies and the linked app user is resolved correctly.
    """
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())

    class _FakeJWK:
        def __init__(self, key):
            self.key = key

    class _FakeJWKSClient:
        def __init__(self, key):
            self._key = key

        def get_signing_key_from_jwt(self, token):
            return _FakeJWK(self._key)

    monkeypatch.setattr(
        "services.supabase_auth_service._get_jwks_client",
        lambda url: _FakeJWKSClient(private_key.public_key()),
    )

    _enable(test_app)
    test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
    auth.login(username="adminuser", password="password123")

    target_uuid = uuid.uuid4()
    with test_app.app_context():
        linked = User(
            username="es256_admin",
            email="es256_admin@example.com",
            first_name="ES256",
            last_name="Admin",
            is_email_confirmed=True,
            is_admin=True,
        )
        linked.set_password("password123")
        db.session.add(linked)
        db.session.commit()
        link_app_user_to_supabase(linked.id, target_uuid)
        linked_id = linked.id

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": str(target_uuid),
            "email": "es256_admin@example.com",
            "iat": now,
            "exp": now + 60,
            "aud": "authenticated",
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )

    response = test_client.get(
        PROBE_PATH, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["via"] == "supabase"
    assert payload["user_id"] == linked_id
    assert payload["is_admin"] is True
    assert payload["supabase_user_id"] == str(target_uuid)


def test_probe_does_not_create_or_modify_user_rows(
    test_app, test_client, admin_user, auth
):
    """End-to-end no-write check across the most common probe scenarios."""
    _enable(test_app)
    auth.login(username="adminuser", password="password123")

    with test_app.app_context():
        admin = User.query.filter_by(username="adminuser").one()
        original_link = admin.supabase_auth_user_id
        original_user_count = User.query.count()
        original_token_count = UserApiToken.query.count()

    # No JWT
    test_client.get(PROBE_PATH)
    # Invalid JWT
    test_client.get(PROBE_PATH, headers={"Authorization": "Bearer bad.jwt.token"})
    # JWT for non-existent linkage
    test_client.get(
        PROBE_PATH,
        headers={"Authorization": f"Bearer {_make_jwt(sub=str(uuid.uuid4()))}"},
    )

    with test_app.app_context():
        assert User.query.count() == original_user_count
        assert UserApiToken.query.count() == original_token_count
        admin_reloaded = User.query.filter_by(username="adminuser").one()
        assert admin_reloaded.supabase_auth_user_id == original_link
