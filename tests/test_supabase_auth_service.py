"""Tests for ``services/supabase_auth_service.py``.

Phase 2 foundation. The verifier is a pure helper today (not consumed by
any live request path), so these tests pin its contract directly.
"""

from __future__ import annotations

import time

import jwt
import pytest

from services.supabase_auth_service import (
    SupabaseAuthDisabled,
    SupabaseAuthMisconfigured,
    SupabaseTokenInvalid,
    is_enabled,
    looks_like_jwt,
    verify_access_token,
)


SECRET = "phase2-test-secret-do-not-use-in-prod"


def _enable_supabase(test_app, secret: str = SECRET):
    test_app.config["SUPABASE_AUTH_ENABLED"] = True
    test_app.config["SUPABASE_JWT_SECRET"] = secret


def _disable_supabase(test_app):
    test_app.config["SUPABASE_AUTH_ENABLED"] = False


def _make_token(secret: str = SECRET, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "email": "linked@example.com",
        "iat": now,
        "exp": now + 60,
        "aud": "authenticated",
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# looks_like_jwt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, False),
        ("", False),
        ("opaque-token-no-dots", False),
        ("a.b", False),
        ("a.b.c", True),
        ("header.payload.signature", True),
        ("a.b.c.d", False),
    ],
)
def test_looks_like_jwt_disambiguator(value, expected):
    assert looks_like_jwt(value) is expected


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


def test_is_enabled_default_false(test_app):
    with test_app.app_context():
        assert is_enabled() is False


def test_is_enabled_when_flag_set(test_app):
    with test_app.app_context():
        _enable_supabase(test_app)
        assert is_enabled() is True


def test_is_enabled_outside_request_returns_false():
    # No app context active → must return False, never raise.
    assert is_enabled() is False


# ---------------------------------------------------------------------------
# verify_access_token
# ---------------------------------------------------------------------------


def test_verify_raises_disabled_when_flag_off(test_app):
    with test_app.app_context():
        _disable_supabase(test_app)
        with pytest.raises(SupabaseAuthDisabled):
            verify_access_token("a.b.c")


def test_verify_raises_misconfigured_when_secret_missing(test_app):
    with test_app.app_context():
        test_app.config["SUPABASE_AUTH_ENABLED"] = True
        test_app.config["SUPABASE_JWT_SECRET"] = None
        with pytest.raises(SupabaseAuthMisconfigured):
            verify_access_token("a.b.c")


def test_verify_rejects_non_jwt_shape(test_app):
    with test_app.app_context():
        _enable_supabase(test_app)
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token("opaque-no-dots")
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token("only.two-segments")


def test_verify_accepts_valid_token(test_app):
    with test_app.app_context():
        _enable_supabase(test_app)
        token = _make_token()
        claims = verify_access_token(token)
        assert claims.supabase_user_id == "11111111-1111-1111-1111-111111111111"
        assert claims.email == "linked@example.com"
        assert claims.raw["aud"] == "authenticated"


def test_verify_rejects_wrong_secret(test_app):
    with test_app.app_context():
        _enable_supabase(test_app, secret="server-secret")
        token = _make_token(secret="attacker-secret")
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)


def test_verify_rejects_expired_token(test_app):
    with test_app.app_context():
        _enable_supabase(test_app)
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": "u",
                "email": "x@example.com",
                "iat": now - 120,
                "exp": now - 60,
            },
            SECRET,
            algorithm="HS256",
        )
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)


def test_verify_rejects_token_missing_sub(test_app):
    with test_app.app_context():
        _enable_supabase(test_app)
        now = int(time.time())
        # PyJWT's ``options={"require": ["sub"]}`` raises before we inspect
        # the claim ourselves; either way the error type is the same.
        token = jwt.encode(
            {"email": "x@example.com", "iat": now, "exp": now + 60},
            SECRET,
            algorithm="HS256",
        )
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)


def test_verify_normalises_missing_email_to_none(test_app):
    with test_app.app_context():
        _enable_supabase(test_app)
        token = _make_token(email=None)
        claims = verify_access_token(token)
        assert claims.email is None


def test_verify_rejects_wrong_algorithm(test_app):
    """A token signed with a non-HS256 algorithm must not verify."""
    with test_app.app_context():
        _enable_supabase(test_app)
        # Encode an HS512 token with the same secret. PyJWT's verify will
        # reject it because we restrict ``algorithms`` to HS256.
        token = jwt.encode(
            {"sub": "u", "exp": int(time.time()) + 60},
            SECRET,
            algorithm="HS512",
        )
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)
