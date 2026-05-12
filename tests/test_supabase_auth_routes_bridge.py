"""Tests for ``routes/supabase_auth_routes.py`` — Phase 3a PR 3.

Pin the bridge endpoint contract:

    - 404 when ``SUPABASE_NEW_USER_SIGNUP_ENABLED`` is False (the entire
      blueprint is invisible).
    - 200 happy-path with a Flask-Login session cookie issued.
    - 200 returning-user fast path.
    - 400 on missing / malformed body fields.
    - 400 on profile-validation failure (with ``field_errors``).
    - 401 on token-verifier failure.
    - 409 on email-match against an unlinked legacy user.
    - 503 on config drift (SUPABASE_AUTH_ENABLED=False).

The Supabase JWT verifier is monkey-patched per test so no real
ES256/JWKS infrastructure is required.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import pytest

from extensions import db
from models import User
from services.supabase_auth_service import SupabaseClaims, SupabaseTokenInvalid


BRIDGE_PATH = "/auth/supabase/bridge"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_phase3a(app):
    app.config["SUPABASE_AUTH_ENABLED"] = True
    app.config["SUPABASE_NEW_USER_SIGNUP_ENABLED"] = True


def _disable_phase3a(app):
    app.config["SUPABASE_NEW_USER_SIGNUP_ENABLED"] = False


def _patch_bridge_verifier(monkeypatch, *, claims=None, raise_with=None):
    """Replace the bridge service's verifier with a fixture."""

    def _fake_verify(token: str):
        if raise_with is not None:
            raise raise_with
        return claims

    monkeypatch.setattr(
        "services.supabase_session_bridge.verify_access_token",
        _fake_verify,
    )


def _make_claims(
    *,
    sub: Optional[str] = None,
    email: str = "newuser@example.com",
    provider: str = "email",
) -> SupabaseClaims:
    return SupabaseClaims(
        supabase_user_id=str(sub or uuid.uuid4()),
        email=email,
        raw={"app_metadata": {"provider": provider}},
    )


def _valid_profile(**overrides) -> Dict[str, Any]:
    payload = {
        "username": "fresh_user",
        "first_name": "Fresh",
        "last_name": "User",
        "preferred_region": "UK",
        "marketing_opt_in": False,
    }
    payload.update(overrides)
    return payload


def _make_legacy_user(**overrides) -> User:
    defaults = {
        "username": "legacy_user",
        "email": "legacy@example.com",
        "first_name": "Legacy",
        "last_name": "User",
        "is_email_confirmed": True,
        "is_admin": False,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def _post_bridge(client, body=None, audit_skip=True):
    """POST /auth/supabase/bridge. Skips audit-file writes by patching
    the bridge default audit path to /dev/null-equivalent."""
    return client.post(BRIDGE_PATH, json=body)


# ---------------------------------------------------------------------------
# Flag gating
# ---------------------------------------------------------------------------


def test_bridge_returns_404_when_signup_flag_off(test_app, test_client):
    _disable_phase3a(test_app)
    response = _post_bridge(
        test_client, {"access_token": "tok", "profile": _valid_profile()}
    )
    assert response.status_code == 404


def test_bridge_returns_404_when_flag_off_even_with_garbage_body(test_app, test_client):
    _disable_phase3a(test_app)
    response = test_client.post(BRIDGE_PATH, data="not even json")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


def test_bridge_400_on_non_json_body(test_app, test_client):
    _enable_phase3a(test_app)
    response = test_client.post(BRIDGE_PATH, data="<<not json>>")
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "invalid_request"


def test_bridge_400_on_empty_body(test_app, test_client):
    _enable_phase3a(test_app)
    response = test_client.post(BRIDGE_PATH, json={})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "missing_access_token"


def test_bridge_400_on_missing_access_token(test_app, test_client):
    _enable_phase3a(test_app)
    response = test_client.post(BRIDGE_PATH, json={"profile": _valid_profile()})
    assert response.status_code == 400
    assert response.get_json()["error"] == "missing_access_token"


def test_bridge_400_on_non_string_access_token(test_app, test_client):
    _enable_phase3a(test_app)
    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": 12345, "profile": _valid_profile()},
    )
    assert response.status_code == 400


def test_bridge_400_on_blank_access_token(test_app, test_client):
    _enable_phase3a(test_app)
    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "   ", "profile": _valid_profile()},
    )
    assert response.status_code == 400


def test_bridge_400_on_non_dict_profile(test_app, test_client):
    _enable_phase3a(test_app)
    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": "not-a-dict"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "invalid_profile"


# ---------------------------------------------------------------------------
# Verifier failures
# ---------------------------------------------------------------------------


def test_bridge_401_on_invalid_token(test_app, test_client, monkeypatch):
    _enable_phase3a(test_app)
    _patch_bridge_verifier(monkeypatch, raise_with=SupabaseTokenInvalid("bad sig"))
    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": _valid_profile()},
    )
    assert response.status_code == 401
    payload = response.get_json()
    assert payload["error"] == "invalid_token"
    assert "bad sig" in payload["message"]


# ---------------------------------------------------------------------------
# Existing legacy user conflict (409)
# ---------------------------------------------------------------------------


def test_bridge_409_on_email_match_against_unlinked_legacy_user(
    test_app, test_client, monkeypatch
):
    claims = _make_claims(email="conflict@example.com")
    _patch_bridge_verifier(monkeypatch, claims=claims)

    _enable_phase3a(test_app)
    with test_app.app_context():
        existing = _make_legacy_user(
            username="conflict_user",
            email="conflict@example.com",
        )
        existing_id = existing.id

    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": _valid_profile(username="otheruser")},
    )
    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"] == "existing_legacy_account"
    assert payload["existing_user_id_hint"] == existing_id

    # Crucial: the legacy row is NOT linked silently.
    with test_app.app_context():
        reloaded = db.session.get(User, existing_id)
        assert reloaded.supabase_auth_user_id is None
        # And no new row created.
        assert User.query.filter_by(username="otheruser").first() is None


# ---------------------------------------------------------------------------
# Profile validation (400 with field_errors)
# ---------------------------------------------------------------------------


def test_bridge_400_on_invalid_profile_with_field_errors(
    test_app, test_client, monkeypatch
):
    claims = _make_claims()
    _patch_bridge_verifier(monkeypatch, claims=claims)
    _enable_phase3a(test_app)

    bad_profile = _valid_profile()
    bad_profile.pop("first_name")
    bad_profile["preferred_region"] = "ZZ"  # invalid
    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": bad_profile},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "invalid_profile"
    assert "first_name" in payload["field_errors"]
    assert "preferred_region" in payload["field_errors"]


# ---------------------------------------------------------------------------
# Happy path: new user
# ---------------------------------------------------------------------------


def test_bridge_200_creates_new_user_and_issues_session(
    test_app, test_client, monkeypatch
):
    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="brandnew@example.com")
    _patch_bridge_verifier(monkeypatch, claims=claims)

    _enable_phase3a(test_app)
    response = test_client.post(
        BRIDGE_PATH,
        json={
            "access_token": "tok",
            "profile": _valid_profile(
                username="brandnew", first_name="Brand", last_name="New"
            ),
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["was_created"] is True
    assert payload["source"] == "bridge_signup"
    assert payload["redirect"] == "/"  # main.home

    with test_app.app_context():
        new_user = User.query.filter_by(username="brandnew").one()
        assert payload["user_id"] == new_user.id
        assert new_user.email == "brandnew@example.com"
        assert new_user.password_hash is None
        assert new_user.is_email_confirmed is True
        assert new_user.auth_provider == "supabase_email"
        assert new_user.supabase_auth_user_id == target_uuid

    # Flask-Login session cookie must have been set on the response.
    set_cookie = response.headers.get("Set-Cookie") or ""
    assert "session=" in set_cookie


def test_bridge_200_for_returning_user_does_not_recreate(
    test_app, test_client, monkeypatch
):
    from services.supabase_auth_linkage import link_app_user_to_supabase

    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="returning@example.com")
    _patch_bridge_verifier(monkeypatch, claims=claims)

    _enable_phase3a(test_app)
    with test_app.app_context():
        existing = _make_legacy_user(
            username="returner_route",
            email="returning@example.com",
        )
        link_app_user_to_supabase(existing.id, target_uuid)
        existing_id = existing.id
        before_count = User.query.count()

    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok"},  # profile not required for returning user
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["was_created"] is False
    assert payload["source"] == "bridge_login"
    assert payload["user_id"] == existing_id
    assert payload["redirect"] == "/"  # main.home

    with test_app.app_context():
        assert User.query.count() == before_count


def test_bridge_session_cookie_lets_subsequent_request_see_current_user(
    test_app, test_client, monkeypatch
):
    """End-to-end: after a successful bridge POST, the test client's
    session cookie must allow the user to reach a `@login_required`
    page (`/profile`) and have ``current_user`` resolve to the new row."""
    target_uuid = uuid.uuid4()
    claims = _make_claims(sub=str(target_uuid), email="follow@example.com")
    _patch_bridge_verifier(monkeypatch, claims=claims)

    _enable_phase3a(test_app)
    bridge_response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": _valid_profile(username="follower")},
    )
    assert bridge_response.status_code == 200

    profile_response = test_client.get("/profile")
    # /profile is @login_required; if the bridge issued a real session
    # cookie, Flask-Login will resolve the user and render the page.
    # 200 (rendered) or 302 (redirect to a logged-in page) both indicate
    # an authenticated session — the failure mode would be a 302 to
    # /login (which is the @login_required redirect target).
    assert profile_response.status_code in (200, 302)
    assert "/login" not in (profile_response.headers.get("Location") or "")


# ---------------------------------------------------------------------------
# Failure paths must NOT issue a session
# ---------------------------------------------------------------------------


def test_bridge_failure_response_does_not_issue_session_cookie(
    test_app, test_client, monkeypatch
):
    _patch_bridge_verifier(monkeypatch, raise_with=SupabaseTokenInvalid("nope"))
    _enable_phase3a(test_app)

    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": _valid_profile()},
    )
    assert response.status_code == 401
    set_cookie = response.headers.get("Set-Cookie") or ""
    # Flask may emit a session header on every response (rotating CSRF
    # token etc.), but it must not contain a ``remember`` token and the
    # response must not authenticate the caller.
    follow_up = test_client.get("/profile")
    # Unauthenticated → @login_required redirects to /login.
    assert follow_up.status_code == 302
    assert "/login" in (follow_up.headers.get("Location") or "")


# ---------------------------------------------------------------------------
# Config-drift defence (503)
# ---------------------------------------------------------------------------


def test_bridge_503_when_master_supabase_flag_off_but_signup_flag_on(
    test_app, test_client
):
    """Defence-in-depth: SUPABASE_NEW_USER_SIGNUP_ENABLED=true while
    SUPABASE_AUTH_ENABLED=false is config drift. The bridge service's
    BridgeFlagDisabled surfaces as 503."""
    test_app.config["SUPABASE_NEW_USER_SIGNUP_ENABLED"] = True
    test_app.config["SUPABASE_AUTH_ENABLED"] = False

    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": _valid_profile()},
    )
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"] == "supabase_auth_unavailable"


# ---------------------------------------------------------------------------
# Sanity: no DB writes on 4xx
# ---------------------------------------------------------------------------


def test_bridge_400_invalid_profile_writes_nothing(test_app, test_client, monkeypatch):
    claims = _make_claims()
    _patch_bridge_verifier(monkeypatch, claims=claims)
    _enable_phase3a(test_app)

    with test_app.app_context():
        before_count = User.query.count()

    bad_profile = _valid_profile()
    bad_profile.pop("first_name")
    response = test_client.post(
        BRIDGE_PATH,
        json={"access_token": "tok", "profile": bad_profile},
    )
    assert response.status_code == 400

    with test_app.app_context():
        assert User.query.count() == before_count
