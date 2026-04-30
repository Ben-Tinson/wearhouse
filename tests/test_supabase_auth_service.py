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


def test_verify_raises_misconfigured_when_hs256_secret_missing(test_app):
    """An HS256 token presented while SUPABASE_JWT_SECRET is unset must be
    refused with ``SupabaseAuthMisconfigured`` — the verifier reaches the
    secret-resolution step before signature verification."""
    with test_app.app_context():
        test_app.config["SUPABASE_AUTH_ENABLED"] = True
        test_app.config["SUPABASE_JWT_SECRET"] = None
        # Real HS256 token — value of secret used to sign is irrelevant
        # because the verifier should fail at the missing-secret check.
        token = jwt.encode(
            {"sub": "x", "exp": int(time.time()) + 60},
            "any-string",
            algorithm="HS256",
        )
        with pytest.raises(SupabaseAuthMisconfigured):
            verify_access_token(token)


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


def test_verify_rejects_unsupported_algorithm(test_app):
    """A token whose ``alg`` is outside the allowlist must be refused.

    The accepted algorithms are HS256 (legacy Supabase shared-secret),
    ES256, and RS256 (Supabase asymmetric signing keys). HS512 is in
    neither set, so the verifier rejects the token before signature
    verification — defending against alg-confusion attacks.
    """
    with test_app.app_context():
        _enable_supabase(test_app)
        token = jwt.encode(
            {"sub": "u", "exp": int(time.time()) + 60},
            SECRET,
            algorithm="HS512",
        )
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)


# ---------------------------------------------------------------------------
# Asymmetric verification (ES256 via JWKS)
# ---------------------------------------------------------------------------


class _FakeJWK:
    """Stand-in for ``jwt.PyJWK`` returned by a mocked JWKS client."""

    def __init__(self, key) -> None:
        self.key = key


class _FakeJWKSClient:
    """Returns a fixed signing key for any token; records lookups."""

    def __init__(self, key) -> None:
        self._key = key
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        return _FakeJWK(self._key)


def _generate_es256_keypair():
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def _make_es256_token(private_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "es256-user-uuid",
        "email": "es256@example.com",
        "iat": now,
        "exp": now + 60,
        "aud": "authenticated",
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        private_key,
        algorithm="ES256",
        headers={"kid": "test-kid", "alg": "ES256"},
    )


def test_verify_accepts_valid_es256_token_via_jwks(test_app, monkeypatch):
    """Happy path for the staging-rehearsal JWT shape (alg=ES256, JWKS-keyed)."""
    private_key, public_key = _generate_es256_keypair()
    fake_client = _FakeJWKSClient(public_key)
    monkeypatch.setattr(
        "services.supabase_auth_service._get_jwks_client",
        lambda url: fake_client,
    )

    with test_app.app_context():
        _enable_supabase(test_app)
        test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
        token = _make_es256_token(private_key)
        claims = verify_access_token(token)
        assert claims.supabase_user_id == "es256-user-uuid"
        assert claims.email == "es256@example.com"
        assert claims.raw["aud"] == "authenticated"
        assert fake_client.calls == 1


def test_verify_rejects_es256_signed_with_wrong_private_key(test_app, monkeypatch):
    """A valid ES256 token signed by an unrelated key must not verify."""
    server_private, server_public = _generate_es256_keypair()
    attacker_private, _ = _generate_es256_keypair()
    monkeypatch.setattr(
        "services.supabase_auth_service._get_jwks_client",
        lambda url: _FakeJWKSClient(server_public),
    )

    with test_app.app_context():
        _enable_supabase(test_app)
        test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
        # Sign with the attacker's key — should fail signature verification
        # against the server's published public key.
        token = _make_es256_token(attacker_private)
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)


def test_verify_raises_misconfigured_when_es256_url_missing(test_app):
    """ES256 verification needs SUPABASE_URL to know where JWKS lives."""
    private_key, _ = _generate_es256_keypair()

    with test_app.app_context():
        test_app.config["SUPABASE_AUTH_ENABLED"] = True
        test_app.config["SUPABASE_URL"] = None
        token = _make_es256_token(private_key)
        with pytest.raises(SupabaseAuthMisconfigured):
            verify_access_token(token)


def test_verify_raises_invalid_when_jwks_lookup_fails(test_app, monkeypatch):
    """A JWKS network / lookup failure surfaces as ``SupabaseTokenInvalid``.

    The caller doesn't need to distinguish "JWKS is down" from "the kid is
    unknown"; either way, the token cannot be verified and must be rejected.
    """
    private_key, _ = _generate_es256_keypair()

    class _BoomClient:
        def get_signing_key_from_jwt(self, token):
            raise RuntimeError("simulated JWKS endpoint unavailable")

    monkeypatch.setattr(
        "services.supabase_auth_service._get_jwks_client",
        lambda url: _BoomClient(),
    )

    with test_app.app_context():
        _enable_supabase(test_app)
        test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
        token = _make_es256_token(private_key)
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)


def test_verify_rejects_expired_es256_token(test_app, monkeypatch):
    private_key, public_key = _generate_es256_keypair()
    monkeypatch.setattr(
        "services.supabase_auth_service._get_jwks_client",
        lambda url: _FakeJWKSClient(public_key),
    )

    with test_app.app_context():
        _enable_supabase(test_app)
        test_app.config["SUPABASE_URL"] = "https://example.supabase.co"
        now = int(time.time())
        token = jwt.encode(
            {"sub": "u", "exp": now - 60, "iat": now - 120},
            private_key,
            algorithm="ES256",
            headers={"kid": "test-kid"},
        )
        with pytest.raises(SupabaseTokenInvalid):
            verify_access_token(token)
