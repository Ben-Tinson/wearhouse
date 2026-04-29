"""Bearer-token regression tests for ``@bearer_or_login_required``.

Phase 2 ships the Supabase Auth foundation but **does not** modify
``decorators.bearer_or_login_required``. These tests pin the existing
``UserApiToken`` behaviour so that:

  - any future change (e.g. the planned format-disambiguation policy)
    has a regression net,
  - mobile / API token callers cannot be silently broken by Phase 2.

Per the accepted bearer-collision policy (see ``docs/DECISIONS.md``),
the ``UserApiToken`` opaque-bearer path must remain byte-for-byte
unchanged regardless of ``SUPABASE_AUTH_ENABLED``.
"""

from __future__ import annotations

import time

import jwt

from extensions import db
from models import User, UserApiToken
from services.api_tokens import create_token_for_user


STEPS_ENDPOINT = "/api/steps/buckets"
JWT_SECRET = "phase2-bearer-regression-secret"


def _make_user_with_token(scopes: str = "steps:write"):
    user = User(
        username="bearer_regression_user",
        email="bearer_regression@example.com",
        first_name="Bearer",
        last_name="User",
        is_email_confirmed=True,
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    token, plaintext = create_token_for_user(user, name="regression", scopes=scopes)
    return user, token, plaintext


def _valid_payload():
    return {
        "source": "apple_health",
        "timezone": "UTC",
        "granularity": "day",
        "buckets": [
            {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
                "steps": 100,
            }
        ],
    }


def _make_jwt(secret: str = JWT_SECRET) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "email": "linked@example.com",
            "iat": now,
            "exp": now + 60,
        },
        secret,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Flag-off baseline (must equal pre-Phase-2 behaviour)
# ---------------------------------------------------------------------------


def test_user_api_token_works_with_flag_off(test_app, test_client):
    """The opaque ``UserApiToken`` path must succeed with the flag off."""
    test_app.config["SUPABASE_AUTH_ENABLED"] = False
    with test_app.app_context():
        _user, _token, plaintext = _make_user_with_token()

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 200


def test_revoked_user_api_token_rejected_with_flag_off(test_app, test_client):
    test_app.config["SUPABASE_AUTH_ENABLED"] = False
    with test_app.app_context():
        _user, token, plaintext = _make_user_with_token()
        from datetime import datetime
        token_id = token.id
        db.session.get(UserApiToken, token_id).revoked_at = datetime.utcnow()
        db.session.commit()

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 401


def test_no_authorization_header_rejected_with_flag_off(test_app, test_client):
    test_app.config["SUPABASE_AUTH_ENABLED"] = False
    response = test_client.post(STEPS_ENDPOINT, json=_valid_payload())
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Flag-on: UserApiToken path must remain unchanged
# ---------------------------------------------------------------------------


def test_user_api_token_works_with_flag_on(test_app, test_client):
    """Flipping the Supabase flag must NOT regress the existing token path.

    This is the central guarantee of the bearer-collision policy: an
    opaque ``UserApiToken`` (zero ``.`` characters) is never confused
    with a Supabase JWT.
    """
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET
    with test_app.app_context():
        _user, _token, plaintext = _make_user_with_token()

    # Sanity: a real ``UserApiToken`` plaintext contains no '.' characters.
    assert "." not in plaintext

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 200


def test_revoked_user_api_token_rejected_with_flag_on(test_app, test_client):
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET
    with test_app.app_context():
        _user, token, plaintext = _make_user_with_token()
        from datetime import datetime
        token_id = token.id
        db.session.get(UserApiToken, token_id).revoked_at = datetime.utcnow()
        db.session.commit()

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# JWT-shaped bearer values: the decorator does NOT yet honour them
# ---------------------------------------------------------------------------


def test_jwt_shaped_bearer_does_not_authorise_today(test_app, test_client):
    """A JWT-shaped bearer must not magically authorise the request.

    Phase 2 introduces JWT *capability* via the resolver, but
    ``@bearer_or_login_required`` is unchanged in this slice. A JWT in
    the header should fall through to the ``UserApiToken`` lookup, find
    nothing matching its SHA-256 hash, and produce a 401 — exactly the
    same outcome as in Phase 1, regardless of the flag state.
    """
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert response.status_code == 401


def test_jwt_shaped_bearer_with_flag_off_also_401(test_app, test_client):
    test_app.config["SUPABASE_AUTH_ENABLED"] = False
    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert response.status_code == 401
