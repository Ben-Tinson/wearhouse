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
# JWT-shaped bearer values: format-disambiguation policy
# ---------------------------------------------------------------------------


def test_jwt_shaped_bearer_for_unlinked_identity_returns_401(test_app, test_client):
    """Verified JWT but no linked app user → 401.

    Format-disambiguation routes the JWT-shaped value to the Supabase
    branch (because the flag is on). Verification succeeds, but no
    ``user.supabase_auth_user_id`` matches the ``sub`` claim, so the
    decorator must reject — never auto-link.
    """
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert response.status_code == 401


def test_jwt_shaped_bearer_with_flag_off_returns_401(test_app, test_client):
    """JWT-shaped bearer with the flag off must be rejected outright.

    This protects against accidental enablement: even if a JWT happens to
    be presented, the decorator refuses to verify it while the flag is
    off, returning 401 rather than falling through to the
    ``UserApiToken`` lookup (which would also 401, but for less obvious
    reasons).
    """
    test_app.config["SUPABASE_AUTH_ENABLED"] = False
    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert response.status_code == 401


def test_jwt_shaped_bearer_for_linked_user_authorises_when_flag_on(test_app, test_client):
    """Verified JWT for a linked app user → 200 on a step-write endpoint.

    This locks in the new positive case: format-disambiguation routes
    the JWT to the Supabase branch, the JWT verifies, the linked user
    is resolved by ``supabase_auth_user_id``, and the request is
    authorised as session-equivalent (no scope check, no DB writes for
    auth bookkeeping).
    """
    import uuid as _uuid

    from services.supabase_auth_linkage import link_app_user_to_supabase

    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET

    target_uuid = _uuid.uuid4()
    with test_app.app_context():
        user = User(
            username="jwt_authorised",
            email="jwt_authorised@example.com",
            first_name="JWT",
            last_name="User",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        link_app_user_to_supabase(user.id, target_uuid)
        user_id = user.id

    token = _make_jwt(secret=JWT_SECRET)
    # Re-issue with our linked sub
    import jwt as _jwt
    now = int(time.time())
    token = _jwt.encode(
        {
            "sub": str(target_uuid),
            "email": "jwt_authorised@example.com",
            "iat": now,
            "exp": now + 60,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    # The JWT branch must NOT have created any UserApiToken row for this user.
    with test_app.app_context():
        assert UserApiToken.query.filter_by(user_id=user_id).count() == 0


def test_jwt_shaped_bearer_for_linked_user_still_blocked_when_flag_off(test_app, test_client):
    """Even for a linked user, flag-off JWT must be rejected."""
    import uuid as _uuid

    from services.supabase_auth_linkage import link_app_user_to_supabase

    test_app.config["SUPABASE_AUTH_ENABLED"] = False

    target_uuid = _uuid.uuid4()
    with test_app.app_context():
        user = User(
            username="off_jwt_user",
            email="off_jwt@example.com",
            first_name="Off",
            last_name="JWT",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        link_app_user_to_supabase(user.id, target_uuid)

    import jwt as _jwt
    now = int(time.time())
    token = _jwt.encode(
        {
            "sub": str(target_uuid),
            "email": "off_jwt@example.com",
            "iat": now,
            "exp": now + 60,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_malformed_jwt_with_flag_on_returns_401(test_app, test_client):
    """A JWT-shaped value that fails verification must be rejected cleanly."""
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET

    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": "Bearer malformed.jwt.value"},
    )
    assert response.status_code == 401


def test_jwt_branch_does_not_write_last_used_at(test_app, test_client):
    """The Supabase JWT branch must not perform DB writes (no last_used_at).

    This locks in the accepted-decision rule that the JWT branch is pure
    verification — no compounding of the decorator's existing implicit-
    commit footprint.
    """
    import uuid as _uuid
    import jwt as _jwt

    from services.supabase_auth_linkage import link_app_user_to_supabase

    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = JWT_SECRET

    target_uuid = _uuid.uuid4()
    with test_app.app_context():
        user = User(
            username="no_writes_user",
            email="no_writes@example.com",
            first_name="No",
            last_name="Writes",
            is_email_confirmed=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        link_app_user_to_supabase(user.id, target_uuid)
        user_id = user.id
        # Capture the supabase_auth_user_id we just wrote (the only
        # legitimate write); it must be unchanged after a JWT request.
        original_link = user.supabase_auth_user_id

    now = int(time.time())
    token = _jwt.encode(
        {
            "sub": str(target_uuid),
            "email": "no_writes@example.com",
            "iat": now,
            "exp": now + 60,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    response = test_client.post(
        STEPS_ENDPOINT,
        json=_valid_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    # No UserApiToken created. supabase_auth_user_id unchanged.
    with test_app.app_context():
        reloaded = db.session.get(User, user_id)
        assert reloaded.supabase_auth_user_id == original_link
        assert UserApiToken.query.filter_by(user_id=user_id).count() == 0
